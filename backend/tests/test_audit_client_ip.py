"""Every audit row written during a request records the client IP.

The Governance Logs "Client IP" column was empty for sign-ins, exports and
5xx rows: uvicorn's proxy-headers middleware clears the peer to ``(None, 0)``
when every X-Forwarded-For hop is a trusted proxy (the local Next server), and
some writers did not pass the IP at all. Background / system jobs have no
client and are listed explicitly in ``SYSTEM_ACTIONS``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.audit import AuthEvent
from app.auth.mock_idp import MockIdentity
from app.core import request_meta
from app.core.exceptions import register_exception_handlers
from app.core.logging_config import bind_request_context, clear_request_context
from app.core.middleware import LatencyMiddleware
from app.governance import audit, logs
from app.governance.log_summary import SERVICE_BY_ACTION
from app.models.db_models import AuditLog, AuthSession
from main import app
from tests.auth_helpers import authorize, make_user, mock_idp_sign_in

# What uvicorn hands the app when the Next server forwards its own loopback.
CLEARED_PEER = (None, 0)
PROXY_HOP = "127.0.0.1"
CLIENT_IP = "203.0.113.7"
# Driven by ``test_server_error_rows_carry_the_client_ip`` (queued write path).
SERVER_ERROR = "server_error"

# Request-path writers that rely on the IP LatencyMiddleware binds (covered by
# ``test_record_audit_event_falls_back_to_the_request_ip``).
REQUEST_CONTEXT_ACTIONS: frozenset[str] = frozenset(
    {
        "query",
        "session_created",
        "session_rotated",
        "session_resumed",
        "session_expired",
        "llm_call_failed",
        "llm_retry",
        "ingest",
        "document_delete",
        "user_feedback",
        "negative_feedback_spike",
        "feedback_promoted_to_eval_case",
        "test_ip_added",
        "test_ip_updated",
        "test_ip_removed",
        "alert_acknowledged",
        "alert_resolved",
        "rate_limited",
        *(e.value for e in AuthEvent),
    }
)
# Written by background jobs with no client: an empty IP is correct.
SYSTEM_ACTIONS: frozenset[str] = frozenset({"online_eval"})


# --------------------------------------------------------------------------
# get_client_ip
# --------------------------------------------------------------------------


def _request(client: tuple | None, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": client})


def test_cleared_peer_uses_the_trusted_proxy_hop(monkeypatch):
    monkeypatch.setattr(request_meta.settings, "trust_proxy_headers", False)
    assert request_meta.get_client_ip(_request(CLEARED_PEER, PROXY_HOP)) == PROXY_HOP


def test_cleared_peer_never_takes_the_spoofable_left_hop(monkeypatch):
    monkeypatch.setattr(request_meta.settings, "trust_proxy_headers", False)
    ip = request_meta.get_client_ip(_request(CLEARED_PEER, f"6.6.6.6, {PROXY_HOP}"))
    assert ip == PROXY_HOP


def test_direct_peer_ignores_forwarded_for_without_trust(monkeypatch):
    monkeypatch.setattr(request_meta.settings, "trust_proxy_headers", False)
    ip = request_meta.get_client_ip(_request(("10.0.0.5", 5000), "6.6.6.6"))
    assert ip == "10.0.0.5"


@pytest.mark.parametrize(
    ("client", "xff"), [(None, None), (CLEARED_PEER, None), (CLEARED_PEER, "junk")]
)
def test_no_address_is_unknown_never_empty(monkeypatch, client, xff):
    monkeypatch.setattr(request_meta.settings, "trust_proxy_headers", False)
    assert request_meta.get_client_ip(_request(client, xff)) == "unknown"


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------


def test_every_known_action_is_classified():
    """A new audit action must be driven below, rely on the request context,
    or be declared a system action (no client)."""
    known = set(SERVICE_BY_ACTION) | {e.value for e in AuthEvent}
    classified = (
        set(DRIVERS) | {SERVER_ERROR} | REQUEST_CONTEXT_ACTIONS | SYSTEM_ACTIONS
    )
    assert known - classified == set()
    assert SYSTEM_ACTIONS.isdisjoint(set(DRIVERS) | REQUEST_CONTEXT_ACTIONS)


async def test_record_audit_event_falls_back_to_the_request_ip():
    session = MagicMock()
    bind_request_context(request_id="r-1", client_ip=CLIENT_IP)
    try:
        await audit.record_audit_event(session, "r-1", "a", "query", {}, commit=False)
    finally:
        clear_request_context()
    await audit.record_audit_event(
        session, "system", "job", "online_eval", {}, commit=False
    )
    in_request, in_job = (call.args[0] for call in session.add.call_args_list)
    assert in_request.ip_address == CLIENT_IP
    assert in_job.ip_address is None


@pytest.fixture
def captured(monkeypatch) -> list[dict]:
    rows: list[dict] = []

    async def _collect(batch: list[dict]) -> None:
        rows.extend(batch)

    monkeypatch.setattr(audit, "_batch_writer", _collect)
    audit._queue.clear()
    return rows


@pytest.mark.parametrize("path", ["/boom", "/five-oh-three"])
async def test_server_error_rows_carry_the_client_ip(monkeypatch, captured, path):
    monkeypatch.setattr(request_meta.settings, "trust_proxy_headers", False)
    mini = FastAPI()
    mini.add_middleware(LatencyMiddleware)
    register_exception_handlers(mini)

    @mini.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    @mini.get("/five-oh-three")
    async def unavailable() -> JSONResponse:
        return JSONResponse({}, status_code=503)

    transport = httpx.ASGITransport(
        app=mini, raise_app_exceptions=False, client=CLEARED_PEER
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        response = await c.get(path, headers={"X-Forwarded-For": PROXY_HOP})
    assert response.status_code >= 500
    await audit.flush_audit_events()
    errors = [r for r in captured if r["action"] == SERVER_ERROR]
    assert len(errors) == 1
    assert errors[0]["ip_address"] == PROXY_HOP


# --------------------------------------------------------------------------
# End to end through the real app (Postgres)
# --------------------------------------------------------------------------

Driver = Callable[[httpx.AsyncClient], Awaitable[httpx.Response]]


async def _login_success(c: httpx.AsyncClient) -> httpx.Response:
    body = await authorize(c, MockIdentity(email="ip-login@example.com", name="Ip"))
    return await c.post("/auth/sso/callback", json=body)


async def _login_denied(c: httpx.AsyncClient) -> httpx.Response:
    bad = {"code": "not-a-code", "state": "x" * 32}
    return await c.post("/auth/sso/callback", json=bad)


DRIVERS: dict[str, Driver] = {
    "login_success": _login_success,
    "login_denied": _login_denied,
    "feedback_exported": lambda c: c.get("/admin/feedback/export?format=jsonl"),
    "governance_logs_export": lambda c: c.get("/admin/governance/logs/export.csv"),
    "usage_export": lambda c: c.get("/admin/usage/export?tab=queries"),
}


@pytest.fixture
async def proxied_client(
    client: httpx.AsyncClient, monkeypatch
) -> AsyncIterator[httpx.AsyncClient]:
    """The conftest client's overrides, behind a peer uvicorn has cleared."""
    monkeypatch.setattr(request_meta.settings, "trust_proxy_headers", False)
    transport = httpx.ASGITransport(app=app, client=CLEARED_PEER)
    async with (
        mock_idp_sign_in(),
        httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={**client.headers, "X-Forwarded-For": PROXY_HOP},
        ) as c,
    ):
        yield c


@pytest.mark.parametrize("action", sorted(DRIVERS))
async def test_request_audit_rows_record_the_client_ip(
    action: str, proxied_client: httpx.AsyncClient, db_session: AsyncSession
):
    response = await DRIVERS[action](proxied_client)
    assert response.status_code < 500, response.text
    if action == "login_success":
        assert response.status_code == 200, response.text
    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == action)))
        .scalars()
        .all()
    )
    assert rows, f"no {action} row written"
    assert {row.ip_address for row in rows} == {PROXY_HOP}


# --------------------------------------------------------------------------
# Logs view: correlated IPs (never stored)
# --------------------------------------------------------------------------


async def _audit_row(db: AsyncSession, action: str, **kw) -> AuditLog:
    row = AuditLog(
        request_id=kw.pop("request_id", str(uuid.uuid4())),
        actor=kw.pop("actor", "someone@example.com"),
        action=action,
        detail=kw.pop("detail", {}),
        ip_address=kw.pop("ip_address", None),
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(row)
    await db.flush()
    return row


async def test_logs_link_missing_ips_to_real_values(db_session: AsyncSession):
    user = await make_user(db_session, status="active")
    now = datetime.now(UTC)
    auth = AuthSession(
        id=uuid.uuid4(),
        id_hash=uuid.uuid4().hex,
        user_id=user.id,
        expires_at=now + timedelta(hours=1),
        idle_expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address="198.51.100.4",
    )
    db_session.add(auth)
    login = await _audit_row(
        db_session, "login_success", detail={"session_id": str(auth.id)}
    )
    request_id = str(uuid.uuid4())
    await _audit_row(db_session, "query", request_id=request_id, ip_address=CLIENT_IP)
    error = await _audit_row(db_session, "server_error", request_id=request_id)
    job = await _audit_row(db_session, "online_eval", request_id="system")
    recorded = await _audit_row(db_session, "usage_export", ip_address="192.0.2.9")

    items, _ = await logs.fetch_items(db_session, logs.LogFilters(), None, 50)
    by_id = {item["id"]: item for item in items}

    def ip(row: AuditLog) -> tuple[str | None, str | None]:
        item = by_id[str(row.id)]
        return item["ip_address"], item["ip_source"]

    assert ip(login) == ("198.51.100.4", "session")
    assert ip(error) == (CLIENT_IP, "request")
    assert ip(job) == (None, None)
    assert ip(recorded) == ("192.0.2.9", "recorded")
    # Display only: the stored rows are untouched.
    await db_session.refresh(error)
    assert error.ip_address is None


async def test_hidden_actions_never_lend_their_ip(db_session: AsyncSession):
    request_id = str(uuid.uuid4())
    await _audit_row(
        db_session, "login_success", request_id=request_id, ip_address=CLIENT_IP
    )
    error = await _audit_row(db_session, "server_error", request_id=request_id)
    hidden = frozenset(e.value for e in AuthEvent)
    items, _ = await logs.fetch_items(
        db_session, logs.LogFilters(hidden_actions=hidden), None, 50
    )
    item = next(i for i in items if i["id"] == str(error.id))
    assert (item["ip_address"], item["ip_source"]) == (None, None)
