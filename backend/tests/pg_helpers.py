"""Helpers for tests that need a real Postgres (they run in CI).

Locally, without a reachable database, these tests are skipped; in CI
(``CI=true``) a connection failure is a hard error so nothing is silently
skipped there.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


def new_engine(**kwargs: object) -> AsyncEngine:
    """A separate engine = a separate "app instance" for multi-instance tests."""
    if "pool_size" not in kwargs:
        kwargs.setdefault("poolclass", NullPool)
    return create_async_engine(get_settings().database_url, **kwargs)


MIGRATION_OPT_IN = "ALLOW_MIGRATION_TESTS"


def require_disposable_database() -> None:
    """Skip unless the database may be migrated down.

    Migration round trips run ``alembic downgrade`` on DATABASE_URL. In CI
    that is a throwaway database; locally it is the shared dev database, and
    a downgrade drops columns under every running server. Locally they run
    only with ``ALLOW_MIGRATION_TESTS=1`` against a disposable DATABASE_URL.
    """
    if os.environ.get("CI") == "true" or os.environ.get(MIGRATION_OPT_IN) == "1":
        return
    pytest.skip(f"migration round trip needs CI or {MIGRATION_OPT_IN}=1")


async def require_postgres(engine: AsyncEngine) -> None:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"Postgres not reachable locally: {type(exc).__name__}")
