"""Request write path robustness (no database).

- The background exchange write is supervised (drained on shutdown), retries
  transient database failures with backoff, and is idempotent.
- Online evaluation is supervised, bounded (backlog cap) and runs its CPU
  checks off the event loop.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core import sessions, tasks
from app.governance import online_eval

EXCHANGE = {
    "user_email": "write-path@example.com",
    "session_id": uuid.uuid4(),
    "new_session": None,
    "user_message_id": uuid.uuid4(),
    "assistant_message_id": uuid.uuid4(),
    "request_id": "req-write",
    "client_ip": "203.0.113.7",
    "audit_detail": {},
}


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sessions, "_WRITE_RETRY_FIRST_SECONDS", 0.0)


def _operational() -> OperationalError:
    return OperationalError("INSERT", {}, ConnectionResetError("reset by peer"))


async def test_transient_failure_is_retried_then_written(monkeypatch) -> None:
    attempts: list[int] = []

    async def flaky(**_kwargs: Any) -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise _operational()

    async def not_there(_mid: uuid.UUID) -> bool:
        return False

    monkeypatch.setattr(sessions, "_write_exchange_once", flaky)
    monkeypatch.setattr(sessions, "_exchange_exists", not_there)
    await sessions._write_exchange(**EXCHANGE)
    assert len(attempts) == 3


async def test_retry_stops_when_a_lost_commit_actually_landed(monkeypatch) -> None:
    attempts: list[int] = []

    async def fails_once(**_kwargs: Any) -> None:
        attempts.append(1)
        raise _operational()

    async def landed(_mid: uuid.UUID) -> bool:
        return True

    monkeypatch.setattr(sessions, "_write_exchange_once", fails_once)
    monkeypatch.setattr(sessions, "_exchange_exists", landed)
    await sessions._write_exchange(**EXCHANGE)
    assert len(attempts) == 1  # idempotent: no duplicate insert


async def test_non_transient_failure_is_not_retried(monkeypatch) -> None:
    attempts: list[int] = []

    async def broken(**_kwargs: Any) -> None:
        attempts.append(1)
        raise IntegrityError("INSERT", {}, ValueError("duplicate key"))

    monkeypatch.setattr(sessions, "_write_exchange_once", broken)
    await sessions._write_exchange(**EXCHANGE)  # logged, never raised
    assert len(attempts) == 1


async def test_retries_are_bounded(monkeypatch) -> None:
    attempts: list[int] = []

    async def always_down(**_kwargs: Any) -> None:
        attempts.append(1)
        raise ConnectionRefusedError("db down")

    async def not_there(_mid: uuid.UUID) -> bool:
        return False

    monkeypatch.setattr(sessions, "_write_exchange_once", always_down)
    monkeypatch.setattr(sessions, "_exchange_exists", not_there)
    await sessions._write_exchange(**EXCHANGE)
    assert len(attempts) == sessions._WRITE_ATTEMPTS


async def test_exchange_write_is_a_supervised_task(monkeypatch) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_write(**_kwargs: Any) -> None:
        started.set()
        await release.wait()

    monkeypatch.setattr(sessions, "_write_exchange", slow_write)

    class _Session:
        id = uuid.uuid4()
        title = "Chat"

    monkeypatch.setattr(
        sessions, "sa_inspect", lambda _s: type("I", (), {"transient": False})()
    )
    _u, assistant_id, _t = sessions.schedule_exchange_write(
        user_email="sup@example.com",
        session=_Session(),  # type: ignore[arg-type]
        audit_detail={},
        request_id="req-sup",
        client_ip=None,
        user_text="hello",
    )
    await started.wait()
    task = sessions.exchange_write_task(assistant_id)
    assert task is not None
    assert task in tasks._tasks  # drained by the lifespan shutdown
    release.set()
    await task


def test_online_eval_skips_when_backlog_is_full(monkeypatch) -> None:
    cfg = online_eval.get_settings().model_copy(update={"online_eval_max_pending": 2})
    monkeypatch.setattr(online_eval, "get_settings", lambda: cfg)
    monkeypatch.setattr(online_eval, "_background_tasks", {object(), object()})
    spawned: list[Any] = []
    monkeypatch.setattr(online_eval, "spawn", lambda coro, **_kw: spawned.append(coro))
    online_eval.schedule_online_evaluation(request_id="r", query="q", answer="a")
    assert spawned == []


def test_rule_checks_flag_secrets_and_pii() -> None:
    flags, precision, coverage = online_eval._rule_checks(
        "r",
        "What is our key?",
        "The key is AKIAABCDEFGHIJKLMNOP, mail jane.citizen@example.com",
        ["Unrelated context."],
    )
    assert "secret_leak_detected" in flags
    assert "pii_leak_detected" in flags
    assert 0.0 <= precision <= 1.0 and 0.0 <= coverage <= 1.0
