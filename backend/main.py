"""FastAPI application entrypoint."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    documents,
    governance,
    health,
    ingest,
    metrics,
    query,
    sessions,
)
from app.core.config import configure_logging
from app.core.database import init_db
from app.core.middleware import LatencyMiddleware
from app.harness.prompts import call_llm
from app.retrieval.embeddings import embed_text_sync
from app.retrieval.reranker import preload_reranker

configure_logging()
logger = logging.getLogger(__name__)


def _startup_azure_health_check() -> None:
    """One real embedding call + one real LLM call, logged so it's
    unambiguous at boot whether Azure OpenAI is actually reachable (STEP 9).
    Never blocks startup — failures are logged, not raised.
    """
    try:
        embed_text_sync("DeployIQ startup health check")
        logger.info("startup_health_check embedding=ok")
    except Exception as exc:
        logger.error("startup_health_check embedding=failed error=%s", exc)

    try:
        answer = call_llm(
            "Reply with the single word: ok.",
            ["DeployIQ is a security and compliance platform."],
        )
        logger.info("startup_health_check llm=%s", "ok" if answer.strip() else "empty")
    except Exception as exc:
        logger.error("startup_health_check llm=failed error=%s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initializes pgvector extension and creates all missing database tables (users, documents, chunks, etc.)
    await init_db()
    _startup_azure_health_check()
    preload_reranker()
    yield


app = FastAPI(title="Deploy IQ RAG Backend", version="0.1.0", lifespan=lifespan)

# Allow the Next.js dev server / deployed frontend to call these APIs directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # TODO: add production frontend origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(LatencyMiddleware)

app.include_router(health.router, tags=["health"])
app.include_router(query.router, tags=["query"])
app.include_router(sessions.router)
app.include_router(ingest.router, tags=["ingest"])
app.include_router(documents.router, tags=["documents"])
app.include_router(metrics.router, tags=["metrics"])
app.include_router(governance.router, tags=["governance"])
