"""User Management API (planchanges4 B2.4.2): `/admin/users-accounts` and
`/admin/access-requests`.

Real sessions in Postgres (marker ``real_auth``; runs in CI). Covers list
filters and validation, PATCH with optimistic concurrency, the self-change
and last-superadmin safeguards (409), revoke sessions, invite, approve /
reject / bulk approve (all-or-nothing), two concurrent approvals (exactly
one wins), the stats endpoint, and an audit event for every action.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.admin_service import UserAdminService, UserChange
from app.auth.audit import AuditContext, AuthEvent
from app.auth.permissions import Role, UserStatus
from app.auth.principal import AuthUser
from app.auth.sessions import to_auth_user
from app.core.exceptions import ConflictError
from app.models.db_models import AccessRequest, AuditLog, AuthSession, User
from tests.auth_helpers import make_user, session_headers
from tests.pg_helpers import new_engine, require_postgres

pytestmark = pytest.mark.real_auth

USERS = "/admin/users-accounts"
REQUESTS = "/admin/access-requests"


@pytest.fixture
async def boss(db_session: AsyncSession) -> User:
    return await make_user(db_session, role="superadmin", status="active")


@pytest.fixture
async def boss_headers(db_session: AsyncSession, boss: User) -> dict[str, str]:
    return await session_headers(db_session, boss)


async def pending_request(
    db: AsyncSession, *, requested_role: str | None = "operator"
) -> tuple[User, AccessRequest]:
    user = await make_user(db, status="pending")
    request = AccessRequest(
        id=uuid.uuid4(),
        user_id=user.id,
        requested_role=requested_role,
        status="pending",
    )
    db.add(request)
    await db.commit()
    return user, request


async def reload(db: AsyncSession, model: type, key: uuid.UUID) -> Any:
    # populate_existing: fresh values without expiring other objects (an
    # expired attribute would need a lazy load, which async sessions forbid).
    return await db.get(model, key, populate_existing=True)


async def audit_actions(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    rows = (await db.execute(select(AuditLog))).scalars().all()
    return [
        r.action for r in rows if (r.detail or {}).get("target_user_id") == str(user_id)
    ]


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "status", "expected"),
    [
        (None, "pending", 403),
        ("operator", "active", 403),
        ("admin", "active", 403),
        ("superadmin", "suspended", 403),
        ("superadmin", "active", 200),
    ],
)
async def test_only_active_superadmins_manage_users(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    role: str | None,
    status: str,
    expected: int,
) -> None:
    user = await make_user(db_session, role=role, status=status)
    headers = await session_headers(db_session, user)
    for path in (USERS, f"{USERS}/stats", REQUESTS):
        assert (await client.get(path, headers=headers)).status_code == expected
    assert (await client.get(USERS)).status_code == 401


# ---------------------------------------------------------------------------
# List / detail
# ---------------------------------------------------------------------------


async def test_list_search_filters_and_pagination(
    client: httpx.AsyncClient, db_session: AsyncSession, boss_headers: dict[str, str]
) -> None:
    tag = uuid.uuid4().hex[:8]
    for i in range(3):
        await make_user(
            db_session, email=f"list_{tag}_{i}@example.com", status="pending"
        )
    await make_user(
        db_session, email=f"list_{tag}_op@example.com", status="active", role="operator"
    )
    await db_session.commit()

    page = await client.get(
        USERS, params={"search": f"list_{tag}", "limit": 2}, headers=boss_headers
    )
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 4
    assert len(body["items"]) == 2
    assert body["limit"] == 2

    filtered = await client.get(
        USERS,
        params={"search": tag, "role": "operator", "status": "active"},
        headers=boss_headers,
    )
    assert [u["email"] for u in filtered.json()["items"]] == [
        f"list_{tag}_op@example.com"
    ]

    by_email = await client.get(
        USERS,
        params={"search": tag, "sort": "email", "order": "asc"},
        headers=boss_headers,
    )
    emails = [u["email"] for u in by_email.json()["items"]]
    assert emails == sorted(emails)


async def test_search_wildcards_are_literal(
    client: httpx.AsyncClient, db_session: AsyncSession, boss_headers: dict[str, str]
) -> None:
    await make_user(db_session, email=f"pct-{uuid.uuid4().hex[:6]}@example.com")
    await db_session.commit()
    for term in ("%", "_", "\\"):
        response = await client.get(
            USERS, params={"search": term}, headers=boss_headers
        )
        assert response.status_code == 200
        for item in response.json()["items"]:
            assert term in item["email"] or term in item["name"]


@pytest.mark.parametrize(
    "params",
    [
        {"sort": "password_hash"},
        {"sort": "email; DROP TABLE users"},
        {"order": "sideways"},
        {"role": "root"},
        {"status": "deleted"},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"offset": 10_001},
        {"search": "x" * 101},
        {"search": "nul\x00byte"},
    ],
)
async def test_list_rejects_bad_parameters(
    client: httpx.AsyncClient, boss_headers: dict[str, str], params: dict[str, Any]
) -> None:
    response = await client.get(USERS, params=params, headers=boss_headers)
    assert response.status_code == 422


async def test_detail_shows_active_sessions(
    client: httpx.AsyncClient, db_session: AsyncSession, boss_headers: dict[str, str]
) -> None:
    user = await make_user(db_session, status="active", role="operator")
    await session_headers(db_session, user)
    response = await client.get(f"{USERS}/{user.id}", headers=boss_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == user.email
    assert len(body["active_sessions"]) == 1
    missing = await client.get(f"{USERS}/{uuid.uuid4()}", headers=boss_headers)
    assert missing.status_code == 404
    bad = await client.get(f"{USERS}/not-a-uuid", headers=boss_headers)
    assert bad.status_code == 422


# ---------------------------------------------------------------------------
# PATCH role / status
# ---------------------------------------------------------------------------


async def test_role_change_bumps_version_and_is_audited(
    client: httpx.AsyncClient, db_session: AsyncSession, boss_headers: dict[str, str]
) -> None:
    user = await make_user(db_session, status="active", role="operator")
    await db_session.commit()
    user_id, version = user.id, user.version
    response = await client.patch(
        f"{USERS}/{user_id}",
        json={"version": version, "role": "admin"},
        headers=boss_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["role"] == "admin"
    assert response.json()["version"] == 2
    assert AuthEvent.ROLE_CHANGED in await audit_actions(db_session, user_id)

    stale = await client.patch(
        f"{USERS}/{user_id}",
        json={"version": 1, "role": "operator"},
        headers=boss_headers,
    )
    assert stale.status_code == 409
    assert (await reload(db_session, User, user_id)).role == "admin"


async def test_role_change_applies_on_the_next_request(
    client: httpx.AsyncClient, db_session: AsyncSession, boss_headers: dict[str, str]
) -> None:
    user = await make_user(db_session, status="active", role="operator")
    headers = await session_headers(db_session, user)
    # Invalid body on purpose: the permission guard answers before validation,
    # so 403 = not allowed, 422 = allowed (and nothing is written).
    admin_only = "/admin/usage/test-ips"
    before = await client.post(admin_only, json={}, headers=headers)
    assert before.status_code == 403
    await client.patch(
        f"{USERS}/{user.id}", json={"version": 1, "role": "admin"}, headers=boss_headers
    )
    after = await client.post(admin_only, json={}, headers=headers)
    assert after.status_code == 422


async def test_suspend_and_reactivate(
    client: httpx.AsyncClient, db_session: AsyncSession, boss_headers: dict[str, str]
) -> None:
    user = await make_user(db_session, status="active", role="operator")
    headers = await session_headers(db_session, user)
    user_id = user.id
    suspended = await client.patch(
        f"{USERS}/{user_id}",
        json={"version": 1, "status": "suspended"},
        headers=boss_headers,
    )
    assert suspended.status_code == 200
    # Takes effect on the very next request, Chat included.
    assert (await client.get("/sessions", headers=headers)).status_code == 403
    reactivated = await client.patch(
        f"{USERS}/{user_id}",
        json={"version": 2, "status": "active"},
        headers=boss_headers,
    )
    assert reactivated.status_code == 200
    assert (await client.get("/sessions", headers=headers)).status_code == 200
    actions = await audit_actions(db_session, user_id)
    assert AuthEvent.USER_SUSPENDED in actions
    assert AuthEvent.USER_REACTIVATED in actions


async def test_activating_needs_a_role_and_closes_the_open_request(
    client: httpx.AsyncClient, db_session: AsyncSession, boss_headers: dict[str, str]
) -> None:
    user, request = await pending_request(db_session)
    user_id, request_id = user.id, request.id
    no_role = await client.patch(
        f"{USERS}/{user_id}",
        json={"version": 1, "status": "active"},
        headers=boss_headers,
    )
    assert no_role.status_code == 409
    ok = await client.patch(
        f"{USERS}/{user_id}",
        json={"version": 1, "status": "active", "role": "operator"},
        headers=boss_headers,
    )
    assert ok.status_code == 200
    assert (await reload(db_session, AccessRequest, request_id)).status == "approved"
    assert AuthEvent.ACCESS_APPROVED in await audit_actions(db_session, user_id)


@pytest.mark.parametrize(
    "change", [{"role": "admin"}, {"status": "suspended"}, {"role": "operator"}]
)
async def test_no_self_demote_or_self_suspend(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    boss: User,
    boss_headers: dict[str, str],
    change: dict[str, str],
) -> None:
    boss_id = boss.id
    response = await client.patch(
        f"{USERS}/{boss_id}", json={"version": 1, **change}, headers=boss_headers
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "You cannot change your own role or status."
    assert (await reload(db_session, User, boss_id)).role == "superadmin"


async def test_last_active_superadmin_cannot_be_removed(
    db_session: AsyncSession,
) -> None:
    """A stale principal (already demoted elsewhere) cannot remove the last one."""
    await db_session.execute(
        update(User)
        .where(User.role == "superadmin", User.status == "active")
        .values(status="suspended")
    )
    last = await make_user(db_session, role="superadmin", status="active")
    stale_actor_row = await make_user(db_session, role="superadmin", status="suspended")
    await db_session.commit()
    last_id = last.id
    actor = AuthUser(
        id=stale_actor_row.id,
        email=stale_actor_row.email,
        name="stale",
        role=Role.SUPERADMIN,
        status=UserStatus.ACTIVE,
        version=1,
    )
    service = UserAdminService(db_session)
    ctx = AuditContext("test-last-superadmin", actor.email, "127.0.0.1")
    for change in (
        UserChange(version=1, role=Role.ADMIN),
        UserChange(version=1, status=UserStatus.SUSPENDED),
    ):
        with pytest.raises(ConflictError) as info:
            await service.update_user(actor, last_id, change, ctx)
        assert info.value.status_code == 409
    refreshed = await reload(db_session, User, last_id)
    assert (refreshed.role, refreshed.status) == ("superadmin", "active")


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1},
        {"role": "admin"},
        {"version": 0, "role": "admin"},
        {"version": 1, "status": "pending"},
        {"version": 1, "role": "root"},
        {"version": 1, "role": "admin", "email": "x@example.com"},
    ],
)
async def test_patch_validation(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    boss_headers: dict[str, str],
    payload: dict[str, Any],
) -> None:
    user = await make_user(db_session, status="active", role="operator")
    await db_session.commit()
    response = await client.patch(
        f"{USERS}/{user.id}", json=payload, headers=boss_headers
    )
    assert response.status_code == 422


async def test_patch_unknown_user_is_404(
    client: httpx.AsyncClient, boss_headers: dict[str, str]
) -> None:
    response = await client.patch(
        f"{USERS}/{uuid.uuid4()}",
        json={"version": 1, "role": "admin"},
        headers=boss_headers,
    )
    assert response.status_code == 404


async def test_patch_needs_csrf(
    client: httpx.AsyncClient, db_session: AsyncSession, boss: User
) -> None:
    headers = await session_headers(db_session, boss, csrf=False)
    user = await make_user(db_session, status="active", role="operator")
    await db_session.commit()
    response = await client.patch(
        f"{USERS}/{user.id}", json={"version": 1, "role": "admin"}, headers=headers
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Revoke sessions / invite
# ---------------------------------------------------------------------------


async def test_revoke_sessions_signs_the_user_out(
    client: httpx.AsyncClient, db_session: AsyncSession, boss_headers: dict[str, str]
) -> None:
    user = await make_user(db_session, status="active", role="operator")
    user_id = user.id
    headers = await session_headers(db_session, user)
    await session_headers(db_session, user)
    response = await client.post(
        f"{USERS}/{user_id}/revoke-sessions", headers=boss_headers
    )
    assert response.status_code == 200
    assert response.json() == {"revoked": 2}
    assert (await client.get("/auth/me", headers=headers)).status_code == 401
    live = (
        (
            await db_session.execute(
                select(AuthSession).where(
                    AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    assert live == []
    assert AuthEvent.SESSION_REVOKED in await audit_actions(db_session, user_id)
    missing = await client.post(
        f"{USERS}/{uuid.uuid4()}/revoke-sessions", headers=boss_headers
    )
    assert missing.status_code == 404


async def test_invite_pre_provisions_a_user(
    client: httpx.AsyncClient, db_session: AsyncSession, boss_headers: dict[str, str]
) -> None:
    email = f"New.Hire-{uuid.uuid4().hex[:6]}@Example.com"
    response = await client.post(
        f"{USERS}/invite",
        json={"email": email, "role": "admin", "name": "New Hire"},
        headers=boss_headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == email.lower()
    assert (body["role"], body["status"]) == ("admin", "active")
    assert AuthEvent.USER_INVITED in await audit_actions(
        db_session, uuid.UUID(body["id"])
    )
    duplicate = await client.post(
        f"{USERS}/invite",
        json={"email": email.upper(), "role": "operator"},
        headers=boss_headers,
    )
    assert duplicate.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "role": "operator"},
        {"email": "a@example.com", "role": "root"},
        {"email": "a@example.com"},
        {"email": "a@example.com", "role": "operator", "status": "active"},
    ],
)
async def test_invite_validation(
    client: httpx.AsyncClient, boss_headers: dict[str, str], payload: dict[str, Any]
) -> None:
    response = await client.post(f"{USERS}/invite", json=payload, headers=boss_headers)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Access requests
# ---------------------------------------------------------------------------


async def test_queue_approve_and_history(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    boss: User,
    boss_headers: dict[str, str],
) -> None:
    user, request = await pending_request(db_session)
    user_id, request_id, boss_id, boss_email = user.id, request.id, boss.id, boss.email
    queue = await client.get(REQUESTS, headers=boss_headers)
    assert queue.status_code == 200
    item = next(i for i in queue.json()["items"] if i["id"] == str(request_id))
    assert item["user_email"] == user.email
    assert item["waiting_seconds"] >= 0

    approved = await client.post(
        f"{REQUESTS}/{request_id}/approve", json={"role": "admin"}, headers=boss_headers
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["decided_by_email"] == boss_email
    refreshed = await reload(db_session, User, user_id)
    assert (refreshed.role, refreshed.status) == ("admin", "active")
    assert refreshed.approved_by == boss_id

    again = await client.post(
        f"{REQUESTS}/{request_id}/approve",
        json={"role": "operator"},
        headers=boss_headers,
    )
    assert again.status_code == 409
    history = await client.get(
        REQUESTS, params={"status": "approved"}, headers=boss_headers
    )
    assert str(request_id) in {i["id"] for i in history.json()["items"]}
    everything = await client.get(
        REQUESTS, params={"status": "all"}, headers=boss_headers
    )
    assert everything.status_code == 200
    assert AuthEvent.ACCESS_APPROVED in await audit_actions(db_session, user_id)


async def test_reject_with_a_note(
    client: httpx.AsyncClient, db_session: AsyncSession, boss_headers: dict[str, str]
) -> None:
    user, request = await pending_request(db_session)
    user_id, request_id = user.id, request.id
    missing_note = await client.post(
        f"{REQUESTS}/{request_id}/reject", json={}, headers=boss_headers
    )
    assert missing_note.status_code == 422
    rejected = await client.post(
        f"{REQUESTS}/{request_id}/reject",
        json={"note": "Not on the project"},
        headers=boss_headers,
    )
    assert rejected.status_code == 200
    assert rejected.json()["decision_note"] == "Not on the project"
    assert (await reload(db_session, User, user_id)).status == "rejected"
    assert AuthEvent.ACCESS_REJECTED in await audit_actions(db_session, user_id)
    late_approve = await client.post(
        f"{REQUESTS}/{request_id}/approve",
        json={"role": "operator"},
        headers=boss_headers,
    )
    assert late_approve.status_code == 409


async def test_approving_a_suspended_users_request_is_409(
    client: httpx.AsyncClient, db_session: AsyncSession, boss_headers: dict[str, str]
) -> None:
    user, request = await pending_request(db_session)
    user.status = "suspended"
    await db_session.commit()
    response = await client.post(
        f"{REQUESTS}/{request.id}/approve",
        json={"role": "operator"},
        headers=boss_headers,
    )
    assert response.status_code == 409


async def test_unknown_request_is_404(
    client: httpx.AsyncClient, boss_headers: dict[str, str]
) -> None:
    for action, body in (("approve", {"role": "admin"}), ("reject", {"note": "n"})):
        response = await client.post(
            f"{REQUESTS}/{uuid.uuid4()}/{action}", json=body, headers=boss_headers
        )
        assert response.status_code == 404


async def test_bulk_approve_is_all_or_nothing(
    client: httpx.AsyncClient, db_session: AsyncSession, boss_headers: dict[str, str]
) -> None:
    pairs = [await pending_request(db_session) for _ in range(3)]
    id_pairs = [(u.id, r.id) for u, r in pairs]
    await client.post(
        f"{REQUESTS}/{id_pairs[0][1]}/reject", json={"note": "no"}, headers=boss_headers
    )
    ids = [str(r) for _, r in id_pairs]
    failed = await client.post(
        f"{REQUESTS}/bulk-approve",
        json={"ids": ids, "role": "operator"},
        headers=boss_headers,
    )
    assert failed.status_code == 409
    for user_id, request_id in id_pairs[1:]:
        assert (await reload(db_session, AccessRequest, request_id)).status == "pending"
        assert (await reload(db_session, User, user_id)).status == "pending"
        assert AuthEvent.ACCESS_APPROVED not in await audit_actions(db_session, user_id)

    ok = await client.post(
        f"{REQUESTS}/bulk-approve",
        json={"ids": ids[1:], "role": "operator"},
        headers=boss_headers,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["approved"] == 2
    for user_id, _ in id_pairs[1:]:
        refreshed = await reload(db_session, User, user_id)
        assert (refreshed.role, refreshed.status) == ("operator", "active")
        assert AuthEvent.ACCESS_APPROVED in await audit_actions(db_session, user_id)

    unknown = await client.post(
        f"{REQUESTS}/bulk-approve",
        json={"ids": [str(uuid.uuid4())], "role": "operator"},
        headers=boss_headers,
    )
    assert unknown.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"ids": [], "role": "operator"},
        {"ids": ["not-a-uuid"], "role": "operator"},
        {"ids": [str(uuid.UUID(int=1))] * 2, "role": "operator"},
        {"ids": [str(uuid.uuid4()) for _ in range(101)], "role": "operator"},
        {"ids": [str(uuid.uuid4())]},
    ],
)
async def test_bulk_approve_validation(
    client: httpx.AsyncClient, boss_headers: dict[str, str], payload: dict[str, Any]
) -> None:
    response = await client.post(
        f"{REQUESTS}/bulk-approve", json=payload, headers=boss_headers
    )
    assert response.status_code == 422


async def test_request_list_rejects_unknown_status(
    client: httpx.AsyncClient, boss_headers: dict[str, str]
) -> None:
    response = await client.get(
        REQUESTS, params={"status": "open"}, headers=boss_headers
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


async def test_stats_shape(
    client: httpx.AsyncClient, db_session: AsyncSession, boss_headers: dict[str, str]
) -> None:
    _, request = await pending_request(db_session)
    await client.post(
        f"{REQUESTS}/{request.id}/approve",
        json={"role": "operator"},
        headers=boss_headers,
    )
    response = await client.get(
        f"{USERS}/stats", params={"days": 7}, headers=boss_headers
    )
    assert response.status_code == 200
    body = response.json()
    kpis = body["kpis"]
    assert kpis["total"] >= 2
    assert kpis["active"] >= 2
    assert kpis["total"] == sum(
        kpis[k] for k in ("active", "pending", "rejected", "suspended")
    )
    assert len(body["logins_per_day"]) == 7
    assert [s["stage"] for s in body["funnel"]] == [
        "signed_in",
        "requested",
        "approved",
        "active",
    ]
    assert {r["role"] for r in body["role_mix"]} == {"superadmin", "admin", "operator"}
    buckets = {b["bucket"]: b["count"] for b in body["time_to_approve"]}
    assert list(buckets) == ["<1h", "1-4h", "4-24h", "1-3d", ">3d"]
    assert buckets["<1h"] >= 1
    for days in (0, 91):
        bad = await client.get(
            f"{USERS}/stats", params={"days": days}, headers=boss_headers
        )
        assert bad.status_code == 422


# ---------------------------------------------------------------------------
# Concurrency (separate connections, committed rows; Postgres in CI)
# ---------------------------------------------------------------------------


async def _committed_users(
    maker: async_sessionmaker[AsyncSession], *specs: tuple[str | None, str]
) -> list[User]:
    async with maker() as db:
        users = [
            await make_user(db, role=role, status=status) for role, status in specs
        ]
        await db.commit()
    return users


async def _cleanup(
    maker: async_sessionmaker[AsyncSession], ids: list[uuid.UUID]
) -> None:
    async with maker() as db:
        await db.execute(delete(User).where(User.id.in_(ids)))
        await db.commit()


async def test_two_concurrent_approvals_exactly_one_wins() -> None:
    engine = new_engine()
    await require_postgres(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    boss_row, target = await _committed_users(
        maker, ("superadmin", "active"), (None, "pending")
    )
    request_id = uuid.uuid4()
    try:
        async with maker() as db:
            db.add(AccessRequest(id=request_id, user_id=target.id, status="pending"))
            await db.commit()
        actor = to_auth_user(boss_row)
        ctx = AuditContext("test-concurrent-approve", actor.email, "127.0.0.1")

        async def approve(role: Role) -> str:
            async with maker() as db:
                try:
                    await UserAdminService(db).approve(actor, request_id, role, ctx)
                except ConflictError:
                    return "conflict"
                return "ok"

        results = await asyncio.gather(approve(Role.OPERATOR), approve(Role.ADMIN))
        assert sorted(results) == ["conflict", "ok"]
        async with maker() as db:
            approvals = [
                r
                for r in (
                    await db.execute(
                        select(AuditLog).where(
                            AuditLog.action == AuthEvent.ACCESS_APPROVED.value,
                            AuditLog.request_id == "test-concurrent-approve",
                        )
                    )
                )
                .scalars()
                .all()
                if r.detail.get("target_user_id") == str(target.id)
            ]
        assert len(approvals) == 1
    finally:
        await _cleanup(maker, [boss_row.id, target.id])
        await engine.dispose()


async def test_concurrent_mutual_demotion_keeps_one_superadmin() -> None:
    engine = new_engine()
    await require_postgres(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    a, b = await _committed_users(
        maker, ("superadmin", "active"), ("superadmin", "active")
    )
    try:
        async with maker() as db:
            others = (
                await db.execute(
                    select(User.id).where(
                        User.role == "superadmin",
                        User.status == "active",
                        User.id.not_in([a.id, b.id]),
                    )
                )
            ).all()
        if others:
            pytest.skip("other committed superadmins exist in this database")

        async def demote(actor_row: User, target: User) -> str:
            async with maker() as db:
                try:
                    await UserAdminService(db).update_user(
                        to_auth_user(actor_row),
                        target.id,
                        UserChange(version=1, role=Role.ADMIN),
                        AuditContext("test-mutual", actor_row.email, None),
                    )
                except ConflictError:
                    return "conflict"
                return "ok"

        results = await asyncio.gather(demote(a, b), demote(b, a))
        assert sorted(results) == ["conflict", "ok"]
        async with maker() as db:
            remaining = (
                await db.execute(
                    select(User.id).where(
                        User.id.in_([a.id, b.id]),
                        User.role == "superadmin",
                        User.status == "active",
                    )
                )
            ).all()
        assert len(remaining) == 1
    finally:
        await _cleanup(maker, [a.id, b.id])
        await engine.dispose()
