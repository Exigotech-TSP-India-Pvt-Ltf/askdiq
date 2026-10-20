"""User Management speed and real time: precomputed stats with the sparkline
series, the keyset auth audit trail, per-user audit in the detail, and the
``users`` live-stream announcement after every committed auth change."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import live
from app.auth.admin_service import SPARK_DAYS, SPARK_EVENTS
from app.core.events import TOPIC_CONSOLE_CHANGED
from app.models.db_models import AccessRequest, User
from tests.auth_helpers import make_user, session_headers

pytestmark = pytest.mark.real_auth

USERS = "/admin/users-accounts"
REQUESTS = "/admin/access-requests"
AUDIT = "/admin/auth-audit"


@pytest.fixture
async def boss(db_session: AsyncSession) -> User:
    return await make_user(db_session, role="superadmin", status="active")


@pytest.fixture
async def boss_headers(db_session: AsyncSession, boss: User) -> dict[str, str]:
    return await session_headers(db_session, boss)


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []

    async def fake_publish(topic: str, item_id: Any) -> None:
        events.append((topic, str(item_id)))

    monkeypatch.setattr(live, "publish_event", fake_publish)
    return events


async def pending_request(db: AsyncSession) -> tuple[User, AccessRequest]:
    user = await make_user(db, status="pending")
    request = AccessRequest(
        id=uuid.uuid4(), user_id=user.id, requested_role="operator", status="pending"
    )
    db.add(request)
    await db.commit()
    return user, request


async def approve(
    client: httpx.AsyncClient, headers: dict[str, str], request: AccessRequest
) -> None:
    response = await client.post(
        f"{REQUESTS}/{request.id}/approve", json={"role": "operator"}, headers=headers
    )
    assert response.status_code == 200, response.text


async def drain_announcements() -> None:
    for task in list(live._tasks):  # noqa: SLF001 - wait for scheduled announcements
        await task


async def test_stats_carry_the_sparkline_series(
    client: httpx.AsyncClient, db_session: AsyncSession, boss_headers: dict[str, str]
) -> None:
    _, request = await pending_request(db_session)
    await approve(client, boss_headers, request)
    response = await client.get(f"{USERS}/stats", headers=boss_headers)
    assert response.status_code == 200
    assert response.headers.get("etag")
    body = response.json()
    assert body["events_days"] == SPARK_DAYS
    assert set(body["events_per_day"]) == set(SPARK_EVENTS)
    approved = body["events_per_day"]["access_approved"]
    assert len(approved) == SPARK_DAYS
    assert approved[-1]["count"] >= 1
    dates = [p["date"] for p in approved]
    assert dates == sorted(dates)


async def test_stats_follow_a_committed_change(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    boss_headers: dict[str, str],
    published: list[tuple[str, str]],
) -> None:
    _, request = await pending_request(db_session)
    before = (await client.get(f"{USERS}/stats", headers=boss_headers)).json()
    await approve(client, boss_headers, request)
    after = (await client.get(f"{USERS}/stats", headers=boss_headers)).json()
    assert after["kpis"]["open_requests"] == before["kpis"]["open_requests"] - 1
    await drain_announcements()
    assert (TOPIC_CONSOLE_CHANGED, live.SCOPE_USERS) in published


async def test_rolled_back_change_announces_nothing(
    db_session: AsyncSession, published: list[tuple[str, str]]
) -> None:
    await drain_announcements()
    published.clear()
    await make_user(db_session, status="pending")  # a transaction is open
    live.mark_auth_changed(db_session)
    await db_session.rollback()
    await db_session.commit()
    await drain_announcements()
    assert published == []


async def test_auth_audit_keyset_pages(
    client: httpx.AsyncClient, db_session: AsyncSession, boss_headers: dict[str, str]
) -> None:
    for _ in range(3):
        _, request = await pending_request(db_session)
        await approve(client, boss_headers, request)
    first = await client.get(
        AUDIT, params={"limit": 2, "action": "access_approved"}, headers=boss_headers
    )
    assert first.status_code == 200
    page1 = first.json()
    assert len(page1["items"]) == 2
    assert page1["has_more"] is True
    assert page1["total"] >= 3
    assert {e["action"] for e in page1["items"]} == {"access_approved"}
    second = await client.get(
        AUDIT,
        params={
            "limit": 2,
            "action": "access_approved",
            "cursor": page1["next_cursor"],
        },
        headers=boss_headers,
    )
    page2 = second.json()
    assert page2["items"]
    ids1 = {e["id"] for e in page1["items"]}
    assert not ids1 & {e["id"] for e in page2["items"]}
    newest = [e["created_at"] for e in page1["items"] + page2["items"]]
    assert newest == sorted(newest, reverse=True)


async def test_auth_audit_search_and_validation(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    boss: User,
    boss_headers: dict[str, str],
) -> None:
    _, request = await pending_request(db_session)
    await approve(client, boss_headers, request)
    hit = await client.get(
        AUDIT, params={"search": boss.email.upper()}, headers=boss_headers
    )
    assert hit.status_code == 200
    assert hit.json()["items"]
    assert all(e["actor"] == boss.email for e in hit.json()["items"])
    miss = await client.get(
        AUDIT, params={"search": "nobody-%-matches"}, headers=boss_headers
    )
    assert miss.json()["items"] == []
    assert miss.json()["total"] == 0
    for params in ({"cursor": "not-a-cursor"}, {"action": "query"}, {"limit": 101}):
        bad = await client.get(AUDIT, params=params, headers=boss_headers)
        assert bad.status_code == 422


async def test_auth_audit_needs_audit_permission(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    admin = await make_user(db_session, role="admin", status="active")
    response = await client.get(AUDIT, headers=await session_headers(db_session, admin))
    assert response.status_code == 403


async def test_user_detail_carries_recent_auth_events(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    boss: User,
    boss_headers: dict[str, str],
) -> None:
    user, request = await pending_request(db_session)
    await approve(client, boss_headers, request)
    target = (await client.get(f"{USERS}/{user.id}", headers=boss_headers)).json()
    assert [e["action"] for e in target["recent_audit"]] == ["access_approved"]
    assert target["recent_audit"][0]["detail"]["target_user_id"] == str(user.id)
    actor = (await client.get(f"{USERS}/{boss.id}", headers=boss_headers)).json()
    assert "access_approved" in {e["action"] for e in actor["recent_activity"]}
