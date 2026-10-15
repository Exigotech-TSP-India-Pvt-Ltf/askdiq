"""FastAPI application entrypoint."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    admin,
    documents,
    governance,
    health,
    ingest,
    metrics,
    query,
    sessions,
)
from app.core.concurrency import configure_thread_pool
from app.core.config import configure_logging, get_settings
from app.core.database import init_db
from app.core.middleware import LatencyMiddleware
from app.harness.prompts import call_llm
from app.retrieval.embeddings import embed_text
from app.retrieval.reranker import preload_reranker

configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()


async def _startup_azure_health_check() -> None:
    """One real embedding call + one real LLM call, logged so it's
    unambiguous at boot whether Azure OpenAI is actually reachable.
    Never blocks startup — failures are logged, not raised.
    """
    try:
        await embed_text("DeployIQ startup health check")
        logger.info("startup_health_check embedding=ok")
    except Exception as exc:
        logger.error("startup_health_check embedding=failed error=%s", exc)

    try:
        answer, _usage = await call_llm(
            "Reply with the single word: ok.",
            ["DeployIQ is a security and compliance platform."],
        )
        logger.info(
            "startup_health_check llm=%s",
            "ok" if answer.strip() else "empty",
        )
    except Exception as exc:
        logger.error("startup_health_check llm=failed error=%s", exc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.env == "production" and not settings.trust_proxy_headers:
        logger.warning(
            "production_startup_warning: trust_proxy_headers is False while env=production. "
            "Client IP attribution and per-IP rate limiting may be inaccurate behind reverse proxies."
        )
    # Initializes pgvector extension and creates all missing database tables.
    await init_db()
    configure_thread_pool()
    await _startup_azure_health_check()
    await asyncio.to_thread(preload_reranker)
    yield


is_prod = settings.env == "production"
app = FastAPI(
    title=f"{settings.brand_name} RAG Backend",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if is_prod else "/docs",
    redoc_url=None if is_prod else "/redoc",
    openapi_url=None if is_prod else "/openapi.json",
)

# Allow the Next.js dev server / deployed frontend to call these APIs directly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
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
app.include_router(admin.router, tags=["admin"])


@app.get("/branding", tags=["branding"])
async def get_branding() -> dict[str, str]:
    """Public unauthenticated branding details for frontend runtime customization."""
    cfg = get_settings()
    return {
        "brand_name": cfg.brand_name,
        "brand_assistant_name": cfg.brand_assistant_name,
        "brand_support_email": cfg.brand_support_email,
    }
