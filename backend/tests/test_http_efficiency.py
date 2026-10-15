"""HTTP efficiency (plan B1.5): gzip, ETag/304, pool, statement timeout,
gather_scoped and the SSE helper."""

from __future__ import annotations

import asyncio
import traceback
from typing import ClassVar

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import database
from app.core.compression import SSEAwareGZipMiddleware
from app.core.concurrency import gather_scoped
from app.core.config import get_settings
from app.core.http_cache import cached_response, weak_etag
from app.core.sse import KEEPALIVE_FRAME, SSEEvent, event_stream, format_sse
from tests.loop_guard import LoopBlockingDetector
from tests.pg_helpers import new_engine, require_postgres

settings = get_settings()
BIG_PAYLOAD = {"rows": [{"id": i, "label": f"row-{i}"} for i in range(200)]}


def _mini_app() -> FastAPI:
    mini = FastAPI()
    mini.add_middleware(SSEAwareGZipMiddleware, minimum_size=1024)
    state = {"payload": BIG_PAYLOAD}

    @mini.get("/cached")
    async def _cached(request: Request):
        return cached_response(request, state["payload"], max_age=15)

    @mini.get("/small")
    async def _small(request: Request):
        return cached_response(request, {"ok": True})

    mini.state.data = state
    return mini


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


# ---------------------------------------------------------------------------
# GZip + ETag (no DB)
# ---------------------------------------------------------------------------


async def test_large_payloads_are_gzip_encoded():
    async with await _client(_mini_app()) as ac:
        res = await ac.get("/cached", headers={"Accept-Encoding": "gzip"})
        small = await ac.get("/small", headers={"Accept-Encoding": "gzip"})
    assert res.status_code == 200
    assert res.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in res.headers["vary"]
    assert res.json() == BIG_PAYLOAD
    assert "content-encoding" not in small.headers


async def test_second_request_with_etag_gets_304():
    app = _mini_app()
    async with await _client(app) as ac:
        first = await ac.get("/cached")
        etag = first.headers["etag"]
        assert etag.startswith('W/"')
        assert first.headers["cache-control"] == "private, max-age=15"

        # Warm path from here on: no callback may block the loop > 50 ms.
        with LoopBlockingDetector():
            second = await ac.get("/cached", headers={"If-None-Match": etag})
        assert second.status_code == 304
        assert second.content == b""
        assert second.headers["etag"] == etag

        listed = await ac.get("/cached", headers={"If-None-Match": f'"x", {etag}'})
        assert listed.status_code == 304

        app.state.data["payload"] = {"rows": []}
        changed = await ac.get("/cached", headers={"If-None-Match": etag})
        assert changed.status_code == 200
        assert changed.headers["etag"] != etag


def test_weak_etag_is_stable_for_identical_bodies():
    assert weak_etag(b'{"a":1}') == weak_etag(b'{"a":1}')
    assert weak_etag(b'{"a":1}') != weak_etag(b'{"a":2}')


def test_main_app_has_gzip_and_pool_pre_ping_from_config():
    from main import app

    assert any(m.cls is SSEAwareGZipMiddleware for m in app.user_middleware)
    assert database.engine.pool._pre_ping is settings.db_pool_pre_ping


# ---------------------------------------------------------------------------
# Dashboard + usage summary ETag round trip (real Postgres; runs in CI)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/admin/dashboard?audience=overview", "/admin/usage/summary"]
)
async def test_admin_aggregations_send_etag_and_304(client, path, monkeypatch):
    from app.api.routes import admin

    # The dashboard reports live process counters; freeze them so two reads
    # of unchanged data produce the same representation.
    monkeypatch.setattr(
        admin, "METRICS", {"requests": 1, "errors": 0, "active_queries": 0}
    )
    first = await client.get(path)
    assert first.status_code == 200
    assert first.headers["cache-control"].startswith("private, max-age=")
    etag = first.headers["etag"]

    second = await client.get(path, headers={"If-None-Match": etag})
    assert second.status_code == 304


# ---------------------------------------------------------------------------
# read_only_session statement timeout (real Postgres; runs in CI)
# ---------------------------------------------------------------------------


async def test_slow_dashboard_query_is_cancelled_with_clean_503(monkeypatch):
    engine = new_engine()
    await require_postgres(engine)
    monkeypatch.setattr(
        database,
        "AsyncSessionLocal",
        async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession),
    )
    monkeypatch.setattr(settings, "dashboard_statement_timeout_ms", 200)

    mini = FastAPI()

    @mini.get("/slow")
    async def _slow(db: AsyncSession = Depends(database.read_only_session)):
        await db.execute(text("SELECT pg_sleep(3)"))
        return {"ok": True}

    @mini.get("/fast")
    async def _fast(db: AsyncSession = Depends(database.read_only_session)):
        timeout = await db.scalar(text("SHOW statement_timeout"))
        return {"timeout": timeout}

    try:
        async with await _client(mini) as ac:
            slow = await ac.get("/slow")
            fast = await ac.get("/fast")
    finally:
        await engine.dispose()

    assert slow.status_code == 503
    assert "Retry-After" in slow.headers
    assert "pg_sleep" not in slow.text
    assert fast.status_code == 200
    assert fast.json()["timeout"] == "200ms"


# ---------------------------------------------------------------------------
# gather_scoped (no DB, plus one real-DB check)
# ---------------------------------------------------------------------------


class _FakeSession:
    instances: ClassVar[list[_FakeSession]] = []

    def __init__(self) -> None:
        self.users = 0
        _FakeSession.instances.append(self)

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


async def test_gather_scoped_uses_one_session_per_task_and_bounds_concurrency():
    _FakeSession.instances.clear()
    active = 0
    peak = 0

    def make(index: int):
        async def _query(session: _FakeSession) -> tuple[int, int]:
            nonlocal active, peak
            session.users += 1
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return index, id(session)

        return _query

    results = await gather_scoped(
        *(make(i) for i in range(8)), limit=3, session_factory=_FakeSession
    )

    assert [r[0] for r in results] == list(range(8))
    assert len({r[1] for r in results}) == 8
    assert all(s.users == 1 for s in _FakeSession.instances)
    assert peak <= 3


async def test_gather_scoped_propagates_first_error_and_cancels_the_rest():
    cancelled = 0

    async def boom(_session: _FakeSession) -> None:
        await asyncio.sleep(0.01)
        raise ValueError("first failure")

    async def slow(_session: _FakeSession) -> None:
        nonlocal cancelled
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled += 1
            raise

    with pytest.raises(ValueError, match="first failure"):
        await gather_scoped(boom, slow, slow, limit=3, session_factory=_FakeSession)
    assert cancelled == 2


async def test_gather_scoped_runs_parallel_queries_on_postgres():
    engine = new_engine(pool_size=8, max_overflow=0)
    await require_postgres(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    sessions: set[int] = set()

    def make(index: int):
        async def _query(session: AsyncSession) -> int:
            sessions.add(id(session))
            return await session.scalar(text("SELECT :n"), {"n": index})

        return _query

    try:
        results = await gather_scoped(
            *(make(i) for i in range(8)), limit=4, session_factory=factory
        )
    finally:
        await engine.dispose()
    assert results == list(range(8))
    assert len(sessions) == 8


# ---------------------------------------------------------------------------
# SSE helper (no DB)
# ---------------------------------------------------------------------------


def test_format_sse_frames():
    assert format_sse({"a": 1}) == 'data: {"a": 1}\n\n'
    assert format_sse(SSEEvent(data="x\ny", event="tick", id="7")) == (
        "id: 7\nevent: tick\ndata: x\ndata: y\n\n"
    )


async def _drive_sse(app, until_chunks: int, accept_gzip: bool = False):
    """Call the ASGI app directly so the stream can be observed and cut."""
    chunks: list[bytes] = []
    start_message: dict = {}
    disconnect = asyncio.Event()
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal start_message
        if message["type"] == "http.response.start":
            start_message = message
        elif message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"])
            if len(chunks) >= until_chunks:
                disconnect.set()

    headers = [(b"accept-encoding", b"gzip")] if accept_gzip else []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/stream",
        "raw_path": b"/stream",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("t", 80),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    return start_message, chunks


async def test_sse_sends_retry_events_and_heartbeats_then_stops_on_disconnect():
    source_closed = asyncio.Event()

    async def source():
        try:
            yield {"n": 1}
            while True:
                await asyncio.sleep(3600)
                yield {"never": True}
        finally:
            source_closed.set()

    mini = FastAPI()
    mini.add_middleware(SSEAwareGZipMiddleware, minimum_size=1)

    @mini.get("/stream")
    async def _stream(request: Request):
        return event_stream(request, source(), heartbeat=0.05, retry_ms=1500)

    baseline = len(asyncio.all_tasks())
    start, chunks = await _drive_sse(mini, until_chunks=4, accept_gzip=True)
    await asyncio.sleep(0.05)

    headers = dict(start["headers"])
    assert headers[b"content-type"].startswith(b"text/event-stream")
    assert b"content-encoding" not in headers, "SSE must bypass gzip"
    body = b"".join(chunks).decode()
    assert body.startswith("retry: 1500\n\n")
    assert 'data: {"n": 1}\n\n' in body
    assert KEEPALIVE_FRAME in body
    assert source_closed.is_set()
    assert len(asyncio.all_tasks()) <= baseline


async def test_sse_surfaces_source_errors_without_leaking_tasks():
    async def broken():
        yield {"n": 1}
        raise RuntimeError("source failed")

    mini = FastAPI()

    @mini.get("/stream")
    async def _stream(request: Request):
        return event_stream(request, broken(), heartbeat=1)

    baseline = len(asyncio.all_tasks())
    error: BaseException | None = None
    try:
        await _drive_sse(mini, until_chunks=100)
    except Exception as exc:  # Starlette may wrap it in an ExceptionGroup
        error = exc
    assert error is not None
    assert "source failed" in "".join(traceback.format_exception(error))
    await asyncio.sleep(0.05)
    assert len(asyncio.all_tasks()) <= baseline
