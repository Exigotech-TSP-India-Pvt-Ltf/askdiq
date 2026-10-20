"""`/auth/*` routes against the in-process mock IdP.

Real sessions in Postgres (marker ``real_auth``; runs in CI). Covers: first
login creates a pending user, SUPERADMIN_EMAILS bootstrap, upsert on ``oid``
and session rotation, invite linking, suspended/identity-conflict denial,
replayed state (400), invalid token (401), logout revoking the server
session, the per-IP login rate limit (429), access requests, ``/auth/me``
caching, and an audit row for every step.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.audit import AUTH_AUDIT_ACTIONS, AuthEvent
from app.auth.mock_idp import MockIdentity
from app.auth.oidc import TokenValidationError, get_oidc_client
from app.auth.permissions import Permission
from app.core.config import get_settings
from app.models.db_models import AccessRequest, AuditLog, AuthSession, User
from main import app
from tests.auth_helpers import (
    authorize,
    make_user,
    mock_idp_sign_in,
    proxy_headers,
    session_headers,
    signed_in,
    sso_sign_in,
)

pytestmark = pytest.mark.real_auth

BOSS = "boss@example.com"
DASHBOARD_ROUTE = "/admin/usage/test-ips"


@pytest.fixture(autouse=True)
async def mock_idp(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "superadmin_emails", BOSS)
    monkeypatch.setattr(settings, "access_contact_email", "it-help@example.com")
    async with mock_idp_sign_in():
        yield


def new_identity(**kwargs: Any) -> MockIdentity:
    email = kwargs.pop("email", f"u-{uuid.uuid4().hex[:10]}@example.com")
    return MockIdentity(email=email, name=kwargs.pop("name", "Test User"), **kwargs)


async def audit_rows(
    db: AsyncSession, action: str, user_id: uuid.UUID | None = None
) -> list[AuditLog]:
    rows = (
        (await db.execute(select(AuditLog).where(AuditLog.action == action)))
        .scalars()
        .all()
    )
    if user_id is None:
        return list(rows)
    return [r for r in rows if (r.detail or {}).get("target_user_id") == str(user_id)]


async def user_by_email(db: AsyncSession, email: str) -> User:
    stmt = select(User).where(User.email == email)
    return (
        await db.execute(stmt.execution_options(populate_existing=True))
    ).scalar_one()


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------


async def test_first_login_creates_a_pending_user(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    identity = new_identity()
    response = await sso_sign_in(client, identity)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["cookie_name"] == get_settings().session_cookie_name
    assert body["me"]["status"] == "pending"
    assert body["me"]["role"] is None
    assert body["me"]["permissions"] == []
    assert body["me"]["access_contact_email"] == "it-help@example.com"

    user = await user_by_email(db_session, identity.email)
    assert user.entra_oid == identity.oid
    assert user.tenant_id == get_settings().entra_tenant_id
    assert user.status == "pending"
    assert user.last_login_at is not None
    # Only a hash of the session id is stored.
    sessions = (
        (
            await db_session.execute(
                select(AuthSession).where(AuthSession.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(sessions) == 1
    assert sessions[0].id_hash != body["session"]

    [event] = await audit_rows(db_session, AuthEvent.LOGIN_SUCCESS, user.id)
    assert event.actor == identity.email
    assert event.detail["created"] is True
    assert event.detail["status"] == "pending"
    # Target emails in audit details are masked.
    assert identity.email not in str(event.detail)


async def test_superadmin_email_becomes_an_active_superadmin(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    body = await signed_in(client, new_identity(email=BOSS))
    assert body["me"]["status"] == "active"
    assert body["me"]["role"] == "superadmin"
    assert Permission.USERS_MANAGE in body["me"]["permissions"]
    user = await user_by_email(db_session, BOSS)
    [event] = await audit_rows(db_session, AuthEvent.LOGIN_SUCCESS, user.id)
    assert event.detail["superadmin_bootstrap"] is True
    users = await client.get("/admin/users-accounts", headers=body["headers"])
    assert users.status_code == 200


async def test_bootstrap_never_reactivates_a_suspended_superadmin_email(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    identity = new_identity(email=BOSS)
    await signed_in(client, identity)
    await db_session.execute(
        update(User).where(User.email == BOSS).values(status="suspended")
    )
    await db_session.commit()
    response = await sso_sign_in(client, identity)
    assert response.status_code == 403


async def test_relogin_upserts_on_oid_and_rotates_the_session(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    identity = new_identity()
    first = await signed_in(client, identity)
    identity.name = "Renamed User"
    second = await sso_sign_in(
        client, identity, headers={"X-Session": first["session"]}
    )
    assert second.status_code == 200
    rows = (
        (await db_session.execute(select(User).where(User.entra_oid == identity.oid)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].full_name == "Renamed User"
    # The old session id was revoked (rotation on login).
    old = await client.get("/auth/me", headers=first["headers"])
    assert old.status_code == 401
    new = await client.get("/auth/me", headers=proxy_headers(second.json()["session"]))
    assert new.status_code == 200


async def test_invited_user_is_linked_on_first_sign_in(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    email = f"invited-{uuid.uuid4().hex[:8]}@example.com"
    db_session.add(
        User(full_name="Invited", email=email, role="operator", status="active")
    )
    await db_session.commit()
    identity = new_identity(email=email)
    body = await signed_in(client, identity)
    assert body["me"]["status"] == "active"
    assert body["me"]["role"] == "operator"
    user = await user_by_email(db_session, email)
    assert user.entra_oid == identity.oid


async def test_email_owned_by_another_identity_is_denied(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    existing = await make_user(db_session, status="active", role="admin")
    await db_session.commit()
    response = await sso_sign_in(client, new_identity(email=existing.email))
    assert response.status_code == 403
    assert response.json()["detail"] == "Sign-in denied."
    denied = [
        r
        for r in await audit_rows(db_session, AuthEvent.LOGIN_DENIED)
        if r.detail.get("reason") == "identity_conflict" and r.actor == existing.email
    ]
    assert len(denied) == 1


async def test_suspended_user_is_denied_without_a_session(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    identity = new_identity()
    await signed_in(client, identity)
    user = await user_by_email(db_session, identity.email)
    user.status = "suspended"
    await db_session.commit()

    response = await sso_sign_in(client, identity)
    assert response.status_code == 403
    body = response.json()
    assert body["detail"] == "Account suspended."
    assert body["access_contact_email"] == "it-help@example.com"
    assert "session" not in body
    sessions = (
        (
            await db_session.execute(
                select(AuthSession).where(AuthSession.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(sessions) == 1  # only the first (pre-suspension) sign-in
    [denied] = await audit_rows(db_session, AuthEvent.LOGIN_DENIED, user.id)
    assert denied.detail["reason"] == "suspended"


async def test_replayed_state_is_400_and_audited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    body = await authorize(client, new_identity())
    first = await client.post("/auth/sso/callback", json=body)
    assert first.status_code == 200
    replay = await client.post("/auth/sso/callback", json=body)
    assert replay.status_code == 400
    assert replay.headers["content-type"].startswith("application/problem+json")
    reasons = [
        r.detail.get("reason")
        for r in await audit_rows(db_session, AuthEvent.LOGIN_DENIED)
    ]
    assert "invalid_state" in reasons


async def test_invalid_id_token_is_401_and_audited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    class RejectingClient:
        async def complete_login(self, code: str, state: str) -> None:
            raise TokenValidationError("wrong tenant")

    app.dependency_overrides[get_oidc_client] = RejectingClient
    response = await client.post(
        "/auth/sso/callback", json={"code": "abc", "state": "s" * 32}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Sign-in failed."
    reasons = [
        r.detail.get("reason")
        for r in await audit_rows(db_session, AuthEvent.LOGIN_DENIED)
    ]
    assert "invalid_token: wrong tenant" in reasons


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"code": "abc"},
        {"code": "abc", "state": "short"},
        {"code": "abc", "state": "s" * 32 + "'; DROP TABLE users;--"},
        {"code": "", "state": "s" * 32},
        {"code": "abc", "state": "s" * 32, "extra": 1},
    ],
)
async def test_callback_validates_its_body(
    client: httpx.AsyncClient, body: dict[str, Any]
) -> None:
    response = await client.post("/auth/sso/callback", json=body)
    assert response.status_code == 422


async def test_sso_routes_need_the_service_key(client: httpx.AsyncClient) -> None:
    response = await client.get("/auth/sso/login", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


async def test_login_rate_limit_gives_429(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "auth_login_rate_limit_per_minute", 2)
    assert (await client.get("/auth/sso/login")).status_code == 200
    assert (await client.get("/auth/sso/login")).status_code == 200
    limited = await client.get("/auth/sso/login")
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0
    # The callback shares the per-IP budget.
    callback = await client.post(
        "/auth/sso/callback", json={"code": "abc", "state": "s" * 32}
    )
    assert callback.status_code == 429


# ---------------------------------------------------------------------------
# /auth/me and logout
# ---------------------------------------------------------------------------


async def test_me_requires_a_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401
    forged = await client.get("/auth/me", headers={"X-Session": "forged"})
    assert forged.status_code == 401


async def test_me_is_cached_with_an_etag(client: httpx.AsyncClient) -> None:
    body = await signed_in(client, new_identity())
    first = await client.get("/auth/me", headers=body["headers"])
    assert first.status_code == 200
    assert first.json()["user"]["email"] == body["me"]["user"]["email"]
    assert "X-Session" in first.headers["vary"]
    etag = first.headers["etag"]
    again = await client.get(
        "/auth/me", headers={**body["headers"], "If-None-Match": etag}
    )
    assert again.status_code == 304


async def test_logout_revokes_the_server_session(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    identity = new_identity()
    body = await signed_in(client, identity)
    response = await client.post("/auth/logout", headers=body["headers"])
    assert response.status_code == 204
    assert (await client.get("/auth/me", headers=body["headers"])).status_code == 401
    user = await user_by_email(db_session, identity.email)
    session = (
        await db_session.execute(
            select(AuthSession).where(AuthSession.user_id == user.id)
        )
    ).scalar_one()
    assert session.revoked_at is not None
    [event] = await audit_rows(db_session, AuthEvent.LOGOUT, user.id)
    assert event.detail["revoked"] is True


async def test_logout_needs_the_csrf_marker(client: httpx.AsyncClient) -> None:
    body = await signed_in(client, new_identity())
    response = await client.post("/auth/logout", headers={"X-Session": body["session"]})
    assert response.status_code == 403
    cross_site = await client.post(
        "/auth/logout",
        headers={**body["headers"], "Origin": "https://evil.example.com"},
    )
    assert cross_site.status_code == 403


# ---------------------------------------------------------------------------
# Access requests
# ---------------------------------------------------------------------------


async def test_pending_user_requests_access(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    body = await signed_in(client, new_identity())
    headers = body["headers"]
    status_before = await client.get("/auth/access-request", headers=headers)
    assert status_before.status_code == 200
    assert status_before.json()["request"] is None
    assert status_before.json()["user_status"] == "pending"

    created = await client.post(
        "/auth/access-request",
        json={"requested_role": "operator", "justification": "Need the dashboards"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "pending"

    after = (await client.get("/auth/access-request", headers=headers)).json()
    assert after["request"]["id"] == created.json()["id"]
    assert after["access_contact_email"] == "it-help@example.com"

    duplicate = await client.post("/auth/access-request", json={}, headers=headers)
    assert duplicate.status_code == 409

    user_id = uuid.UUID(body["me"]["user"]["id"])
    [event] = await audit_rows(db_session, AuthEvent.ACCESS_REQUESTED, user_id)
    assert event.detail["requested_role"] == "operator"
    # Dashboard access is still denied until a superadmin approves.
    assert (await client.get(DASHBOARD_ROUTE, headers=headers)).status_code == 403


async def test_rejected_user_can_ask_again(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, status="rejected")
    headers = await session_headers(db_session, user)
    response = await client.post("/auth/access-request", json={}, headers=headers)
    assert response.status_code == 201
    refreshed = await db_session.get(User, user.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == "pending"


async def test_active_user_cannot_request_access(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, status="active", role="operator")
    user_id = user.id  # the 409 rolls the session back (expires loaded rows)
    headers = await session_headers(db_session, user)
    response = await client.post("/auth/access-request", json={}, headers=headers)
    assert response.status_code == 409
    rows = (
        (
            await db_session.execute(
                select(AccessRequest).where(AccessRequest.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


async def test_suspended_user_cannot_request_access(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, status="suspended")
    headers = await session_headers(db_session, user)
    response = await client.post("/auth/access-request", json={}, headers=headers)
    assert response.status_code == 403
    assert (
        await client.get("/auth/access-request", headers=headers)
    ).status_code == 403
    # /auth/me still answers so the UI can show the suspended page.
    me = await client.get("/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["status"] == "suspended"


@pytest.mark.parametrize(
    "payload",
    [
        {"requested_role": "root"},
        {"justification": "x" * 1001},
        {"justification": "bad\x00byte"},
        {"unexpected": True},
    ],
)
async def test_access_request_validation(
    client: httpx.AsyncClient, db_session: AsyncSession, payload: dict[str, Any]
) -> None:
    user = await make_user(db_session, status="pending")
    headers = await session_headers(db_session, user)
    response = await client.post("/auth/access-request", json=payload, headers=headers)
    assert response.status_code == 422


async def test_access_request_needs_a_session_and_csrf(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    assert (await client.post("/auth/access-request", json={})).status_code == 401
    user = await make_user(db_session, status="pending")
    headers = await session_headers(db_session, user, csrf=False)
    response = await client.post("/auth/access-request", json={}, headers=headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Auth audit events are superadmin-only on the governance views
# ---------------------------------------------------------------------------


async def test_auth_audit_events_need_audit_auth_read(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await signed_in(client, new_identity())
    operator = await make_user(db_session, status="active", role="operator")
    operator_headers = await session_headers(db_session, operator)
    boss = await make_user(db_session, status="active", role="superadmin")
    boss_headers = await session_headers(db_session, boss)

    hidden = await client.get(
        "/governance/audit-log",
        params={"action": "login_success", "limit": 500},
        headers=operator_headers,
    )
    assert hidden.status_code == 200
    assert hidden.json() == []
    everything = await client.get(
        "/governance/audit-log", params={"limit": 500}, headers=operator_headers
    )
    assert not {e["action"] for e in everything.json()} & AUTH_AUDIT_ACTIONS
    logs = await client.get(
        "/admin/governance/logs", params={"limit": 200}, headers=operator_headers
    )
    assert logs.status_code == 200
    assert not {i["action"] for i in logs.json()["items"]} & AUTH_AUDIT_ACTIONS

    visible = await client.get(
        "/governance/audit-log",
        params={"action": "login_success", "limit": 500},
        headers=boss_headers,
    )
    assert visible.status_code == 200
    assert len(visible.json()) >= 1

    # category=auth keeps only auth events (they are not crowded out of
    # `limit` by query rows), and stays empty without audit:auth:read.
    db_session.add(AuditLog(request_id="r-cat", actor="x@example.com", action="query"))
    await db_session.commit()
    auth_only = await client.get(
        "/governance/audit-log",
        params={"category": "auth", "limit": 500},
        headers=boss_headers,
    )
    assert auth_only.status_code == 200
    actions = {e["action"] for e in auth_only.json()}
    assert actions
    assert actions <= AUTH_AUDIT_ACTIONS
    operator_auth = await client.get(
        "/governance/audit-log",
        params={"category": "auth"},
        headers=operator_headers,
    )
    assert operator_auth.json() == []
    bad = await client.get(
        "/governance/audit-log", params={"category": "nope"}, headers=boss_headers
    )
    assert bad.status_code == 422
