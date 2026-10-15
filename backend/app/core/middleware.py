"""Per-request latency tracking.

Wraps every request in a timer, tags it with a request-scoped stage timer
that other layers (retrieval, harness, guardrails) can write into via
`request.state.stage_timings`, and persists the total + per-stage timings
so `/metrics/latency` can compute P50/P70/P100 later.
"""

import asyncio
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.database import AsyncSessionLocal
from app.models.db_models import LatencyRecord
from app.retrieval.embeddings import embedding_timing_sink
from app.retrieval.reranker import rerank_timing_sink

logger = logging.getLogger(__name__)

# Strong refs so fire-and-forget persist tasks aren't garbage collected mid-flight.
_background_tasks: set[asyncio.Task] = set()

# Process-local counters feeding the governance monitoring dashboard.
METRICS = {"requests": 0, "errors": 0, "active_queries": 0}


class LatencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        request.state.stage_timings = {}  # populated by downstream layers

        rerank_sink: list[float] = []
        rerank_timing_sink.set(rerank_sink)
        embed_sink: list[float] = []
        embedding_timing_sink.set(embed_sink)
        start = time.perf_counter()
        is_query = request.url.path == "/query" and request.method == "POST"
        METRICS["requests"] += 1
        if is_query:
            METRICS["active_queries"] += 1
        try:
            response = await call_next(request)
        except Exception:
            METRICS["errors"] += 1
            raise
        finally:
            if is_query:
                METRICS["active_queries"] -= 1
        if embed_sink:
            request.state.stage_timings["embedding"] = sum(embed_sink)
        if rerank_sink:
            request.state.stage_timings["reranker"] = sum(rerank_sink)
        if response.status_code >= 500:
            METRICS["errors"] += 1
        total_ms = (time.perf_counter() - start) * 1000

        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Latency-MS"] = f"{total_ms:.2f}"

        # Only persist for the query endpoint — that's the one the latency
        # target and P50/P70/P100 requirement actually apply to.
        if request.url.path == "/query" and request.method == "POST":
            task = asyncio.create_task(
                self._persist(
                    request.state.request_id,
                    dict(request.state.stage_timings),
                    total_ms,
                )
            )
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        return response

    @staticmethod
    async def _persist(request_id: str, stages: dict, total_ms: float) -> None:
        try:
            async with AsyncSessionLocal() as session:
                session.add(
                    LatencyRecord(
                        request_id=request_id,
                        total_ms=total_ms,
                        embedding_ms=stages.get("embedding"),
                        retrieval_ms=stages.get("retrieval"),
                        guardrail_pre_ms=stages.get("guardrail_pre"),
                        llm_ms=stages.get("llm"),
                        guardrail_post_ms=stages.get("guardrail_post"),
                        reranker_ms=stages.get("reranker"),
                    )
                )
                await session.commit()
        except Exception:
            logger.warning(
                "latency_persist_failed request_id=%s", request_id, exc_info=True
            )
