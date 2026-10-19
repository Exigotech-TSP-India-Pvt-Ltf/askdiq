"""Visitor and conversation entity stats (planchanges4 B7.2).

Pure helpers run without a database; endpoint tests seed far-past days with
unique IPs / title markers (real Postgres, run in CI) and assert exact numbers.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.core.config import get_settings
from app.dashboard.window import parse_day, resolve_days
from app.entities import conversations
from app.models.db_models import (
    ChatMessageRecord,
    Chunk,
    Document,
    EvalPromotedCase,
    EvaluationRecord,
    TurnInsight,
)
from tests.dashboard_helpers import (
    exchange,
    feedback,
    marker,
    session_at,
    unique_day,
    unique_ip,
)

TODAY = date(2026, 10, 15)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_default_range_is_last_seven_days() -> None:
    days = resolve_days(None, None, today=TODAY, max_days=400)
    assert days.first == date(2026, 10, 9)
    assert days.last == TODAY
    assert len(days.days()) == 7
    assert days.end - days.start == timedelta(days=7)


def test_reversed_or_too_long_range_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_days(date(2026, 10, 2), date(2026, 10, 1), today=TODAY, max_days=400)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        resolve_days(date(2026, 1, 1), date(2026, 1, 11), today=TODAY, max_days=10)
    assert exc.value.status_code == 422


def test_parse_day() -> None:
    assert parse_day(None, "from") is None
    assert parse_day(" ", "from") is None
    assert parse_day("2026-10-09", "from") == date(2026, 10, 9)
    with pytest.raises(HTTPException):
        parse_day("2026-10-09T10:00", "from")


def test_turn_bins_are_contiguous_and_open_ended() -> None:
    bins = conversations.TURN_BINS
    assert bins[0][0] == 1
    for (_, high), (low, _) in zip(bins, bins[1:], strict=False):
        assert high is not None
        assert low == high + 1
    assert bins[-1][1] is None


def test_turn_item_without_assistant_reply_is_all_null() -> None:
    user = ChatMessageRecord(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        turn_index=0,
        role="user",
        content="hi",
        created_at=datetime(2026, 1, 1, 9),
    )
    item = conversations.turn_item(0, user, None, conversations.TurnContext())
    assert item["user_message_id"] == str(user.id)
    assert item["created_at"] == "2026-01-01T09:00:00Z"
    assert item["tokens"] is None
    assert item["sources"] == []
    assert item["stages_ms"] == dict.fromkeys(conversations.STAGE_KEYS)


def test_tokens_include_embeddings_in_total() -> None:
    asst = ChatMessageRecord(
        prompt_tokens=2400,
        completion_tokens=380,
        total_tokens=2780,
        reasoning_tokens=128,
        embedding_tokens=12,
        guardrail_tokens=8,
        token_details={"cached_tokens": 1024},
    )
    assert conversations._tokens(asst) == {
        "prompt": 2400,
        "cached_prompt": 1024,
        "completion": 380,
        "reasoning": 128,
        "embedding": 12,
        "guardrail": 8,
        "total": 2800,
    }
    assert conversations._tokens(ChatMessageRecord()) is None


# ---------------------------------------------------------------------------
# Visitors (real Postgres)
# ---------------------------------------------------------------------------


async def _seed_visitors(db) -> dict:
    d0 = unique_day()
    v1, v2 = unique_ip(), unique_ip()
    # V1: first-ever session on D0 (new), back on D2 (returning).
    s1 = await session_at(db, d0 + timedelta(hours=10), v1)
    _, a1 = await exchange(
        db, s1, 0, d0 + timedelta(hours=10), cost=0.004, latency_ms=900
    )
    s2 = await session_at(db, d0 + timedelta(days=2, hours=15), v1)
    await exchange(
        db, s2, 0, d0 + timedelta(days=2, hours=15), outcome="fallback", cost=0.006
    )
    await feedback(
        db,
        a1,
        2,
        d0 + timedelta(hours=11),
        comment="Out of date",
        reason_tags=["wrong"],
    )
    # V2: first session before the range, active on D1 through it.
    old = await session_at(
        db, d0 - timedelta(days=5), v2, last_activity_at=d0 + timedelta(days=1, hours=8)
    )
    await exchange(db, old, 0, d0 + timedelta(days=1, hours=8))
    return {"d0": d0, "v1": v1, "v2": v2, "s1": s1, "s2": s2}


def _range(d0: datetime, days: int = 3) -> dict[str, str]:
    return {
        "from": d0.date().isoformat(),
        "to": (d0 + timedelta(days=days - 1)).date().isoformat(),
    }


async def test_visitor_stats_exact_numbers(client, db_session) -> None:
    seed = await _seed_visitors(db_session)
    d0 = seed["d0"]
    resp = await client.get("/admin/visitors/stats", params=_range(d0))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["from"] == d0.date().isoformat()
    assert data["tz"] == "UTC"
    kpis = data["kpis"]
    assert kpis["visitors"] == 2
    assert kpis["new_visitors"] == 1
    assert kpis["returning_visitors"] == 1
    assert kpis["sessions"] == 2
    assert kpis["queries"] == 3
    assert kpis["avg_sessions_per_visitor"] == 1.0
    assert data["new_vs_returning"] == [
        {"day": (d0 + timedelta(days=i)).date().isoformat(), "new": n, "returning": r}
        for i, (n, r) in enumerate(((1, 0), (0, 1), (0, 1)))
    ]
    heat = {(c["weekday"], c["hour"]): c["sessions"] for c in data["heatmap"]}
    assert heat == {
        (d0.weekday(), 10): 1,
        ((d0 + timedelta(days=2)).weekday(), 15): 1,
    }


async def test_visitor_stats_active_now_and_empty_range(client, db_session) -> None:
    now = datetime.utcnow()
    session = await session_at(
        db_session, now - timedelta(hours=2), unique_ip(), last_activity_at=now
    )
    await exchange(db_session, session, 0, now - timedelta(minutes=1))
    resp = await client.get("/admin/visitors/stats", params=_range(unique_day(), 2))
    data = resp.json()
    assert data["kpis"]["active_now"] >= 1
    active = data["active_visitors"]
    assert active["active_now"] == data["kpis"]["active_now"]
    assert active["active_now_minutes"] == 5
    assert active["dau"]["value"] >= 1
    assert active["dau"]["days"] == 1
    assert data["kpis"]["visitors"] == 0
    assert data["kpis"]["avg_sessions_per_visitor"] is None
    assert [d["new"] for d in data["new_vs_returning"]] == [0, 0]


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-10-10", "to": "2026-10-09"},
        {"from": "2001-01-01", "to": "2026-10-09"},
        {"from": "yesterday"},
        {"audience": "all"},
    ],
)
async def test_visitor_stats_validation(client, params) -> None:
    assert (await client.get("/admin/visitors/stats", params=params)).status_code == 422


async def test_users_heatmap_slot_filter(client, db_session) -> None:
    seed = await _seed_visitors(db_session)
    d0, v1 = seed["d0"], seed["v1"]
    slot = {"weekday": d0.weekday(), "hour": 10, **_range(d0)}
    hit = await client.get("/admin/users", params={**slot, "q": v1})
    assert hit.status_code == 200, hit.text
    assert [u["ip_address"] for u in hit.json()["items"]] == [v1]
    miss = await client.get("/admin/users", params={**slot, "hour": 11, "q": v1})
    assert miss.json()["items"] == []
    other = await client.get("/admin/users", params={**slot, "q": seed["v2"]})
    assert other.json()["items"] == []


@pytest.mark.parametrize(
    "params",
    [
        {"weekday": 1},
        {"hour": 3},
        {"weekday": 7, "hour": 1},
        {"weekday": 0, "hour": 24},
    ],
)
async def test_users_slot_filter_validation(client, params) -> None:
    assert (await client.get("/admin/users", params=params)).status_code == 422


async def test_users_ignores_range_without_slot(client) -> None:
    resp = await client.get("/admin/users", params={"from": "2026-10-01T00:00:00Z"})
    assert resp.status_code == 200


async def test_visitor_timeline(client, db_session) -> None:
    seed = await _seed_visitors(db_session)
    d0 = seed["d0"]
    resp = await client.get(
        f"/admin/visitors/client-{seed['v1']}/timeline", params=_range(d0)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["visitor_id"] == f"client-{seed['v1']}"
    assert data["ip_address"] == seed["v1"]
    assert [s["session_id"] for s in data["sessions"]] == [
        str(seed["s1"].id),
        str(seed["s2"].id),
    ]
    assert data["sessions"][0]["turns"] == 1
    assert data["sessions"][0]["cost_usd"] == pytest.approx(0.004)
    assert data["sessions"][0]["started_at"].endswith("Z")
    queries = data["queries"]
    assert [q["outcome"] for q in queries] == ["ungrounded", "answered"]
    assert queries[1]["latency_ms"] == 900.0
    assert queries[0]["latency_ms"] is None
    assert queries[1]["query"] == "how many leave days"
    assert [c["cost_usd"] for c in data["cost_series"]] == [
        pytest.approx(0.004),
        0.0,
        pytest.approx(0.006),
    ]
    assert [c["queries"] for c in data["cost_series"]] == [1, 0, 1]
    assert len(data["feedback"]) == 1
    fb = data["feedback"][0]
    assert fb["rating"] == 2
    assert fb["reason_tags"] == ["wrong"]
    assert fb["turn_index"] == 0
    assert fb["request_id"].startswith("b7-")
    assert data["truncated"] == {"sessions": False, "queries": False, "feedback": False}


@pytest.mark.parametrize(
    "visitor_id", ["client-192.0.2.254", str(uuid.uuid4()), "not-a-visitor"]
)
async def test_unknown_visitor_is_404(client, visitor_id) -> None:
    resp = await client.get(f"/admin/visitors/{visitor_id}/timeline")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Conversations (real Postgres)
# ---------------------------------------------------------------------------


async def _seed_conversations(db) -> dict:
    d0 = unique_day()
    tag = marker()
    ip = unique_ip()
    one = await session_at(db, d0 + timedelta(hours=1), ip, title=f"{tag} one")
    await exchange(db, one, 0, d0 + timedelta(hours=1), outcome="blocked")
    three = await session_at(db, d0 + timedelta(days=1), ip, title=f"{tag} three")
    for t in range(3):
        await exchange(
            db,
            three,
            t,
            d0 + timedelta(days=1, minutes=t),
            outcome=None if t else "llm_failed",
        )
    ten = await session_at(db, d0 + timedelta(days=1, hours=5), ip, title=f"{tag} ten")
    for t in range(10):
        await exchange(db, ten, t, d0 + timedelta(days=1, hours=5, minutes=t))
    return {"d0": d0, "tag": tag, "one": one, "three": three, "ten": ten}


async def test_conversation_stats_exact_numbers(client, db_session) -> None:
    seed = await _seed_conversations(db_session)
    d0 = seed["d0"]
    resp = await client.get(
        "/admin/conversations/stats", params={**_range(d0, 2), "q": seed["tag"]}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["totals"] == {"conversations": 3, "turns": 14, "avg_turns": 4.67}
    assert [b["conversations"] for b in data["turns_histogram"]] == [1, 1, 0, 0, 1]
    assert data["turns_histogram"][-1] == {
        "min_turns": 10,
        "max_turns": None,
        "conversations": 1,
    }
    day0, day1 = data["outcome_by_day"]
    assert day0["counts"] == {
        "answered": 0,
        "ungrounded": 0,
        "blocked": 1,
        "conversational": 0,
        "llm_failed": 0,
        "clarify": 0,
    }
    # NULL outcome counts as answered.
    assert day1["counts"]["answered"] == 12
    assert day1["counts"]["llm_failed"] == 1


async def test_conversation_stats_empty_range(client) -> None:
    data = (
        await client.get(
            "/admin/conversations/stats",
            params={**_range(unique_day(), 1), "q": marker()},
        )
    ).json()
    assert data["totals"] == {"conversations": 0, "turns": 0, "avg_turns": None}
    assert data["outcome_by_day"][0]["counts"]["answered"] == 0


async def test_conversation_list_turn_and_outcome_filters(client, db_session) -> None:
    seed = await _seed_conversations(db_session)
    tag, d0 = seed["tag"], seed["d0"]

    rows = (await client.get("/admin/conversations", params={"q": tag})).json()["items"]
    by_id = {r["id"]: r for r in rows}
    assert by_id[str(seed["ten"].id)]["turn_count"] == 10
    assert by_id[str(seed["three"].id)]["outcome_counts"] == {
        "llm_failed": 1,
        "answered": 2,
    }

    mid = await client.get(
        "/admin/conversations", params={"q": tag, "min_turns": 2, "max_turns": 3}
    )
    assert [r["id"] for r in mid.json()["items"]] == [str(seed["three"].id)]
    open_ended = await client.get(
        "/admin/conversations", params={"q": tag, "min_turns": 10}
    )
    assert [r["id"] for r in open_ended.json()["items"]] == [str(seed["ten"].id)]

    blocked = await client.get(
        "/admin/conversations",
        params={"q": tag, "outcome": "blocked", "day": d0.date().isoformat()},
    )
    assert [r["id"] for r in blocked.json()["items"]] == [str(seed["one"].id)]
    wrong_day = await client.get(
        "/admin/conversations",
        params={
            "q": tag,
            "outcome": "blocked",
            "day": (d0 + timedelta(days=1)).date().isoformat(),
        },
    )
    assert wrong_day.json()["total"] == 0
    answered = await client.get(
        "/admin/conversations", params={"q": tag, "outcome": "answered"}
    )
    assert answered.json()["total"] == 2


@pytest.mark.parametrize(
    "params",
    [
        {"min_turns": 4, "max_turns": 3},
        {"min_turns": 0},
        {"day": "2026-10-09"},
        {"outcome": "great"},
        {"outcome": "answered", "day": "09/10/2026"},
    ],
)
async def test_conversation_list_filter_validation(client, params) -> None:
    assert (await client.get("/admin/conversations", params=params)).status_code == 422


async def test_conversation_detail_turns(client, db_session, monkeypatch) -> None:
    d0 = unique_day()
    session = await session_at(db_session, d0, unique_ip(), title="detail")
    await exchange(db_session, session, 0, d0, outcome="conversational", cost=0.0)
    _, asst = await exchange(
        db_session,
        session,
        1,
        d0 + timedelta(minutes=2),
        cost=0.0031,
        latency_ms=1790.4,
        stages={
            "embedding": 41.0,
            "retrieval": 80.2,
            "llm": 1650.7,
            "guardrail_pre": 12.4,
        },
        tokens={
            "prompt": 2400,
            "completion": 380,
            "total": 2780,
            "reasoning": 128,
            "embedding": 12,
            "guardrail": 8,
            "details": {"cached_tokens": 1024},
        },
    )
    document = Document(source_name=f"{marker()}.md", source_type="markdown")
    db_session.add(document)
    await db_session.flush()
    chunk = Chunk(
        document_id=document.id,
        content="Annual leave is 20 days.",
        embedding=[0.0] * get_settings().embedding_dim,
        strategy="structure_aware",
        chunk_index=3,
        chunk_metadata={"section_heading": "Annual leave"},
    )
    db_session.add(chunk)
    await db_session.flush()
    db_session.add(
        EvaluationRecord(
            request_id=asst.request_id,
            message_id=asst.id,
            session_id=session.id,
            query="q",
            answer="a",
            retrieved_chunks=[{"chunk_id": str(chunk.id), "score": 0.82}],
        )
    )
    db_session.add(
        TurnInsight(
            message_id=asst.id,
            session_id=session.id,
            intent="LEAVE",
            bot_quality="PART",
            issue="outdated_content",
            substantive=True,
            handled=False,
            classified_by="rule",
        )
    )
    case_id = f"case-{uuid.uuid4().hex[:8]}"
    db_session.add(
        EvalPromotedCase(
            id=case_id,
            query="q",
            promoted_by="b7@example.com",
            source_message_id=asst.id,
        )
    )
    await feedback(
        db_session, asst, 2, d0 + timedelta(minutes=3), comment="Out of date"
    )
    await db_session.flush()

    resp = await client.get(f"/admin/conversations/{session.id}", params={"turn": 1})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["message_count"] == 4
    assert data["focus_turn"] == 1
    assert [t["turn_index"] for t in data["turns"]] == [0, 1]
    turn = data["turns"][1]
    assert turn["assistant_message_id"] == str(asst.id)
    assert turn["outcome"] == "answered"
    assert turn["stages_ms"] == {
        "guardrail_pre": 12.4,
        "embedding": 41.0,
        "retrieval": 80.2,
        "reranker": None,
        "llm": 1650.7,
        "guardrail_post": None,
    }
    assert turn["total_ms"] == 1790.4
    assert turn["tokens"]["total"] == 2800
    assert turn["tokens"]["cached_prompt"] == 1024
    assert turn["cost_usd"] == pytest.approx(0.0031)
    assert turn["sources"] == [
        {
            "document_id": str(document.id),
            "chunk_id": str(chunk.id),
            "chunk_index": 3,
            "source_name": document.source_name,
            "section_heading": "Annual leave",
            "score": 0.82,
        }
    ]
    assert turn["feedback"][0]["rating"] == 2
    assert turn["feedback"][0]["triage_status"] == "new"
    assert turn["labels"] == {
        "intent": "LEAVE",
        "quality": "PART",
        "issue": "outdated_content",
    }
    assert turn["promoted_case_id"] == case_id
    first = data["turns"][0]
    assert first["outcome"] == "conversational"
    assert first["total_ms"] is None
    assert first["labels"] is None

    missing = await client.get(
        f"/admin/conversations/{session.id}", params={"turn": 99}
    )
    assert missing.json()["focus_turn"] is None
    bad = await client.get(f"/admin/conversations/{session.id}", params={"turn": -1})
    assert bad.status_code == 422


async def _seed_sort_pair(db) -> dict:
    """`early` started first but was active last; `late` is the reverse."""
    d0, tag = unique_day(), marker()
    early_ip, late_ip = unique_ip(), unique_ip()
    early = await session_at(
        db, d0, early_ip, title=f"{tag} early", last_activity_at=d0 + timedelta(days=3)
    )
    late = await session_at(db, d0 + timedelta(days=1), late_ip, title=f"{tag} late")
    return {"tag": tag, "early": early, "late": late, "ips": [early_ip, late_ip]}


@pytest.mark.parametrize(
    ("sort", "first"),
    [
        ("created_at_desc", "late"),
        ("created_at_asc", "early"),
        ("last_activity_desc", "early"),
        ("last_activity_asc", "late"),
    ],
)
async def test_conversation_list_sorts_by_date(client, db_session, sort, first) -> None:
    seed = await _seed_sort_pair(db_session)
    resp = await client.get(
        "/admin/conversations", params={"q": seed["tag"], "sort": sort}
    )
    assert resp.status_code == 200, resp.text
    ids = [r["id"] for r in resp.json()["items"]]
    other = "early" if first == "late" else "late"
    assert ids == [str(seed[first].id), str(seed[other].id)]


@pytest.mark.parametrize(
    ("sort", "first"),
    [
        ("first_seen_desc", 1),
        ("first_seen_asc", 0),
        ("last_active_desc", 0),
        ("last_active_asc", 1),
    ],
)
async def test_visitor_list_sorts_by_date(client, db_session, sort, first) -> None:
    ips = (await _seed_sort_pair(db_session))["ips"]
    # `limit` is capped (USAGE_PAGE_MAX_LIMIT), so walk every page in order.
    page_size = get_settings().usage_page_max_limit
    rows: list[dict] = []
    offset = 0
    while True:
        resp = await client.get(
            "/admin/users",
            params={"sort": sort, "limit": page_size, "offset": offset},
        )
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        rows += [u for u in items if u["ip_address"] in ips]
        if len(items) < page_size:
            break
        offset += page_size
    assert [u["ip_address"] for u in rows] == [ips[first], ips[1 - first]]
    assert all(u["first_seen"] and u["last_active"] for u in rows)
