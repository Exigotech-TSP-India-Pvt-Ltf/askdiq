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


async def require_postgres(engine: AsyncEngine) -> None:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"Postgres not reachable locally: {type(exc).__name__}")
