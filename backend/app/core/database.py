from collections.abc import AsyncGenerator

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    # Validate on checkout (stale sockets behind nginx / cloud LBs) and recycle.
    pool_pre_ping=settings.db_pool_pre_ping,
    pool_recycle=settings.db_pool_recycle_seconds,
)

# Postgres SQLSTATE for "canceling statement due to statement timeout".
QUERY_CANCELED_SQLSTATE = "57014"

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a DB session per request."""
    async with AsyncSessionLocal() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Dependency: the factory ``gather_scoped`` opens one session per task from.

    A dependency (not a module global) so tests can route parallel
    aggregation through their transactional session.
    """
    return AsyncSessionLocal


def is_statement_timeout(exc: BaseException) -> bool:
    """True when ``exc`` is Postgres cancelling a statement (statement_timeout)."""
    if not isinstance(exc, DBAPIError):
        return False
    return getattr(exc.orig, "sqlstate", None) == QUERY_CANCELED_SQLSTATE


async def read_only_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for heavy read paths (dashboards): bounded statement time.

    ``SET LOCAL statement_timeout`` lasts for the request's transaction; a
    query that exceeds ``DASHBOARD_STATEMENT_TIMEOUT_MS`` is cancelled by
    Postgres and surfaces as a clean 503 instead of a hung worker.
    """
    timeout_ms = int(settings.dashboard_statement_timeout_ms)
    async with AsyncSessionLocal() as session:
        # Integer from typed settings; SET cannot take bind parameters.
        await session.execute(
            text(f"SET LOCAL statement_timeout = {timeout_ms}")  # nosec B608
        )
        try:
            yield session
        except DBAPIError as exc:
            if is_statement_timeout(exc):
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="The query took too long. Please retry or narrow the range.",
                    headers={"Retry-After": "5"},
                ) from None
            raise


async def init_db() -> None:
    """Ensure the pgvector extension is enabled."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
