"""DB timeout hardening: migration lock_timeout + retries, API statement cap.

Guards against the "every request hangs for 60 s" incident: an ``alembic
upgrade`` waiting for an ACCESS EXCLUSIVE lock queues every query on the
table behind it, and request sessions had no statement cap.
"""

from __future__ import annotations

import functools
import time
import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import RouteContext
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.core import database
from app.core.concurrency import ScopedRunner, get_scoped_runner
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.migration_guard import (
    LOCK_NOT_AVAILABLE_SQLSTATE,
    apply_migration_timeouts,
    backoff_seconds,
    is_lock_timeout,
    run_with_lock_retries,
)
from tests.pg_helpers import new_engine, require_postgres
from tests.route_helpers import flat_routes, is_api_route

settings = get_settings()

API_TIMEOUT_MS = 200
FAST_FAIL_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Migration guard (unit)
# ---------------------------------------------------------------------------


class _FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.commits = 0

    def execute(self, statement: object) -> None:
        self.statements.append(str(statement))

    def commit(self) -> None:
        self.commits += 1


class _PgError(Exception):
    def __init__(self, pgcode: str) -> None:
        super().__init__(pgcode)
        self.pgcode = pgcode


def _lock_error() -> DBAPIError:
    return OperationalError("ALTER TABLE documents ...", {}, _PgError("55P03"))


def test_migration_connection_gets_lock_and_statement_timeouts() -> None:
    conn = _FakeConnection()
    apply_migration_timeouts(conn, lock_timeout_ms=5000, statement_timeout_ms=0)
    assert conn.statements == ["SET lock_timeout = 5000", "SET statement_timeout = 0"]
    assert conn.commits == 1, "committed so the session SETs outlive the txn"


def test_is_lock_timeout_matches_only_55p03() -> None:
    assert LOCK_NOT_AVAILABLE_SQLSTATE == "55P03"
    assert is_lock_timeout(_lock_error())
    wrapped = RuntimeError("alembic failed")
    wrapped.__cause__ = _lock_error()
    assert is_lock_timeout(wrapped)
    assert not is_lock_timeout(OperationalError("x", {}, _PgError("57014")))
    assert not is_lock_timeout(ValueError("boom"))


def test_lock_timeout_is_retried_with_backoff_then_succeeds() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def upgrade() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise _lock_error()
        return "done"

    result = run_with_lock_retries(
        upgrade, retries=5, base_backoff_seconds=2.0, sleep=sleeps.append
    )
    assert result == "done"
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]


def test_lock_timeout_gives_up_after_retries() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def upgrade() -> None:
        calls.append(1)
        raise _lock_error()

    with pytest.raises(OperationalError):
        run_with_lock_retries(
            upgrade, retries=2, base_backoff_seconds=1.0, sleep=sleeps.append
        )
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


def test_other_migration_errors_are_not_retried() -> None:
    calls: list[int] = []

    def upgrade() -> None:
        calls.append(1)
        raise ValueError("bad migration")

    with pytest.raises(ValueError, match="bad migration"):
        run_with_lock_retries(upgrade, retries=5, base_backoff_seconds=0)
    assert len(calls) == 1


def test_backoff_is_capped() -> None:
    assert backoff_seconds(1, 2.0) == 2.0
    assert backoff_seconds(3, 2.0) == 8.0
    assert backoff_seconds(20, 2.0) == 60.0


# ---------------------------------------------------------------------------
# Migration guard against a real held lock (Postgres; runs in CI)
# ---------------------------------------------------------------------------


@pytest.fixture
async def lock_engine() -> AsyncIterator[AsyncEngine]:
    engine = new_engine()
    await require_postgres(engine)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def locked_table(
    lock_engine: AsyncEngine,
) -> AsyncIterator[tuple[str, AsyncConnection]]:
    """A table another session holds ACCESS SHARE on (blocks any ALTER)."""
    table = f"lock_probe_{uuid.uuid4().hex[:12]}"
    async with lock_engine.begin() as conn:
        await conn.execute(text(f"CREATE TABLE {table} (id int)"))
    holder = await lock_engine.connect()
    await holder.execute(text(f"LOCK TABLE {table} IN ACCESS SHARE MODE"))
    try:
        yield table, holder
    finally:
        await holder.close()
        async with lock_engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))


def _alter_attempt(
    conn: Connection, table: str, on_attempt: Callable[[Connection], None]
) -> None:
    """One migration attempt on ``conn`` (rolled back when it fails)."""
    try:
        apply_migration_timeouts(conn, lock_timeout_ms=200, statement_timeout_ms=0)
        on_attempt(conn)
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN extra int"))
        conn.commit()
    finally:
        conn.rollback()


@pytest.mark.db
async def test_blocked_migration_fails_fast_and_retries(
    lock_engine: AsyncEngine, locked_table: tuple[str, AsyncConnection]
) -> None:
    table, _holder = locked_table
    attempts: list[str] = []
    sleeps: list[float] = []

    def body(conn: Connection) -> None:
        run_with_lock_retries(
            lambda: _alter_attempt(
                conn,
                table,
                lambda c: attempts.append(
                    c.execute(text("SHOW lock_timeout")).scalar_one()
                ),
            ),
            retries=2,
            base_backoff_seconds=0.01,
            sleep=sleeps.append,
        )

    started = time.monotonic()
    async with lock_engine.connect() as conn:
        with pytest.raises(DBAPIError) as info:
            await conn.run_sync(body)
    elapsed = time.monotonic() - started

    assert is_lock_timeout(info.value)
    assert attempts == ["200ms"] * 3
    assert sleeps == [0.01, 0.02]
    assert elapsed < FAST_FAIL_SECONDS


@pytest.mark.db
async def test_blocked_migration_succeeds_once_the_lock_is_released(
    lock_engine: AsyncEngine, locked_table: tuple[str, AsyncConnection]
) -> None:
    table, holder = locked_table
    attempts: list[int] = []

    def body(conn: Connection) -> None:
        run_with_lock_retries(
            lambda: _alter_attempt(conn, table, lambda _c: attempts.append(1)),
            retries=3,
            base_backoff_seconds=0,
            sleep=lambda _s: holder.sync_connection.rollback(),
        )

    async with lock_engine.connect() as conn:
        await conn.run_sync(body)
    assert len(attempts) == 2


# ---------------------------------------------------------------------------
# API statement timeout (Postgres; runs in CI)
# ---------------------------------------------------------------------------


def _mini_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/slow")
    async def _slow(db: AsyncSession = Depends(database.get_db)) -> dict:
        await db.execute(text("SELECT pg_sleep(3)"))
        return {"ok": True}

    @app.get("/slow-after-commit")
    async def _slow_after_commit(db: AsyncSession = Depends(database.get_db)) -> dict:
        await db.execute(text("SELECT 1"))
        await db.commit()  # the cap is re-applied on the next transaction
        await db.execute(text("SELECT pg_sleep(3)"))
        return {"ok": True}

    @app.get("/export")
    async def _export(db: AsyncSession = Depends(database.unbounded_session)) -> dict:
        await db.execute(text("SELECT pg_sleep(0.5)"))
        return {"timeout": await db.scalar(text("SHOW statement_timeout"))}

    @app.get("/runner")
    async def _runner(runner: ScopedRunner = Depends(get_scoped_runner)) -> dict:
        async with runner.session() as session:
            return {"timeout": await session.scalar(text("SHOW statement_timeout"))}

    @app.get("/factory")
    async def _factory(
        factory=Depends(database.get_session_factory),  # noqa: ANN001
    ) -> dict:
        async with factory() as session:
            return {"timeout": await session.scalar(text("SHOW statement_timeout"))}

    return app


@pytest.fixture
async def timeout_client(monkeypatch: pytest.MonkeyPatch):
    engine = new_engine()
    await require_postgres(engine)
    monkeypatch.setattr(
        database,
        "RequestSessionLocal",
        functools.partial(database.RequestSessionLocal, bind=engine),
    )
    monkeypatch.setattr(settings, "db_statement_timeout_ms", API_TIMEOUT_MS)
    transport = ASGITransport(app=_mini_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            yield ac, engine
    finally:
        await engine.dispose()


@pytest.mark.parametrize("path", ["/slow", "/slow-after-commit"])
async def test_slow_request_query_returns_503_db_timeout_quickly(
    timeout_client, path: str
) -> None:
    client, _engine = timeout_client
    started = time.monotonic()
    res = await client.get(path)
    elapsed = time.monotonic() - started

    assert res.status_code == 503
    assert res.headers["content-type"].startswith("application/problem+json")
    body = res.json()
    assert body["code"] == "db_timeout"
    assert body["request_id"]
    assert res.headers["X-Request-ID"] == body["request_id"]
    assert "Retry-After" in res.headers
    assert "pg_sleep" not in res.text
    assert elapsed < FAST_FAIL_SECONDS


async def test_request_scoped_factories_carry_the_api_timeout(timeout_client) -> None:
    client, _engine = timeout_client
    for path in ("/runner", "/factory"):
        res = await client.get(path)
        assert res.status_code == 200, path
        assert res.json()["timeout"] == f"{API_TIMEOUT_MS}ms", path


async def test_exports_opt_out_of_the_api_timeout(timeout_client) -> None:
    client, engine = timeout_client
    async with engine.connect() as conn:
        server_default = await conn.scalar(text("SHOW statement_timeout"))
    res = await client.get("/export")
    assert res.status_code == 200
    assert res.json()["timeout"] == server_default


async def test_background_sessions_are_not_capped(timeout_client) -> None:
    """Jobs worker / schedulers / ingestion scripts use ``AsyncSessionLocal``."""
    _client, engine = timeout_client
    async with engine.connect() as conn:
        server_default = await conn.scalar(text("SHOW statement_timeout"))
    async with database.AsyncSessionLocal(bind=engine) as session:
        await session.execute(text("SELECT pg_sleep(0.5)"))
        assert await session.scalar(text("SHOW statement_timeout")) == server_default


async def test_disabled_api_timeout_sets_nothing(timeout_client, monkeypatch) -> None:
    client, engine = timeout_client
    monkeypatch.setattr(settings, "db_statement_timeout_ms", 0)
    async with engine.connect() as conn:
        server_default = await conn.scalar(text("SHOW statement_timeout"))
    res = await client.get("/runner")
    assert res.json()["timeout"] == server_default


# ---------------------------------------------------------------------------
# Long request paths are wired to the opt-out
# ---------------------------------------------------------------------------


def _dependency_calls(route: RouteContext) -> set[object]:
    calls: set[object] = set()
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        calls.add(dep.call)
        stack.extend(dep.dependencies)
    return calls


@pytest.mark.parametrize(
    "endpoint",
    ["export_usage_csv", "export_feedback_pairs", "ingest_json", "ingest_file"],
)
def test_long_running_endpoints_use_unbounded_session(endpoint: str) -> None:
    from main import app  # noqa: PLC0415 - app import is heavy; keep it local

    routes = [r for r in flat_routes(app) if is_api_route(r) and r.name == endpoint]
    assert routes, endpoint
    for route in routes:
        assert database.unbounded_session in _dependency_calls(route), endpoint
