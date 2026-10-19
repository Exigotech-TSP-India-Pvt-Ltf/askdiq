"""RBAC: permission table (B2.3.2), dependency family (B2.3.3), CSRF (B2.3.5),
IDOR (B2.3.4) and the proxy contract (B2.5) with real sessions in Postgres."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    UserStatus,
    permissions_for,
)
from app.auth.principal import AuthUser
from app.auth.sessions import SessionService
from app.models.db_models import User
from tests.auth_helpers import make_user, session_headers

# ---------------------------------------------------------------------------
# B2.3.2 permission matrix, generated from newplan §7.3
# ---------------------------------------------------------------------------

# (capability, permissions, operator, admin, superadmin)
SECTION_7_3 = [
    ("Dashboard, Usage, Governance, ... (view)", {Permission.DASHBOARD_READ}, 1, 1, 1),
    ("Documents: upload, delete", {Permission.DOCUMENTS_WRITE}, 0, 1, 1),
    (
        "Feedback: triage, promote to test, export",
        {Permission.FEEDBACK_TRIAGE, Permission.FEEDBACK_EXPORT},
        0,
        1,
        1,
    ),
    ("Evaluation: run, promote cases", {Permission.EVAL_RUN}, 0, 1, 1),
    (
        "Test-IP configuration, usage export",
        {Permission.TESTIP_WRITE, Permission.USAGE_EXPORT},
        0,
        1,
        1,
    ),
    ("User Management", {Permission.USERS_MANAGE}, 0, 0, 1),
    ("View the auth audit trail", {Permission.AUDIT_AUTH_READ}, 0, 0, 1),
    # planchanges4 B2.3.2: superadmin also owns branding and taxonomy.
    ("Branding", {Permission.BRANDING_WRITE}, 0, 0, 1),
    ("Taxonomy", {Permission.TAXONOMY_WRITE}, 0, 0, 1),
    ("Models and pricing", {Permission.MODELS_WRITE}, 0, 0, 1),
]
_ROLE_COLUMNS = (Role.OPERATOR, Role.ADMIN, Role.SUPERADMIN)
MATRIX = {
    (role, perm): bool(row[2 + i])
    for row in SECTION_7_3
    for perm in row[1]
    for i, role in enumerate(_ROLE_COLUMNS)
}


def test_matrix_covers_every_permission_and_role() -> None:
    assert {p for _, p in MATRIX} == set(Permission)
    assert set(ROLE_PERMISSIONS) == set(Role)


@pytest.mark.parametrize(
    ("role", "permission"),
    sorted(MATRIX, key=lambda k: (k[0].value, k[1].value)),
    ids=str,
)
def test_role_permission_matrix(role: Role, permission: Permission) -> None:
    assert (permission in ROLE_PERMISSIONS[role]) is MATRIX[(role, permission)]
    assert (permission in permissions_for(role.value)) is MATRIX[(role, permission)]


def test_unknown_or_missing_role_grants_nothing() -> None:
    assert permissions_for(None) == frozenset()
    assert permissions_for("root") == frozenset()


@pytest.mark.parametrize(
    "status", [UserStatus.PENDING, UserStatus.REJECTED, UserStatus.SUSPENDED]
)
def test_only_active_users_hold_permissions(status: UserStatus) -> None:
    user = AuthUser(
        id=uuid.uuid4(),
        email="x@example.com",
        name="x",
        role=Role.SUPERADMIN,
        status=status,
        version=1,
    )
    assert user.permissions == frozenset()
    assert not user.has(Permission.DASHBOARD_READ)


# ---------------------------------------------------------------------------
# B2.3.3 dependency family with real sessions (Postgres, runs in CI)
# ---------------------------------------------------------------------------

DASHBOARD_ROUTE = "/admin/usage/test-ips"
CHAT_ROUTE = "/sessions"


def _doc_url() -> str:
    return f"/documents/{uuid.uuid4()}"


@pytest.mark.real_auth
async def test_no_session_is_401(client: AsyncClient) -> None:
    response = await client.get(DASHBOARD_ROUTE)
    assert response.status_code == 401
    response = await client.get(CHAT_ROUTE, headers={"X-Session": "forged-token"})
    assert response.status_code == 401


@pytest.mark.real_auth
async def test_pending_user_gets_chat_but_not_dashboard(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, status="pending")
    headers = await session_headers(db_session, user)
    assert (await client.get(DASHBOARD_ROUTE, headers=headers)).status_code == 403
    assert (await client.get(CHAT_ROUTE, headers=headers)).status_code == 200


@pytest.mark.real_auth
async def test_rejected_user_still_gets_chat(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, status="rejected")
    headers = await session_headers(db_session, user)
    assert (await client.get(CHAT_ROUTE, headers=headers)).status_code == 200
    assert (await client.get(DASHBOARD_ROUTE, headers=headers)).status_code == 403


@pytest.mark.real_auth
async def test_suspended_user_is_denied_everywhere(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, role="superadmin", status="suspended")
    headers = await session_headers(db_session, user)
    for path in (CHAT_ROUTE, DASHBOARD_ROUTE):
        response = await client.get(path, headers=headers)
        assert response.status_code == 403
        assert response.json()["detail"] == "Account suspended."


@pytest.mark.real_auth
async def test_wrong_role_is_403_and_right_role_passes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    operator = await make_user(db_session, role="operator", status="active")
    admin = await make_user(db_session, role="admin", status="active")
    op_headers = await session_headers(db_session, operator)
    admin_headers = await session_headers(db_session, admin)

    assert (await client.get(DASHBOARD_ROUTE, headers=op_headers)).status_code == 200
    assert (await client.delete(_doc_url(), headers=op_headers)).status_code == 403
    # Authorised: reaches the handler, which reports the missing document.
    assert (await client.delete(_doc_url(), headers=admin_headers)).status_code == 404


@pytest.mark.real_auth
async def test_suspension_takes_effect_on_the_next_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, role="operator", status="active")
    headers = await session_headers(db_session, user)
    assert (await client.get(DASHBOARD_ROUTE, headers=headers)).status_code == 200

    await db_session.execute(
        update(User).where(User.id == user.id).values(status="suspended", version=2)
    )
    await db_session.commit()
    assert (await client.get(DASHBOARD_ROUTE, headers=headers)).status_code == 403


@pytest.mark.real_auth
async def test_role_change_takes_effect_on_the_next_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, role="operator", status="active")
    headers = await session_headers(db_session, user)
    assert (await client.delete(_doc_url(), headers=headers)).status_code == 403

    await db_session.execute(
        update(User).where(User.id == user.id).values(role="admin", version=2)
    )
    await db_session.commit()
    assert (await client.delete(_doc_url(), headers=headers)).status_code == 404


@pytest.mark.real_auth
async def test_revoked_session_is_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, role="operator", status="active")
    headers = await session_headers(db_session, user)
    await SessionService(db_session).revoke(headers["X-Session"])
    await db_session.commit()
    assert (await client.get(DASHBOARD_ROUTE, headers=headers)).status_code == 401


@pytest.mark.real_auth
async def test_missing_service_key_is_401_even_with_valid_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """B2.5: the backend only answers our proxy."""
    user = await make_user(db_session, role="superadmin", status="active")
    headers = await session_headers(db_session, user)
    for key in ("", "wrong-service-key"):
        response = await client.get(
            DASHBOARD_ROUTE, headers={**headers, "X-API-Key": key}
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# B2.3.5 CSRF on cookie-authenticated state changes
# ---------------------------------------------------------------------------


@pytest.mark.real_auth
@pytest.mark.parametrize(
    "csrf_headers",
    [
        {},
        {"X-Requested-With": "askdiq"},
        {"X-Requested-With": "askdiq", "Origin": "https://evil.example.com"},
        {"X-Requested-With": "XMLHttpRequest", "Origin": "http://localhost:3000"},
        {"Origin": "http://localhost:3000"},
    ],
    ids=["none", "no-origin", "cross-origin", "wrong-marker", "no-marker"],
)
async def test_cross_site_post_is_rejected(
    client: AsyncClient, db_session: AsyncSession, csrf_headers: dict[str, str]
) -> None:
    user = await make_user(db_session, status="pending")
    headers = await session_headers(db_session, user, csrf=False)
    response = await client.post(
        CHAT_ROUTE, json={"title": "x"}, headers={**headers, **csrf_headers}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Cross-site request rejected."


@pytest.mark.real_auth
async def test_same_origin_post_passes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, status="pending")
    headers = await session_headers(db_session, user)
    response = await client.post(CHAT_ROUTE, json={"title": "ok"}, headers=headers)
    assert response.status_code == 201


@pytest.mark.real_auth
async def test_safe_methods_need_no_csrf_headers(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_user(db_session, status="pending")
    headers = await session_headers(db_session, user, csrf=False)
    assert (await client.get(CHAT_ROUTE, headers=headers)).status_code == 200


# ---------------------------------------------------------------------------
# B2.3.4 IDOR: chat sessions are owned by the signed-in user
# ---------------------------------------------------------------------------


@pytest.mark.real_auth
async def test_pending_user_cannot_read_another_users_chat_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await make_user(db_session, status="pending")
    other = await make_user(db_session, status="pending")
    owner_headers = await session_headers(db_session, owner)
    other_headers = await session_headers(db_session, other)

    created = await client.post(
        CHAT_ROUTE, json={"title": "private"}, headers=owner_headers
    )
    assert created.status_code == 201
    session_id = created.json()["id"]
    path = f"{CHAT_ROUTE}/{session_id}"

    assert (await client.get(path, headers=owner_headers)).status_code == 200
    assert (await client.get(path, headers=other_headers)).status_code == 404
    rename = await client.patch(path, json={"title": "pwned"}, headers=other_headers)
    assert rename.status_code == 404
    assert (await client.delete(path, headers=other_headers)).status_code == 404
    listed = await client.get(CHAT_ROUTE, headers=other_headers)
    assert session_id not in {s["id"] for s in listed.json()}


@pytest.mark.real_auth
async def test_client_id_header_cannot_impersonate_another_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await make_user(db_session, status="pending")
    other = await make_user(db_session, status="pending")
    owner_headers = await session_headers(db_session, owner)
    other_headers = await session_headers(db_session, other)
    created = await client.post(
        CHAT_ROUTE, json={"title": "mine"}, headers=owner_headers
    )
    path = f"{CHAT_ROUTE}/{created.json()['id']}"
    spoofed = {**other_headers, "X-Client-Id": owner.email}
    assert (await client.get(path, headers=spoofed)).status_code == 404
