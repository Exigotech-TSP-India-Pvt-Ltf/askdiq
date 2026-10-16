"""Parity fixture loader (planchanges4 B6.4.1).

Loads ``tests/fixtures/insights_reference.json`` (the reference HTML's 49
conversations and 117 exchanges, extracted by
``scripts/extract_insights_fixture.py``) into ``chat_sessions``,
``chat_messages`` and ``turn_insights``, using the HTML's own codes and labels.

- One HTML conversation = one ``chat_sessions`` row; one HTML exchange = a user
  row plus its assistant row (same ``turn_index``); labels sit on the
  assistant row (newplan 6.4).
- Ids are deterministic (uuid5), and every insert is ``ON CONFLICT DO
  NOTHING``, so loading twice is a no-op.
- HTML timestamps are wall-clock times in ``FIXTURE_TIMEZONE``; they are stored
  as naive UTC like real chat rows (converted in SQL).
- Only for this fixture, the HTML's website-specific taxonomy codes (CAR, STAFF,
  PRIV, TECH, META and the "No live job openings" issue) are added to
  ``insight_taxonomy`` so parity is exact.

PR-13 tests use the ``insights_reference_db`` fixture (import it from here).
Inside its rolled-back transaction it first removes every other chat session
and rollup row, so the dashboard endpoints see exactly the reference data.
The HTML's "Great" rating comes from user feedback in our model (newplan 6.4),
so a 5-star rating is seeded on those conversations.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.insights import get_parallel_runner
from app.core.config import get_settings
from app.core.database import get_db, read_only_session
from app.insights.taxonomy import DEFAULT_TAXONOMY
from main import app
from tests.pg_helpers import new_engine, require_postgres

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "insights_reference.json"
FIXTURE_NAMESPACE = uuid.UUID("6f1d3b8e-2c4a-4e7f-9a51-3d2e8c7b0a14")
FIXTURE_TIMEZONE = "Australia/Sydney"
FIXTURE_EMAIL_DOMAIN = "insights-fixture.invalid"
ASSISTANT_PLACEHOLDER = "[reference fixture: assistant reply not captured]"

# (code, label, group) - HTML-only intents, seeded only for the fixture.
FIXTURE_INTENTS = (
    ("CAR", "Careers", "Career seeker"),
    ("STAFF", "Staff lookup", "Staff / people lookup"),
    ("PRIV", "Private person", "Staff / people lookup"),
    ("TECH", "Technical", "Off-scope request"),
    ("META", "About the bot", "Off-scope request"),
)
FIXTURE_ISSUES = (("JOBS", "No live job openings"),)


@lru_cache
def load_reference() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def issue_codes() -> dict[str, str]:
    """HTML issue label -> taxonomy code."""
    codes = {entry.label: code for code, entry in DEFAULT_TAXONOMY.issues.items()}
    codes.update({label: code for code, label in FIXTURE_ISSUES})
    return codes


def fixture_id(kind: str, *parts: str) -> uuid.UUID:
    return uuid.uuid5(FIXTURE_NAMESPACE, ":".join((kind, *parts)))


def session_id(conv_id: str) -> uuid.UUID:
    return fixture_id("session", conv_id)


def person_email(person: str) -> str:
    return f"{person.lower()}@{FIXTURE_EMAIL_DOMAIN}"


def person_ip(person: str) -> str:
    # TEST-NET-3 documentation range: never a real visitor address.
    return f"203.0.113.{int(person.lstrip('P'))}"


def friction_code(value: str) -> str | None:
    lowered = value.lower()
    if "hostile" in lowered:
        return "hostile"
    if "abandon" in lowered:
        return "abandoned"
    return None


def _outcome(quality: str) -> str:
    if quality == "ACK":
        return "conversational"
    if quality in {"CORR", "SCOPE"}:
        return "blocked"
    return "answered"


# Wall-clock text in :tz -> naive UTC timestamp (same as real chat rows).
_SESSION_SQL = text(
    """
    INSERT INTO chat_sessions
        (id, user_email, title, created_at, last_activity_at, expires_at, created_ip)
    VALUES (
        :id, :email, :title,
        (CAST(CAST(:start AS text) AS timestamp) AT TIME ZONE CAST(:tz AS text))
            AT TIME ZONE 'UTC',
        (CAST(CAST(:end AS text) AS timestamp) AT TIME ZONE CAST(:tz AS text))
            AT TIME ZONE 'UTC',
        (CAST(CAST(:end AS text) AS timestamp) AT TIME ZONE CAST(:tz AS text))
            AT TIME ZONE 'UTC',
        :ip
    )
    ON CONFLICT (id) DO NOTHING RETURNING id
    """
)
_MESSAGE_SQL = text(
    """
    INSERT INTO chat_messages
        (id, session_id, turn_index, role, content, created_at, grounded,
         guardrail_flags, ip_address, outcome)
    VALUES (
        :id, :session_id, :turn_index, :role, :content,
        (CAST(CAST(:ts AS text) AS timestamp) AT TIME ZONE CAST(:tz AS text))
            AT TIME ZONE 'UTC',
        :grounded, CAST(:flags AS json), :ip, :outcome
    )
    ON CONFLICT (id) DO NOTHING RETURNING id
    """
)
_TAXONOMY_SQL = text(
    """
    INSERT INTO insight_taxonomy
        (kind, code, label, "group", substantive, handled, sort_order, is_system)
    VALUES (:kind, :code, :label, :group, true, false, :sort_order, false)
    ON CONFLICT (kind, code) DO NOTHING RETURNING id
    """
)
_INSIGHT_SQL = text(
    "INSERT INTO turn_insights (id, message_id, session_id, intent, bot_quality, "
    "issue, substantive, handled, friction, confidence, classified_by) VALUES "
    "(:id, :message_id, :session_id, :intent, :bot_quality, :issue, :substantive, "
    ":handled, :friction, 1.0, 'manual') ON CONFLICT (message_id) DO NOTHING "
    "RETURNING id"
)


async def _insert(session: AsyncSession, stmt: Any, params: dict[str, Any]) -> int:
    return len((await session.execute(stmt, params)).all())


async def load_reference_fixture(session: AsyncSession) -> dict[str, int]:
    """Insert the reference dataset; returns rows inserted per table."""
    data = load_reference()
    issues = issue_codes()
    inserted = {"taxonomy": 0, "sessions": 0, "messages": 0, "turn_insights": 0}

    for order, (code, label, group) in enumerate(FIXTURE_INTENTS, start=100):
        inserted["taxonomy"] += await _insert(
            session,
            _TAXONOMY_SQL,
            {
                "kind": "intent",
                "code": code,
                "label": label,
                "group": group,
                "sort_order": order,
            },
        )
    for order, (code, label) in enumerate(FIXTURE_ISSUES, start=100):
        inserted["taxonomy"] += await _insert(
            session,
            _TAXONOMY_SQL,
            {
                "kind": "issue",
                "code": code,
                "label": label,
                "group": None,
                "sort_order": order,
            },
        )

    for conv in data["conversations"]:
        inserted["sessions"] += await _insert(
            session,
            _SESSION_SQL,
            {
                "id": session_id(conv["conv_id"]),
                "email": person_email(conv["person"]),
                "title": f"Reference {conv['conv_id']}",
                "start": conv["start"],
                "end": conv["end"],
                "tz": FIXTURE_TIMEZONE,
                "ip": person_ip(conv["person"]),
            },
        )

    for exchange in data["exchanges"]:
        conv_id, turn = exchange["conv_id"], exchange["turn"]
        sid = session_id(conv_id)
        quality = exchange["bot_quality"]
        handled = exchange["handled"] == "Yes"
        assistant_id = fixture_id("assistant", conv_id, turn)
        common = {
            "session_id": sid,
            "turn_index": int(turn) - 1,
            "ts": exchange["timestamp"],
            "tz": FIXTURE_TIMEZONE,
        }
        inserted["messages"] += await _insert(
            session,
            _MESSAGE_SQL,
            {
                **common,
                "id": fixture_id("user", conv_id, turn),
                "role": "user",
                "content": exchange["user_message"],
                "grounded": None,
                "flags": None,
                "ip": person_ip(exchange["person"]),
                "outcome": None,
            },
        )
        inserted["messages"] += await _insert(
            session,
            _MESSAGE_SQL,
            {
                **common,
                "id": assistant_id,
                "role": "assistant",
                "content": ASSISTANT_PLACEHOLDER,
                "grounded": quality == "OK" and handled,
                "flags": "[]",
                "ip": None,
                "outcome": _outcome(quality),
            },
        )
        issue_label = exchange["issue"]
        inserted["turn_insights"] += await _insert(
            session,
            _INSIGHT_SQL,
            {
                "id": fixture_id("insight", conv_id, turn),
                "message_id": assistant_id,
                "session_id": sid,
                "intent": exchange["intent"],
                "bot_quality": quality,
                "issue": issues[issue_label] if issue_label else None,
                "substantive": exchange["substantive"] == "Yes",
                "handled": handled,
                "friction": friction_code(exchange.get("friction", "")),
            },
        )
    await session.flush()
    return inserted


_FEEDBACK_SQL = text(
    """
    INSERT INTO message_feedback (id, message_id, session_id, actor, rating)
    VALUES (:id, :message_id, :session_id, :actor, :rating)
    ON CONFLICT (message_id, actor) DO NOTHING RETURNING id
    """
)
GREAT_RATING = 5


async def load_reference_feedback(session: AsyncSession) -> int:
    """A 5-star rating on the last reply of every conversation rated "Great"."""
    data = load_reference()
    last_turn: dict[str, str] = {}
    for exchange in data["exchanges"]:
        conv_id = exchange["conv_id"]
        if int(exchange["turn"]) >= int(last_turn.get(conv_id, "0")):
            last_turn[conv_id] = exchange["turn"]
    inserted = 0
    for conv in data["conversations"]:
        if conv["experience_rating"] != "Great":
            continue
        conv_id = conv["conv_id"]
        inserted += await _insert(
            session,
            _FEEDBACK_SQL,
            {
                "id": fixture_id("feedback", conv_id),
                "message_id": fixture_id("assistant", conv_id, last_turn[conv_id]),
                "session_id": session_id(conv_id),
                "actor": person_email(conv["person"]),
                "rating": GREAT_RATING,
            },
        )
    await session.flush()
    return inserted


async def isolate_insights_data(session: AsyncSession) -> None:
    """Remove other chat data and rollup rows inside the test transaction."""
    await session.execute(
        text("DELETE FROM chat_sessions WHERE user_email NOT LIKE :domain"),
        {"domain": f"%@{FIXTURE_EMAIL_DOMAIN}"},
    )
    await session.execute(text("DELETE FROM insight_daily_rollup"))


@pytest.fixture
async def insights_reference_db(db_session: AsyncSession) -> AsyncSession:
    """``db_session`` with only the reference dataset (rolled back after)."""
    engine = new_engine()
    try:
        await require_postgres(engine)
    finally:
        await engine.dispose()
    await isolate_insights_data(db_session)
    await load_reference_fixture(db_session)
    await load_reference_feedback(db_session)
    return db_session


SessionTask = Callable[[AsyncSession], Awaitable[Any]]


def sequential_runner(session: AsyncSession) -> Callable[..., Awaitable[list[Any]]]:
    """``gather_scoped`` stand-in: the tasks run in turn on the test session
    (the fixture data is uncommitted, so other sessions cannot see it)."""

    async def run(*tasks: SessionTask, **_kwargs: Any) -> list[Any]:
        return [await task(session) for task in tasks]

    return run


@pytest.fixture
async def insights_client(
    insights_reference_db: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    """API client whose every database access uses the fixture transaction."""

    async def _db() -> AsyncIterator[AsyncSession]:
        yield insights_reference_db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[read_only_session] = _db
    app.dependency_overrides[get_parallel_runner] = lambda: sequential_runner(
        insights_reference_db
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-API-Key": get_settings().api_key_name or "ci-test-key"},
        ) as client:
            yield client
    finally:
        for dep in (get_db, read_only_session, get_parallel_runner):
            app.dependency_overrides.pop(dep, None)
