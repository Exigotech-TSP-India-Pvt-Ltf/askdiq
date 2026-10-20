"""Missing log sources (OWASP A09): every new trigger writes
exactly one insert-only, PII-masked audit row without blocking the caller."""

import gc
import json
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.core import rate_limit, sessions
from app.core.exceptions import register_exception_handlers
from app.core.middleware import LatencyMiddleware
from app.governance import audit
from app.governance.pii import redact_pii
from app.harness import orchestrator
from app.models.db_models import AuditLog, ChatSession
from tests.test_orchestrator import _run


@pytest.fixture(autouse=True, scope="module")
def _collect_garbage_after_module():
    """This module builds ad-hoc apps and MagicMock sessions (cyclic garbage).
    Collect it here so a later gen-2 GC pass does not land inside another
    test's event-loop blocking gate."""
    yield
    gc.collect()


@pytest.fixture
def captured(monkeypatch) -> list[dict]:
    """Capture flushed audit batches instead of writing them to the DB."""
    rows: list[dict] = []

    async def _collect(batch: list[dict]) -> None:
        rows.extend(batch)

    monkeypatch.setattr(audit, "_batch_writer", _collect)
    audit._queue.clear()
    return rows


def _pii_clean(detail: dict) -> bool:
    return redact_pii(json.dumps(detail))[1] == []


def _actions(rows: list[dict]) -> list[str]:
    return [r["action"] for r in rows]


# --------------------------------------------------------------------------
# Request-level events
# --------------------------------------------------------------------------


async def test_rate_limited_writes_exactly_one_row(captured):
    store: dict[str, deque[float]] = defaultdict(deque)
    rate_limit._check_and_record(store, "alice@example.com", 1, "per user")
    with pytest.raises(HTTPException) as exc:
        rate_limit._check_and_record(store, "alice@example.com", 1, "per user")
    assert exc.value.status_code == 429

    await audit.flush_audit_events()
    assert _actions(captured) == ["rate_limited"]
    row = captured[0]
    assert row["actor"] == "alice@example.com"
    assert row["detail"]["status_code"] == 429
    assert row["detail"]["scope"] == "per user"
    assert _pii_clean(row["detail"])


async def test_rate_limited_per_ip_does_not_use_ip_as_actor(captured):
    store: dict[str, deque[float]] = defaultdict(deque)
    rate_limit._check_and_record(store, "10.1.2.3", 1, "per IP")
    with pytest.raises(HTTPException):
        rate_limit._check_and_record(store, "10.1.2.3", 1, "per IP")
    await audit.flush_audit_events()
    assert _actions(captured) == ["rate_limited"]
    assert captured[0]["actor"] == "system"


async def test_rate_limited_on_shared_state_path_writes_one_row(monkeypatch, captured):
    class _OverLimitBackend:
        async def incr_window(self, _key: str, _window_s: int) -> int:
            return 999

    monkeypatch.setattr(rate_limit, "get_state_backend", _OverLimitBackend)
    with pytest.raises(HTTPException) as exc:
        await rate_limit.aenforce_ip_rate_limit("10.9.8.7")
    assert exc.value.status_code == 429

    await audit.flush_audit_events()
    assert _actions(captured) == ["rate_limited"]
    assert captured[0]["detail"]["scope"] == "per IP"
    assert _pii_clean(captured[0]["detail"])


async def test_record_server_error_helper_writes_one_row(captured):
    audit.record_server_error("req-500", "/query", 500, "RuntimeError")
    await audit.flush_audit_events()
    assert _actions(captured) == ["server_error"]
    assert captured[0]["request_id"] == "req-500"
    assert captured[0]["detail"] == {
        "path": "/query",
        "status_code": 500,
        "error_type": "RuntimeError",
    }


async def test_5xx_response_through_middleware_writes_one_server_error(captured):
    app = FastAPI()
    app.add_middleware(LatencyMiddleware)

    @app.get("/boom")
    async def boom() -> JSONResponse:
        return JSONResponse({"detail": "down"}, status_code=503)

    @app.get("/fine")
    async def fine() -> dict:
        return {"ok": True}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        assert (await client.get("/fine")).status_code == 200
        resp = await client.get("/boom")
    assert resp.status_code == 503

    await audit.flush_audit_events()
    assert _actions(captured) == ["server_error"]
    assert captured[0]["request_id"] == resp.headers["X-Request-ID"]
    assert captured[0]["detail"]["status_code"] == 503
    assert captured[0]["detail"]["path"] == "/boom"


async def test_unhandled_exception_writes_exactly_one_server_error(captured):
    app = FastAPI()
    app.add_middleware(LatencyMiddleware)
    register_exception_handlers(app)

    @app.get("/crash")
    async def crash() -> dict:
        raise RuntimeError("kaboom")

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://t",
    ) as client:
        resp = await client.get("/crash")
    assert resp.status_code == 500

    await audit.flush_audit_events()
    assert _actions(captured) == ["server_error"]
    assert captured[0]["detail"] == {
        "path": "/crash",
        "status_code": 500,
        "error_type": "RuntimeError",
    }
    assert captured[0]["request_id"] == resp.json()["request_id"]


async def test_llm_failure_and_retry_are_discrete_rows(monkeypatch, captured):
    response = await _run(
        monkeypatch,
        llm_side_effect=[RuntimeError("transient Azure error"), "Deploy IQ does X."],
        post_checks_side_effect=[(True, 0.9, [])],
    )
    assert response.grounded is True

    await audit.flush_audit_events()
    assert _actions(captured) == ["llm_call_failed", "llm_retry"]
    failed, retry = captured
    assert failed["request_id"] == "test-request"
    assert failed["detail"]["attempt"] == 1
    assert failed["detail"]["error_type"] == "RuntimeError"
    assert failed["detail"]["will_retry"] is True
    assert retry["detail"] == {
        "attempt": 2,
        "max_attempts": orchestrator.settings.max_llm_retries + 1,
        "reason": "llm_call_failed",
    }
    assert all(_pii_clean(r["detail"]) for r in captured)


async def test_successful_llm_call_writes_no_rows(monkeypatch, captured):
    await _run(
        monkeypatch,
        llm_side_effect=["Deploy IQ does X."],
        post_checks_side_effect=[(True, 0.9, [])],
    )
    await audit.flush_audit_events()
    assert captured == []


# --------------------------------------------------------------------------
# Writer behaviour: PII masking, batching, non-blocking
# --------------------------------------------------------------------------


async def test_emitted_detail_is_pii_masked(captured):
    audit.emit_audit_event(
        "server_error",
        {"path": "/x", "note": "contact bob@example.com or 0412 345 678"},
        request_id="r1",
    )
    await audit.flush_audit_events()
    detail = captured[0]["detail"]
    assert "bob@example.com" not in json.dumps(detail)
    assert "[REDACTED_EMAIL]" in detail["note"]
    assert _pii_clean(detail)


async def test_same_tick_events_are_flushed_as_one_batch(monkeypatch):
    batches: list[list[dict]] = []

    async def _collect(batch: list[dict]) -> None:
        batches.append(list(batch))

    monkeypatch.setattr(audit, "_batch_writer", _collect)
    audit._queue.clear()
    for i in range(5):
        audit.emit_audit_event("rate_limited", {"n": i}, request_id=f"r{i}")
    await audit.flush_audit_events()
    assert len(batches) == 1
    assert len(batches[0]) == 5


async def test_writer_failure_never_reaches_the_caller(monkeypatch):
    async def _boom(_batch: list[dict]) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(audit, "_batch_writer", _boom)
    audit._queue.clear()
    audit.emit_audit_event("server_error", {"path": "/x"}, request_id="r")
    await audit.flush_audit_events()  # must not raise
    assert audit._queue == []


# --------------------------------------------------------------------------
# Lifecycle and admin events
# --------------------------------------------------------------------------


def _fake_db(get_result: ChatSession | None) -> MagicMock:
    db = MagicMock()
    db.get = AsyncMock(return_value=get_result)
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _session(email: str, expired: bool) -> ChatSession:
    now = datetime.utcnow()
    return ChatSession(
        id=uuid.uuid4(),
        user_email=email,
        title="t",
        created_at=now - timedelta(hours=2),
        last_activity_at=now - timedelta(hours=1),
        expires_at=now - timedelta(minutes=1) if expired else now + timedelta(hours=1),
        created_ip="10.0.0.9",
    )


async def test_expired_session_writes_expire_and_rotate(captured):
    old = _session("u@example.com", expired=True)
    new = await sessions.resolve_session_for_query(
        _fake_db(old), "u@example.com", str(old.id), "10.0.0.9"
    )
    await audit.flush_audit_events()
    assert _actions(captured) == ["session_expired", "session_rotated"]
    assert captured[0]["detail"]["session_id"] == str(old.id)
    assert captured[1]["detail"] == {
        "from_session_id": str(old.id),
        "to_session_id": str(new.id),
        "reason": "expired",
    }
    assert all(r["actor"] == "u@example.com" for r in captured)


async def test_unknown_session_id_writes_one_rotate(captured):
    requested = str(uuid.uuid4())
    await sessions.resolve_session_for_query(
        _fake_db(None), "u@example.com", requested, "10.0.0.9"
    )
    await audit.flush_audit_events()
    assert _actions(captured) == ["session_rotated"]
    assert captured[0]["detail"]["reason"] == "not_found"


async def test_live_session_reuse_writes_nothing(captured):
    live = _session("u@example.com", expired=False)
    got = await sessions.resolve_session_for_query(
        _fake_db(live), "u@example.com", str(live.id), "10.0.0.9"
    )
    assert got is live
    await audit.flush_audit_events()
    assert captured == []


async def test_explicit_session_create_writes_session_created():
    db = _fake_db(None)
    await sessions.create_session(db, "u@example.com", "My chat", "10.0.0.9")
    added = [c.args[0] for c in db.add.call_args_list]
    events = [a for a in added if isinstance(a, AuditLog)]
    assert [e.action for e in events] == ["session_created"]
    assert events[0].actor == "u@example.com"
    assert events[0].detail["source"] == "explicit"


# --------------------------------------------------------------------------
# DB-backed (run in CI): admin events and the immutability trigger
# --------------------------------------------------------------------------


async def test_test_ip_update_is_audited_as_update(client, db_session):
    ip = "198.51.100.77"
    assert (
        await client.post("/admin/usage/test-ips", json={"ip_address": ip})
    ).status_code == 200
    assert (
        await client.post(
            "/admin/usage/test-ips", json={"ip_address": ip, "description": "VPN"}
        )
    ).status_code == 200
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action.in_(["test_ip_added", "test_ip_updated"])
                )
            )
        )
        .scalars()
        .all()
    )
    mine = [r for r in rows if (r.detail or {}).get("ip_address") == ip]
    assert sorted(r.action for r in mine) == ["test_ip_added", "test_ip_updated"]
    updated = next(r for r in mine if r.action == "test_ip_updated")
    assert updated.detail["previous"]["description"] is None
    assert (await client.delete(f"/admin/usage/test-ips/{ip}")).status_code == 200


async def test_new_event_rows_are_still_immutable(db_session):
    row = AuditLog(
        request_id="trigger-regression",
        actor="system",
        action="rate_limited",
        detail={"scope": "per IP"},
    )
    db_session.add(row)
    await db_session.flush()
    for stmt in (
        "UPDATE audit_log SET action = 'tampered' WHERE id = :id",
        "DELETE FROM audit_log WHERE id = :id",
    ):
        nested = await db_session.begin_nested()
        with pytest.raises(Exception) as exc:
            await db_session.execute(text(stmt), {"id": row.id})
        assert "append-only" in str(exc.value)
        await nested.rollback()
