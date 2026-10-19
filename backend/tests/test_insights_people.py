"""Probable people, visitor and hour filters of the Analytics page (DB-backed).

Rows are seeded inside the test's rolled-back transaction and every query is
scoped to the seeded people, so the shared database is left untouched.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.insights.analytics_repository import (
    InsightsAnalyticsRepository,
    QueryContext,
    person_salt,
    split_visitor,
    visitor_id,
)
from app.insights.analytics_service import InsightsAnalyticsService
from app.models.db_models import ChatMessageRecord, ChatSession, TurnInsight
from app.schemas.insights import InsightsFilters, PersonIdentity
from tests.pg_helpers import require_postgres

SALT = person_salt(get_settings().session_secret.get_secret_value())
BASE_TS = datetime(2026, 3, 2, 9, 15)


def key(source: str) -> str:
    return hashlib.sha256((SALT + source).encode()).hexdigest()[:16]


def ctx(**filters: object) -> QueryContext:
    return QueryContext(
        filters=InsightsFilters(tz="UTC", **filters),
        date_from=None,
        date_to=None,
        salt=SALT,
        test_ips=(),
    )


async def seed_session(
    db: AsyncSession,
    *,
    email: str,
    ip: str | None,
    start: datetime,
    turns: tuple[tuple[str, bool], ...],
) -> uuid.UUID:
    """One session with ``turns`` = (intent, handled) labelled exchanges."""
    session = ChatSession(
        id=uuid.uuid4(),
        user_email=email,
        created_ip=ip,
        created_at=start,
        last_activity_at=start + timedelta(minutes=len(turns)),
        expires_at=start + timedelta(hours=1),
    )
    db.add(session)
    await db.flush()
    for i, (intent, handled) in enumerate(turns):
        at = start + timedelta(minutes=i)
        db.add(
            ChatMessageRecord(
                session_id=session.id,
                turn_index=i,
                role="user",
                content=f"q{i}",
                created_at=at,
            )
        )
        reply = ChatMessageRecord(
            session_id=session.id,
            turn_index=i,
            role="assistant",
            content="a",
            created_at=at,
        )
        db.add(reply)
        await db.flush()
        db.add(
            TurnInsight(
                message_id=reply.id,
                session_id=session.id,
                intent=intent,
                bot_quality="OK" if handled else "PART",
                substantive=intent not in {"GRE", "ACK"},
                handled=handled,
                classified_by="rule",
            )
        )
    await db.flush()
    return session.id


@pytest.fixture
async def people(db_session: AsyncSession) -> dict[str, object]:
    await require_postgres(db_session.bind.engine)  # type: ignore[union-attr]
    tag = uuid.uuid4().hex[:8]
    email = f"Analytics.Person.{tag}@Example.test"
    ip_a, ip_b = f"10.250.{int(tag[:2], 16)}.1", f"10.250.{int(tag[:2], 16)}.2"
    anon_ip = f"10.251.{int(tag[:2], 16)}.9"
    first = await seed_session(
        db_session,
        email=email,
        ip=ip_a,
        start=BASE_TS,
        turns=(("SEC", True), ("SVC", True)),
    )
    # Same person: case and whitespace differ, another network, an hour later.
    second = await seed_session(
        db_session,
        email=f"  {email.upper()} ",
        ip=ip_b,
        start=BASE_TS + timedelta(hours=1),
        turns=(("SEC", False),),
    )
    # The anonymous actor is not an identity: it is grouped on its network.
    anon = await seed_session(
        db_session, email="anonymous", ip=anon_ip, start=BASE_TS, turns=(("SVC", True),)
    )
    return {
        "person": key(email.lower()),
        "anon": key(f"ip:{anon_ip}"),
        "sessions": (first, second, anon),
        "ips": (ip_a, ip_b, anon_ip),
    }


async def test_person_groups_email_case_insensitively_and_label_is_stable(
    db_session, people
):
    repo = InsightsAnalyticsRepository(db_session)
    person = people["person"]
    rows = await repo.people(ctx(person=person))
    assert {r.person_key for r in rows} == {person}
    assert sum(r.n for r in rows) == 2
    filtered_pn = {r.pn for r in rows}
    # Narrowing further (one hour of the day) keeps the same P-number.
    narrowed = await repo.people(ctx(person=person, hour=BASE_TS.hour + 1))
    assert {r.pn for r in narrowed} == filtered_pn
    assert sum(r.n for r in narrowed) == 1
    number = await repo.person_number(ctx(), person)
    assert {number.pn} == filtered_pn
    assert number.conversations == 2


async def test_anonymous_actor_falls_back_to_network(db_session, people):
    repo = InsightsAnalyticsRepository(db_session)
    rows = await repo.people(ctx(person=people["anon"]))
    assert sum(r.n for r in rows) == 1


async def test_person_detail_lists_real_visitors(db_session, people):
    service = InsightsAnalyticsService(db_session, runner=None)  # type: ignore[arg-type]
    detail = await service.person(people["person"], InsightsFilters(tz="UTC"))
    assert detail is not None
    assert detail.identity is PersonIdentity.ACCOUNT
    assert detail.sessions == 2
    assert detail.conversations == 2
    ip_a, ip_b, _ = people["ips"]
    assert {v.visitor_id for v in detail.visitors} == {
        f"client-{ip_a}",
        f"client-{ip_b}",
    }
    anon = await service.person(people["anon"], InsightsFilters(tz="UTC"))
    assert anon is not None and anon.identity is PersonIdentity.NETWORK
    assert await service.person("0" * 16, InsightsFilters(tz="UTC")) is None


async def test_visitor_filter_matches_the_visitors_page(db_session, people):
    repo = InsightsAnalyticsRepository(db_session)
    ip_a = people["ips"][0]
    rows = await repo.people(ctx(visitor=f"client-{ip_a}"))
    assert {r.person_key for r in rows} == {people["person"]}
    assert sum(r.n for r in rows) == 1


async def test_hour_filter_narrows_exchanges(db_session, people):
    repo = InsightsAnalyticsRepository(db_session)
    hours = await repo.hourly(ctx(person=people["person"]))
    assert hours == {BASE_TS.hour: 2, BASE_TS.hour + 1: 1}
    only = await repo.hourly(ctx(person=people["person"], hour=BASE_TS.hour))
    assert only == {BASE_TS.hour: 2}


def test_visitor_id_round_trip():
    assert visitor_id("10.0.0.1") == "client-10.0.0.1"
    assert visitor_id(None) is None
    assert split_visitor("client-10.0.0.1") == ("10.0.0.1", None)
    uid = str(uuid.uuid4())
    assert split_visitor(uid) == (None, uid)
