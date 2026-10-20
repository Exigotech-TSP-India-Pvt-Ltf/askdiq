"""``usage_turns`` (trigger-maintained ledger) and the Usage & Cost lists
built on it: the triggers keep it equal to the user/assistant pairing join,
and every list returns what the previous per-request join returned
(``tests/usage_legacy.py``)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import admin_usage
from app.governance import usage_queries as uq
from app.models.db_models import (
    ChatMessageRecord,
    ChatSession,
    LatencyRecord,
    UsageTurn,
)
from tests import usage_legacy as legacy

_PAIRS_SQL = text(
    """
    SELECT a.id, u.id, u.created_at, u.ip_address, s.created_ip, a.request_id,
           a.outcome, a.grounded, a.prompt_tokens, a.completion_tokens,
           a.reasoning_tokens, a.total_tokens, a.embedding_tokens, a.cost_usd,
           (SELECT l.total_ms FROM latency_metrics l WHERE l.request_id = a.request_id
             ORDER BY l.created_at DESC LIMIT 1)
    FROM chat_messages u
    JOIN chat_messages a ON a.session_id = u.session_id
     AND a.turn_index = u.turn_index AND a.role = 'assistant'
    JOIN chat_sessions s ON s.id = u.session_id
    WHERE u.role = 'user'
    """
)
_LEDGER_SQL = text(
    """
    SELECT asst_msg_id, user_msg_id, created_at, user_ip, session_ip, request_id,
           outcome, grounded, prompt_tokens, completion_tokens, reasoning_tokens,
           total_tokens, embedding_tokens, cost_usd, latency_ms
    FROM usage_turns
    """
)


async def _ledger_matches_join(db: AsyncSession) -> None:
    pairs = sorted(map(tuple, (await db.execute(_PAIRS_SQL)).all()), key=str)
    ledger = sorted(map(tuple, (await db.execute(_LEDGER_SQL)).all()), key=str)
    assert ledger == pairs


def _tag() -> str:
    return uuid.uuid4().hex[:8]


async def _isolate(db: AsyncSession) -> None:
    """Hide rows other tests committed (the suite shares one database): the
    comparisons below cover whole tables, so they see only this test's rows.
    Runs inside the test transaction, so the rows return on rollback."""
    await db.execute(text("SET LOCAL lock_timeout = '10s'"))
    await db.execute(delete(LatencyRecord))
    await db.execute(delete(ChatMessageRecord))
    await db.execute(delete(ChatSession))


async def _seed(db: AsyncSession, tag: str) -> list[ChatSession]:
    """Three sessions on two IPs (one turn without an IP), costs and
    latencies distinct, turns one hour apart over the last few days.
    Only these rows exist for the test (:func:`_isolate`)."""
    await _isolate(db)
    base = datetime.utcnow() - timedelta(days=3)
    sessions = []
    n = 0
    for s_i, ip in enumerate(
        (f"10.{tag[:2]}.0.1", f"10.{tag[:2]}.0.2", f"10.{tag[:2]}.0.1")
    ):
        sess = ChatSession(
            user_email=f"{tag}@ledger.test",
            title=f"Ledger {tag} topic {s_i}",
            created_ip=ip,
            created_at=base,
            last_activity_at=base + timedelta(hours=s_i),
            expires_at=base + timedelta(days=30),
        )
        db.add(sess)
        await db.flush()
        sessions.append(sess)
        for turn in range(s_i + 1):
            n += 1
            at = base + timedelta(hours=n)
            rid = f"{tag}-{n}"
            db.add(
                ChatMessageRecord(
                    session_id=sess.id,
                    turn_index=turn,
                    role="user",
                    content=f"question {tag} {n}",
                    created_at=at,
                    ip_address=None if n == 2 else ip,
                )
            )
            db.add(
                ChatMessageRecord(
                    session_id=sess.id,
                    turn_index=turn,
                    role="assistant",
                    content=f"answer {tag} {n}",
                    created_at=at + timedelta(seconds=5),
                    request_id=rid,
                    outcome="fallback" if n == 3 else None,
                    grounded=n != 3,
                    prompt_tokens=100 * n,
                    completion_tokens=40 * n,
                    reasoning_tokens=10 * n,
                    total_tokens=140 * n,
                    embedding_tokens=7 * n,
                    cost_usd=0.000123 * n + 0.0000001 * n * n,
                )
            )
            if n != 4:
                db.add(LatencyRecord(request_id=rid, total_ms=1000.5 + n))
    await db.flush()
    return sessions


async def test_triggers_keep_the_ledger_equal_to_the_pairing_join(db_session):
    tag = _tag()
    sessions = await _seed(db_session, tag)
    await _ledger_matches_join(db_session)

    # Assistant row updated (cost / outcome), latency added, session IP moved.
    await db_session.execute(
        update(ChatMessageRecord)
        .where(ChatMessageRecord.request_id == f"{tag}-1")
        .values(cost_usd=1.5, outcome="blocked")
    )
    db_session.add(LatencyRecord(request_id=f"{tag}-4", total_ms=42.0))
    await db_session.execute(
        update(ChatSession)
        .where(ChatSession.id == sessions[1].id)
        .values(created_ip="10.99.99.99")
    )
    await db_session.flush()
    await _ledger_matches_join(db_session)

    # One message, then a whole session (cascade) deleted.
    await db_session.execute(
        delete(ChatMessageRecord).where(ChatMessageRecord.request_id == f"{tag}-2")
    )
    await db_session.execute(
        delete(ChatSession).where(ChatSession.id == sessions[2].id)
    )
    await db_session.flush()
    await _ledger_matches_join(db_session)
    remaining = await db_session.scalar(
        select(func.count())
        .select_from(UsageTurn)
        .where(UsageTurn.request_id.like(f"{tag}-%"))
    )
    assert remaining == 2  # session 0 (1 turn) + session 1 (2 turns, 1 deleted)


def _canon(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(items, key=lambda x: str(x[key]))


_RANGES = [(None, None), ("days", 2), ("days", 30)]


def _window(spec: tuple) -> tuple[datetime | None, datetime | None]:
    if spec[0] is None:
        return None, None
    now = datetime.utcnow()
    return now - timedelta(days=spec[1]), now


@pytest.mark.parametrize("audience", ["overview", "users", "testing"])
@pytest.mark.parametrize("window", _RANGES)
async def test_users_and_sessions_match_the_join(db_session, audience, window):
    tag = _tag()
    await _seed(db_session, tag)
    lo, hi = _window(window)
    big = 100_000
    u_rows = await uq.users_base(db_session, audience, lo, hi)
    s_rows = await uq.sessions_base(db_session, audience, lo, hi)
    for q in (None, f"10.{tag[:2]}", "0.1"):
        for user_type in (None, "test", "production", "bogus"):
            old = await legacy._usage_users_page(
                db_session,
                audience=audience,
                q=q,
                user_type=user_type,
                sort="cost_desc",
                limit=big,
                offset=0,
                from_dt=lo,
                to_dt=hi,
            )
            new = uq.users_page(
                u_rows, q=q, user_type=user_type, sort="cost_desc", limit=big, offset=0
            )
            assert new["total"] == old["total"]
            assert _canon(new["items"], "ip_address") == _canon(
                old["items"], "ip_address"
            )
            for name in (
                "unique_users",
                "active_sessions",
                "queries_driven",
                "shared_gateway_hint",
                "top_consumer_share_pct",
            ):
                assert new["stats"][name] == old["stats"][name], name
    for q, ip in ((None, None), (f"ledger {tag}", None), (None, f"10.{tag[:2]}.0.1")):
        old = await legacy._usage_sessions_page(
            db_session,
            audience=audience,
            q=q,
            ip=ip,
            sort="cost_desc",
            limit=big,
            offset=0,
            from_dt=lo,
            to_dt=hi,
        )
        new = uq.sessions_page(
            s_rows, q=q, ip=ip, sort="cost_desc", limit=big, offset=0
        )
        assert new["total"] == old["total"]
        assert _canon(new["items"], "id") == _canon(old["items"], "id")
        for name in ("total_sessions", "avg_turns_per_session", "max_depth"):
            assert new["stats"][name] == old["stats"][name], name
        assert new["stats"]["avg_cost_per_session"] == pytest.approx(
            old["stats"]["avg_cost_per_session"], abs=1e-6
        )


@pytest.mark.parametrize(
    "sort",
    [
        "cost_desc",
        "cost_asc",
        "queries_desc",
        "tokens_asc",
        "last_active_desc",
        "sessions_asc",
    ],
)
async def test_user_and_session_pages_follow_the_sort(db_session, sort):
    tag = _tag()
    await _seed(db_session, tag)
    q = f"10.{tag[:2]}.0."
    u_rows = await uq.users_base(db_session, "overview", None, None)
    old = await legacy._usage_users_page(
        db_session,
        audience="overview",
        q=q,
        user_type=None,
        sort=sort,
        limit=1,
        offset=1,
    )
    new = uq.users_page(u_rows, q=q, user_type=None, sort=sort, limit=1, offset=1)
    assert new == old
    if sort in legacy._ALLOWED_SESSION_SORTS:
        s_rows = await uq.sessions_base(db_session, "overview", None, None)
        q = f"ledger {tag}"
        old = await legacy._usage_sessions_page(
            db_session, audience="overview", q=q, ip=None, sort=sort, limit=2, offset=1
        )
        new = uq.sessions_page(s_rows, q=q, ip=None, sort=sort, limit=2, offset=1)
        assert new == old


@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"audience": "users"},
        {"audience": "testing"},
        {"q": "question"},
        {"q": "answer {tag}"},
        {"q": "{tag}-3"},
        {"outcome": "fallback"},
        {"grounded": True},
        {"ip": "10.{tag2}.0.2"},
        {"days": 2},
    ],
)
@pytest.mark.parametrize(
    "sort", ["created_at_desc", "cost_asc", "tokens_desc", "latency_desc"]
)
async def test_queries_match_the_join(db_session, filters, sort):
    tag = _tag()
    await _seed(db_session, tag)
    values = {
        k: v.format(tag=tag, tag2=tag[:2]) if isinstance(v, str) else v
        for k, v in filters.items()
    }
    lo, hi = _window(("days", values.pop("days"))) if "days" in values else (None, None)
    params = {
        "audience": "overview",
        "q": None,
        "ip": None,
        "session_id": None,
        "outcome": None,
        "grounded": None,
        "lo": lo,
        "hi": hi,
        **values,
    }
    old = await legacy._usage_queries_page(
        db_session, legacy.UsageQueryArgs(**params, sort=sort, limit=100_000, offset=0)
    )
    f = admin_usage.QueryFilters(**params)
    stats = await admin_usage._query_stats(db_session, await f.conditions(db_session))
    items = await admin_usage._query_page_items(db_session, f, sort, 100_000, 0)
    assert stats == old["stats"]
    assert _canon(items, "request_id") == _canon(old["items"], "request_id")
    # Pages follow the sort (ties broken by recency, then id).
    key = {
        "created_at": "created_at",
        "cost": "cost_usd",
        "tokens": None,
        "latency": "latency_ms",
    }[sort.rsplit("_", 1)[0]]
    if key:
        got = [x[key] for x in items if x[key] is not None]
        assert got == sorted(got, reverse=sort.endswith("_desc"))


async def test_list_routes_serve_pages(client, db_session):
    tag = _tag()
    await _seed(db_session, tag)
    for path, extra in (
        ("users", {"q": f"10.{tag[:2]}.0."}),
        ("sessions", {"q": f"ledger {tag}"}),
        ("queries", {"q": tag}),
    ):
        r = await client.get(
            f"/admin/usage/{path}", params={"limit": 2, "offset": 1, **extra}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == {"users": 2, "sessions": 3, "queries": 6}[path]
        assert len(body["items"]) == min(2, body["total"] - 1)
