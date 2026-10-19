"""Global pytest fixtures for AskDIQ backend test suite."""

import asyncio
import contextlib
import hashlib
import os
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import cache
from unittest.mock import patch

# Unit/API tests use the in-process shared-state backend so each test can
# reset rate-limit windows; Postgres-backed state is exercised explicitly in
# tests/test_shared_state.py. Must be set before the app settings load.
os.environ.setdefault("STATE_BACKEND", "memory")
# SSO tests run against the local mock IdP; secrets come from env, never .env.
os.environ.setdefault("AUTH_DEV_MOCK", "true")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-0123456789abcdef")

import pytest
from fastapi import Header
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sql_text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.deps import authenticate_session
from app.auth.permissions import Role, UserStatus
from app.auth.principal import AuthUser
from app.core.cache import dashboard_cache
from app.core.concurrency import ScopedRunner, get_scoped_runner
from app.core.config import get_settings
from app.core.database import get_db, get_session_factory
from app.governance.auth import sanitize_client_id
from main import app
from tests.insights_fixture import (  # noqa: F401 - shared pytest fixtures
    insights_client,
    insights_reference_db,
)

settings = get_settings()

DB_PROBE_TIMEOUT_SECONDS = 5.0
DB_FIXTURES = frozenset({"db_session", "client"})


def resolve_test_database_url() -> str:
    """TEST_DATABASE_URL wins over DATABASE_URL; TEST_DB_SUFFIX renames the DB."""
    url = settings.test_database_url or settings.database_url
    if not settings.test_db_suffix:
        return url
    parsed = make_url(url)
    return parsed.set(
        database=f"{parsed.database}_{settings.test_db_suffix}"
    ).render_as_string(hide_password=False)


test_engine = create_async_engine(
    resolve_test_database_url(),
    poolclass=NullPool,
)

TestingSessionLocal = async_sessionmaker(
    test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def _probe_database() -> None:
    async with test_engine.connect() as connection:
        await connection.execute(sql_text("SELECT 1"))


@cache
def db_skip_reason() -> str | None:
    """Reason to skip DB tests, or None when they must run.

    Only an explicit local opt-in (ALLOW_DB_SKIP=1) together with an
    unreachable database yields a skip. CI never sets ALLOW_DB_SKIP, so a
    missing database there fails loudly instead of skipping.
    """
    if os.environ.get("ALLOW_DB_SKIP") != "1":
        return None
    try:
        asyncio.run(asyncio.wait_for(_probe_database(), DB_PROBE_TIMEOUT_SECONDS))
    except Exception as exc:  # any connect failure means "unreachable"
        return f"test database unreachable and ALLOW_DB_SKIP=1 ({type(exc).__name__})"
    return None


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "db: test needs the database without using the db fixtures"
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip tests that need the database when it is unreachable (opt-in)."""
    reason = db_skip_reason()
    if reason is None:
        return
    marker = pytest.mark.skip(reason=reason)
    for item in items:
        needs_db = item.get_closest_marker("db") is not None or bool(
            DB_FIXTURES.intersection(getattr(item, "fixturenames", ()))
        )
        if needs_db:
            item.add_marker(marker)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


async def _signed_in_test_user(
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> AuthUser:
    """Stand-in for a resolved SSO session: an active superadmin.

    Only the session lookup is replaced; the `X-API-Key` service-key check,
    status checks and permission guards still run for real. Tests that need
    several users pick one per request with `X-Client-Id`.
    """
    email = sanitize_client_id(x_client_id)
    return AuthUser(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"askdiq-test:{email}"),
        email=email,
        name=email,
        role=Role.SUPERADMIN,
        status=UserStatus.ACTIVE,
        version=1,
    )


@pytest.fixture(autouse=True)
def _fresh_dashboard_cache(monkeypatch: pytest.MonkeyPatch):
    """Dashboard aggregates are cached per process; each test starts empty
    (rolled-back rows of one test must never answer another). Stored
    snapshots are off unless a test turns them on (tests/test_dashboard_snapshots.py):
    many tests share one transactional session across concurrent requests."""
    monkeypatch.setattr(get_settings(), "dashboard_snapshots_persist", False)
    dashboard_cache.clear()
    yield
    dashboard_cache.clear()


@pytest.fixture(autouse=True)
def signed_in_superadmin(request: pytest.FixtureRequest):
    """Every API test runs as a signed-in superadmin unless marked `real_auth`
    (which exercises the real session resolution in tests/test_auth_*.py)."""
    if request.node.get_closest_marker("real_auth"):
        app.dependency_overrides.pop(authenticate_session, None)
        yield
        return
    app.dependency_overrides[authenticate_session] = _signed_in_test_user
    yield
    app.dependency_overrides.pop(authenticate_session, None)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provides a transactional database session rolled back after each test."""
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


def shared_session_factory(
    session: AsyncSession,
) -> Callable[[], contextlib.AbstractAsyncContextManager[AsyncSession]]:
    """A ``gather_scoped`` session factory that hands out the test's
    transactional session, one task at a time (an AsyncSession is not
    concurrency-safe), so parallel aggregates see uncommitted test data."""
    lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def _factory() -> AsyncIterator[AsyncSession]:
        async with lock:
            yield session

    return _factory


def shared_session_runner(session: AsyncSession) -> ScopedRunner:
    """A ScopedRunner whose every task borrows ``session`` (limit=1: sequential)."""

    @asynccontextmanager
    async def _borrow() -> AsyncGenerator[AsyncSession, None]:
        yield session

    return ScopedRunner(session_factory=_borrow, limit=1)


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Test client with database dependency override and valid default auth."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_factory] = lambda: shared_session_factory(
        db_session
    )
    # Parallel-aggregation routes (gather_scoped) share the transactional
    # session too, one task at a time, so they see rows seeded by the test.
    app.dependency_overrides[get_scoped_runner] = lambda: shared_session_runner(
        db_session
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"X-API-Key": settings.api_key_name or "ci-test-key"},
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def fake_embed():
    """Deterministic embedding fake generating reproducible 1536-dim vectors."""
    calls: list[str] = []

    def _make_vector(text: str) -> list[float]:
        calls.append(text)
        h = hashlib.sha256(text.encode("utf-8")).digest()
        base = [float(b) / 255.0 for b in h]
        return (base * 48)[:1536]

    async def _embed_text(
        text: str, task_type: str | None = None, **kwargs
    ) -> list[float]:
        return _make_vector(text)

    async def _embed_texts(
        texts: list[str], task_type: str | None = None, **kwargs
    ) -> list[list[float]]:
        from app.retrieval.embeddings import (
            current_embedding_stage,
            embedding_tokens_sink,
        )

        sink = embedding_tokens_sink.get()
        if sink is not None:
            stage = kwargs.get("stage") or current_embedding_stage.get()
            sink.append((stage, sum(len(t.split()) for t in texts) * 2 or 4))
        return [_make_vector(t) for t in texts]

    with (
        patch("app.retrieval.embeddings.embed_text", side_effect=_embed_text),
        patch("app.retrieval.embeddings.embed_texts", side_effect=_embed_texts),
        patch("app.guardrails.post_checks.embed_texts", side_effect=_embed_texts),
        patch("app.ingestion.service.embed_texts", side_effect=_embed_texts),
    ):
        yield {"calls": calls, "embed": _embed_text, "embed_many": _embed_texts}


@pytest.fixture
def fake_llm():
    """Configurable LLM response mock with token usage tracking."""
    calls: list[dict] = []
    response_holder = {
        "answer": "This is a deterministic fake answer for tests.",
        "usage": {
            "prompt_tokens": 15,
            "completion_tokens": 25,
            "total_tokens": 40,
            "reasoning_tokens": 0,
        },
    }

    async def _call_llm(
        prompt: str,
        context: list[str] | None = None,
        history: list | None = None,
        **kwargs,
    ):
        calls.append(
            {"prompt": prompt, "context": context, "history": history, "kwargs": kwargs}
        )
        return response_holder["answer"], response_holder["usage"]

    with (
        patch("app.harness.prompts.call_llm", side_effect=_call_llm),
        patch("app.harness.orchestrator.call_llm", side_effect=_call_llm),
    ):
        yield {
            "calls": calls,
            "set_response": lambda a, u: response_holder.update(
                {"answer": a, "usage": u}
            ),
        }


class FrozenTime:
    def __init__(self, start_timestamp: float = 1700000000.0):
        self._current = start_timestamp

    def time(self) -> float:
        return self._current

    def monotonic(self) -> float:
        return self._current

    def advance(self, seconds: float) -> None:
        self._current += seconds


@pytest.fixture
def frozen_time():
    """Allows freezing and advancing monotonic and wall time for rate limit testing."""
    ft = FrozenTime()
    with (
        patch("time.time", side_effect=ft.time),
        patch("time.monotonic", side_effect=ft.monotonic),
    ):
        yield ft
