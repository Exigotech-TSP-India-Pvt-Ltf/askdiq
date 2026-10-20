"""FastAPI application entrypoint."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    admin,
    alerts,
    auth,
    branding,
    console,
    documents,
    governance,
    health,
    ingest,
    insights,
    metrics,
    model_settings,
    query,
    sessions,
    user_management,
)
from app.auth.mock_idp import mount_mock_idp
from app.core.compression import SSEAwareGZipMiddleware
from app.core.concurrency import configure_thread_pool
from app.core.config import Settings, configure_logging, get_settings
from app.core.database import get_session_factory, init_db
from app.core.exceptions import register_exception_handlers
from app.core.middleware import LatencyMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
from app.core.tasks import drain as drain_background_tasks
from app.governance.stream import get_governance_hub
from app.harness.prompts import call_llm
from app.jobs.runtime import start_platform_services
from app.model_settings.service import refresh_model_settings
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
    # Settings > Models overrides, before the first Azure call uses them.
    await refresh_model_settings(get_session_factory())
    await _startup_azure_health_check()
    await asyncio.to_thread(preload_reranker)
    # Event bus (LISTEN/NOTIFY) and, if JOBS_WORKER_ENABLED, the job worker.
    platform_services = await start_platform_services()
    try:
        yield
    finally:
        # Stop the shared governance SSE producers before the event bus.
        await get_governance_hub().aclose()
        await platform_services.aclose()
        # Let supervised fire-and-forget work (e.g. latency persistence)
        # finish, bounded so shutdown can never hang.
        await drain_background_tasks(settings.task_drain_timeout_seconds)


def api_docs_enabled(cfg: Settings) -> bool:
    """Docs are public outside production; in production only on opt-in."""
    return cfg.env != "production" or cfg.enable_api_docs


def docs_urls(cfg: Settings) -> dict[str, str | None]:
    """FastAPI docs/redoc/openapi URLs, or None (404) when docs are disabled."""
    enabled = api_docs_enabled(cfg)
    return {
        "docs_url": "/docs" if enabled else None,
        "redoc_url": "/redoc" if enabled else None,
        "openapi_url": "/openapi.json" if enabled else None,
    }


app = FastAPI(
    title=f"{settings.brand_name} RAG Backend",
    version="0.1.0",
    lifespan=lifespan,
    **docs_urls(settings),
)

register_exception_handlers(app)

# Allow the Next.js dev server / deployed frontend to call these APIs directly.
# Explicit header/method allow-lists (no wildcards, OWASP A02).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
)

app.add_middleware(LatencyMiddleware)
app.add_middleware(SSEAwareGZipMiddleware, minimum_size=settings.gzip_minimum_size)
# Added last so it is the outermost user middleware: every response,
# including CORS preflights and handled errors, carries the headers.
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(health.router, tags=["health"])
app.include_router(query.router, tags=["query"])
app.include_router(sessions.router)
app.include_router(ingest.router, tags=["ingest"])
app.include_router(documents.router, tags=["documents"])
app.include_router(metrics.router, tags=["metrics"])
app.include_router(governance.router, tags=["governance"])
app.include_router(admin.router, tags=["admin"])
app.include_router(insights.router)
app.include_router(branding.router)
app.include_router(branding.admin_router)
app.include_router(model_settings.router)
app.include_router(auth.router)
app.include_router(user_management.router)
app.include_router(alerts.router)
app.include_router(console.router)
# Local mock Entra IdP, only with AUTH_DEV_MOCK=true (settings refuse it
# outside ENV=development).
mount_mock_idp(app, settings)
