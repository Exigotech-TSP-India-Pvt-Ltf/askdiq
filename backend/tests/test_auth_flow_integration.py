"""Phase B2 end-to-end journey through the mock IdP.

A new user signs in (pending), uses Chat, gets 403 on the dashboard,
requests access; a superadmin (bootstrapped from SUPERADMIN_EMAILS) approves;
the user's next request is authorised without signing in again; the user is
suspended and the next request is denied; signing in again is refused. The
auth audit trail records every step and only the superadmin can read it.
Real sessions in Postgres (marker ``real_auth``; runs in CI).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest

from app.auth.mock_idp import MockIdentity
from app.auth.permissions import Permission
from app.core.config import get_settings
from tests.auth_helpers import mock_idp_sign_in, signed_in, sso_sign_in

pytestmark = pytest.mark.real_auth

BOSS = "journey-boss@example.com"
DASHBOARD = "/admin/usage/test-ips"
CHAT = "/sessions"


@pytest.fixture(autouse=True)
async def mock_idp(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    monkeypatch.setattr(get_settings(), "superadmin_emails", BOSS)
    monkeypatch.setattr(get_settings(), "access_contact_email", "it-help@example.com")
    async with mock_idp_sign_in():
        yield


async def test_full_sign_in_approval_and_suspension_journey(
    client: httpx.AsyncClient,
) -> None:
    # 1. A new user signs in through Entra (mock) and is pending.
    newcomer = MockIdentity(
        email=f"newcomer-{uuid.uuid4().hex[:8]}@example.com", name="New Comer"
    )
    user = await signed_in(client, newcomer)
    headers = user["headers"]
    me = (await client.get("/auth/me", headers=headers)).json()
    assert (me["status"], me["role"], me["permissions"]) == ("pending", None, [])
    assert me["access_contact_email"] == "it-help@example.com"

    # 2. Chat works; every dashboard route answers 403.
    assert (await client.get(CHAT, headers=headers)).status_code == 200
    created = await client.post(CHAT, json={"title": "hello"}, headers=headers)
    assert created.status_code == 201
    denied = await client.get(DASHBOARD, headers=headers)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Dashboard access requires approval."
    assert (
        await client.get("/admin/users-accounts", headers=headers)
    ).status_code == 403

    # 3. The user requests access.
    requested = await client.post(
        "/auth/access-request",
        json={"requested_role": "operator", "justification": "On-call rota"},
        headers=headers,
    )
    assert requested.status_code == 201
    request_id = requested.json()["id"]

    # 4. A superadmin from SUPERADMIN_EMAILS signs in and approves it.
    boss = await signed_in(client, MockIdentity(email=BOSS, name="Boss"))
    boss_headers = boss["headers"]
    assert boss["me"]["role"] == "superadmin"
    queue = (await client.get("/admin/access-requests", headers=boss_headers)).json()
    assert request_id in {item["id"] for item in queue["items"]}
    approved = await client.post(
        f"/admin/access-requests/{request_id}/approve",
        json={"role": "operator"},
        headers=boss_headers,
    )
    assert approved.status_code == 200

    # 5. The user's very next request is authorised (same session).
    assert (await client.get(DASHBOARD, headers=headers)).status_code == 200
    me = (await client.get("/auth/me", headers=headers)).json()
    assert (me["status"], me["role"]) == ("active", "operator")
    assert me["permissions"] == [Permission.DASHBOARD_READ]
    # An operator still cannot manage users or do admin-level writes.
    assert (
        await client.get("/admin/users-accounts", headers=headers)
    ).status_code == 403
    assert (await client.post(DASHBOARD, json={}, headers=headers)).status_code == 403
    status_now = (await client.get("/auth/access-request", headers=headers)).json()
    assert status_now["request"]["status"] == "approved"

    # 6. The superadmin suspends the user; the next request is denied.
    user_id = user["me"]["user"]["id"]
    account = (
        await client.get(f"/admin/users-accounts/{user_id}", headers=boss_headers)
    ).json()
    suspended = await client.patch(
        f"/admin/users-accounts/{user_id}",
        json={"version": account["version"], "status": "suspended"},
        headers=boss_headers,
    )
    assert suspended.status_code == 200
    # Suspension revokes every session at once: the old cookie is dead.
    assert (await client.get(DASHBOARD, headers=headers)).status_code == 401
    assert (await client.get(CHAT, headers=headers)).status_code == 401
    assert (await client.get("/auth/me", headers=headers)).status_code == 401

    # 7. Signing in again is refused while suspended.
    again = await sso_sign_in(client, newcomer)
    assert again.status_code == 403
    assert again.json()["access_contact_email"] == "it-help@example.com"

    # 8. The auth audit trail has every step, visible to the superadmin only.
    trail = await client.get(
        "/governance/audit-log", params={"limit": 500}, headers=boss_headers
    )
    assert trail.status_code == 200
    mine = [
        e["action"]
        for e in trail.json()
        if (e["detail"] or {}).get("target_user_id") == user_id
    ]
    for action in (
        "login_success",
        "access_requested",
        "access_approved",
        "user_suspended",
        "login_denied",
    ):
        assert action in mine, (action, mine)

    # 9. The superadmin signs out; the session is dead server-side.
    assert (await client.post("/auth/logout", headers=boss_headers)).status_code == 204
    assert (await client.get("/auth/me", headers=boss_headers)).status_code == 401


async def test_dashboard_is_unreachable_without_the_proxy_key(
    client: httpx.AsyncClient,
) -> None:
    """A01: a valid session alone cannot reach the API directly."""
    boss = await signed_in(client, MockIdentity(email=BOSS, name="Boss"))
    response = await client.get(
        DASHBOARD, headers={**boss["headers"], "X-API-Key": "not-the-key"}
    )
    assert response.status_code == 401
