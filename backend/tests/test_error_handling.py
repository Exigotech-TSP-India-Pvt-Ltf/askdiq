"""Exceptional conditions (OWASP A10).

problem+json error bodies, supervised background tasks and lifespan drain.
No database is needed: ``get_db`` is overridden with a stub.
"""

import asyncio
import inspect
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import main
from app.core import middleware, tasks
from app.core.config import get_settings
from app.core.database import get_db
from main import app

settings = get_settings()
API_KEY = settings.api_key_name or "ci-test-key"
PII_EMAIL = "jane.citizen@example.com"


class _ExplodingSession:
    def __getattr__(self, name: str):
        raise RuntimeError(
            f"connection to db-internal-host:5432 failed for {PII_EMAIL}"
        )


async def _exploding_db() -> AsyncGenerator[_ExplodingSession, None]:
    yield _ExplodingSession()


@pytest.fixture
async def api() -> AsyncGenerator[AsyncClient, None]:
    app.dependency_overrides[get_db] = _exploding_db
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"X-API-Key": API_KEY},
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


def _assert_problem(res, status: int) -> dict:
    assert res.status_code == status
    assert res.headers["content-type"].startswith("application/problem+json")
    body = res.json()
    for key in ("type", "title", "status", "detail", "request_id"):
        assert key in body, key
    assert body["status"] == status
    assert body["request_id"]
    assert res.headers["x-request-id"] == body["request_id"]
    return body


# --- Global exception handlers ---------------------------------------------


async def test_forced_error_returns_500_problem_without_internals(
    api: AsyncClient,
) -> None:
    with patch("app.core.exceptions.logger") as log:
        # Uncached route: always queries the (exploding) DB session.
        res = await api.get("/documents")
    body = _assert_problem(res, 500)
    assert body["title"] == "Internal Server Error"
    text = res.text
    for leaked in (
        "Traceback",
        "RuntimeError",
        "db-internal-host",
        PII_EMAIL,
        'File "',
    ):
        assert leaked not in text, leaked
    # Security headers are set even though Starlette's outermost
    # ServerErrorMiddleware produced this response.
    assert res.headers["x-content-type-options"] == "nosniff"

    log.error.assert_called_once()
    kwargs = log.error.call_args.kwargs
    assert kwargs["request_id"] == body["request_id"]
    assert kwargs["error_type"] == "RuntimeError"
    # Logged with PII masked.
    assert PII_EMAIL not in kwargs["error"]
    assert "[REDACTED_EMAIL]" in kwargs["error"]
    assert PII_EMAIL not in kwargs["traceback"]


async def test_validation_error_lists_fields_without_echoing_input(
    api: AsyncClient,
) -> None:
    leaked_value = "sk-live-SUPERSECRET-9f8e7d"
    res = await api.get(f"/admin/usage/queries?limit={leaked_value}&offset=-5")
    body = _assert_problem(res, 422)
    assert body["type"] == "urn:askdiq:problem:validation-error"
    assert isinstance(body["detail"], str)
    fields = {tuple(err["loc"]) for err in body["errors"]}
    assert ("query", "limit") in fields
    for err in body["errors"]:
        assert set(err) == {"loc", "msg", "type"}
    assert leaked_value not in res.text


async def test_validation_error_on_body_does_not_echo_secret(
    api: AsyncClient,
) -> None:
    leaked_value = "password=hunter2-TOPSECRET"
    res = await api.post("/query", json={"query": {"nested": leaked_value}})
    body = _assert_problem(res, 422)
    assert body["errors"]
    assert leaked_value not in res.text
    assert "hunter2" not in res.text


async def test_http_exception_keeps_detail_and_headers(api: AsyncClient) -> None:
    res = await api.get("/admin/usage/summary", headers={"X-API-Key": "wrong"})
    body = _assert_problem(res, 401)
    # Existing clients read body.detail; it is preserved.
    assert body["detail"] == "Access Denied!"
    assert body["title"] == "Unauthorized"


async def test_not_found_and_method_not_allowed_are_problem_json(
    api: AsyncClient,
) -> None:
    missing = await api.get("/no/such/route")
    assert _assert_problem(missing, 404)["detail"] == "Not Found"
    wrong_method = await api.put("/branding")
    _assert_problem(wrong_method, 405)
    assert "allow" in wrong_method.headers


# --- Supervised background tasks -------------------------------------------


async def test_failing_task_is_logged_not_lost() -> None:
    async def boom() -> None:
        raise ValueError(f"persist failed for {PII_EMAIL}")

    with patch.object(tasks, "logger") as log:
        task = tasks.spawn(boom(), name="test_boom")
        assert task in tasks._tasks  # strong reference while running
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)  # let the done-callback run

    assert task not in tasks._tasks
    log.error.assert_called_once()
    assert log.error.call_args.args[0] == "background_task_failed"
    kwargs = log.error.call_args.kwargs
    assert kwargs["task_name"] == "test_boom"
    assert kwargs["error_type"] == "ValueError"
    assert PII_EMAIL not in kwargs["error"]


async def test_drain_waits_for_in_flight_tasks() -> None:
    finished: list[str] = []

    async def slow_ok() -> None:
        await asyncio.sleep(0.05)
        finished.append("done")

    tasks.spawn(slow_ok(), name="test_slow_ok")
    assert tasks.pending_tasks() >= 1
    cancelled = await tasks.drain(grace_seconds=2.0)
    assert cancelled == 0
    assert finished == ["done"]
    assert tasks.pending_tasks() == 0


async def test_drain_is_bounded_and_cancels_stragglers() -> None:
    async def hang() -> None:
        await asyncio.sleep(30)

    task = tasks.spawn(hang(), name="test_hang")
    loop = asyncio.get_running_loop()
    started = loop.time()
    cancelled = await tasks.drain(grace_seconds=0.05)
    assert loop.time() - started < 2.0
    assert cancelled == 1
    assert task.cancelled()


async def test_drain_with_nothing_pending_returns_immediately() -> None:
    assert await tasks.drain(grace_seconds=0.01) == 0


def test_latency_middleware_uses_supervised_spawn() -> None:
    source = inspect.getsource(middleware)
    assert "asyncio.create_task" not in source
    assert "spawn(" in source


async def test_lifespan_drains_background_tasks_on_shutdown() -> None:
    drain = AsyncMock(return_value=0)
    with (
        patch.object(main, "init_db", AsyncMock()),
        patch.object(main, "configure_thread_pool", MagicMock()),
        patch.object(main, "_startup_azure_health_check", AsyncMock()),
        patch.object(main, "preload_reranker", MagicMock()),
        patch.object(main, "drain_background_tasks", drain),
    ):
        async with main.lifespan(app):
            drain.assert_not_awaited()
    drain.assert_awaited_once_with(settings.task_drain_timeout_seconds)


async def test_bodyless_http_exception_has_no_problem_body() -> None:
    from fastapi import FastAPI, HTTPException

    from app.core.exceptions import register_exception_handlers

    mini = FastAPI()
    register_exception_handlers(mini)

    @mini.get("/etag")
    async def etag() -> None:
        raise HTTPException(status_code=304, headers={"ETag": '"v1"'})

    async with AsyncClient(
        transport=ASGITransport(app=mini), base_url="http://testserver"
    ) as ac:
        res = await ac.get("/etag")
    assert res.status_code == 304
    assert res.content == b""
    assert res.headers["etag"] == '"v1"'
