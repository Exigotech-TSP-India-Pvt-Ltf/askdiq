"""Per-request latency tracking.

Wraps every request in a timer, tags it with a request-scoped stage timer
that other layers (retrieval, harness, guardrails) can write into via
`request.state.stage_timings`, and persists the total + per-stage timings
so `/metrics/latency` can compute P50/P70/P100 later.
"""
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.database import AsyncSessionLocal
from app.models.db_models import LatencyRecord


class LatencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        request.state.stage_timings = {}  # populated by downstream layers

        start = time.perf_counter()
        response = await call_next(request)
        total_ms = (time.perf_counter() - start) * 1000

        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Latency-MS"] = f"{total_ms:.2f}"

        # Only persist for the query endpoint — that's the one the latency
        # target and P50/P70/P100 requirement actually apply to.
        if request.url.path == "/query" and request.method == "POST":
            await self._persist(request, total_ms)

        return response

    @staticmethod
    async def _persist(request: Request, total_ms: float) -> None:
        stages = request.state.stage_timings
        async with AsyncSessionLocal() as session:
            session.add(
                LatencyRecord(
                    request_id=request.state.request_id,
                    total_ms=total_ms,
                    embedding_ms=stages.get("embedding"),
                    retrieval_ms=stages.get("retrieval"),
                    guardrail_pre_ms=stages.get("guardrail_pre"),
                    llm_ms=stages.get("llm"),
                    guardrail_post_ms=stages.get("guardrail_post"),
                )
            )
            await session.commit()
