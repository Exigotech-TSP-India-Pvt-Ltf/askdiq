"""Phase B4 integration (planchanges4 B4.4): 2k logs + 500 traces end to end.

Seeds a far-past window inside the rolled-back test transaction, then walks
the logs cursor, applies filters, reads the overview, a trace detail, the CSV
export and the live stream, and checks the G1-G13 regression points the new
endpoints touch. Needs the CI Postgres.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.events import TOPIC_GOVERNANCE_INCIDENT, Event, EventBus
from app.governance.overview import overview_payload
from app.governance.stream import GovernanceStreamHub
from app.models.db_models import AuditLog
from tests.conftest import shared_session_runner
from tests.governance_helpers import (
    LEVEL_FIXTURES,
    audit_rows,
    chat_session,
    iso,
    trace_rows,
    unique_window,
)

BASE = "/admin/governance"
LOGS = 2000
TRACES = 500
_OUTCOMES = (
    ("answered", []),
    ("ungrounded", ["groundedness_check_failed"]),
    ("blocked", ["unsafe_input_detected"]),
    ("llm_failed", ["llm_call_failed"]),
    ("answered", ["low_retrieval_confidence"]),
)


@pytest.fixture
async def seeded(db_session):
    start, end = unique_window(days=3)
    # Logs live in day 1, traces (whose query audit rows are logs too) in day 2.
    logs_end = start + timedelta(days=1) - timedelta(seconds=1)
    traces_start = start + timedelta(days=1)
    db_session.add_all(audit_rows(start, LOGS, step=timedelta(seconds=20)))
    session = await chat_session(db_session)
    trace_ids: list[str] = []
    for i in range(TRACES):
        outcome, flags = _OUTCOMES[i % len(_OUTCOMES)]
        rid, rows = trace_rows(
            session,
            traces_start + timedelta(minutes=i),
            total_ms=float(200 + (i % 50) * 300),  # 200 ms .. 14.9 s
            outcome=outcome,
            flags=flags,
            stages={"embedding": 15.0, "retrieval": 40.0, "llm": 120.0 + i},
            query=f"question number {i}",
        )
        db_session.add_all(rows)
        trace_ids.append(rid)
    await db_session.flush()
    return {
        "start": start,
        "logs_end": logs_end,
        "traces_start": traces_start,
        "end": end,
        "traces": trace_ids,
        "session": session,
    }


async def _get(client, path: str, **params):
    response = await client.get(f"{BASE}/{path}", params=params)
    assert response.status_code == 200, response.text
    return response


async def test_logs_cursor_walk_filters_and_counts(client, seeded):
    window = {"from": iso(seeded["start"]), "to": iso(seeded["logs_end"])}
    ids: list[str] = []
    stamps: list[str] = []
    cursor = None
    while True:
        params = {**window, "limit": 200, **({"cursor": cursor} if cursor else {})}
        page = (await _get(client, "logs", **params)).json()
        assert page["total"] == LOGS and not page["total_is_estimate"]
        ids += [i["id"] for i in page["items"]]
        stamps += [i["created_at"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(ids) == len(set(ids)) == LOGS
    assert stamps == sorted(stamps, reverse=True)

    per_level = LOGS // len(LEVEL_FIXTURES)
    assert page["level_counts"] == dict.fromkeys(
        ("CRITICAL", "ALERT", "ERROR", "WARNING", "INFO"), per_level
    )
    crit = (await _get(client, "logs", level="CRITICAL", **window)).json()
    assert crit["total"] == per_level
    assert all(i["summary"] and i["service"] == "query" for i in crit["items"])
    # G11: detail is an object, service is a category, not the raw action.
    assert isinstance(crit["items"][0]["detail"], dict)
    narrow = (
        await _get(
            client,
            "logs",
            **{
                "from": iso(seeded["start"]),
                "to": iso(seeded["start"] + timedelta(seconds=20 * 99)),
            },
        )
    ).json()
    assert narrow["total"] == 100  # inclusive bounds


async def test_logs_filter_uses_index_scan(db_session, seeded):
    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan = (
        (
            await db_session.execute(
                text(
                    "EXPLAIN SELECT id FROM audit_log "
                    "WHERE created_at >= :a AND created_at <= :b "
                    "ORDER BY created_at DESC, id DESC LIMIT 50"
                ),
                {"a": seeded["start"], "b": seeded["end"]},
            )
        )
        .scalars()
        .all()
    )
    plan_text = "\n".join(plan)
    assert "ix_audit_log_created_id" in plan_text, plan_text
    assert "Sort" not in plan_text, plan_text


async def test_traces_page_walk_filters_and_detail(client, seeded):
    window = {"from": iso(seeded["traces_start"]), "to": iso(seeded["end"])}
    seen: list[str] = []
    cursor = None
    while True:
        params = {**window, "limit": 200, **({"cursor": cursor} if cursor else {})}
        page = (await _get(client, "traces/page", **params)).json()
        assert page["total"] == TRACES
        seen += [i["request_id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert sorted(seen) == sorted(seeded["traces"])

    failed = (await _get(client, "traces/page", status="failed", **window)).json()
    assert failed["total"] == TRACES // len(_OUTCOMES)
    assert {i["status"] for i in failed["items"]} == {"failed"}  # G5
    ungrounded = (
        await _get(client, "traces/page", status="ungrounded", **window)
    ).json()
    # outcome=ungrounded rows; answered+low_retrieval_confidence stays "ok".
    assert ungrounded["total"] == TRACES // len(_OUTCOMES)
    slow = (
        await _get(client, "traces/page", latency_bucket="very_slow", **window)
    ).json()
    assert slow["total"] > 0
    assert all(i["total_ms"] >= 10000 for i in slow["items"])
    one = (await _get(client, "traces/page", q="question number 42", **window)).json()
    assert one["total"] == 1

    rid = one["items"][0]["request_id"]
    detail = (await _get(client, f"traces/{rid}")).json()
    assert detail["conversation_id"] == str(seeded["session"].id)
    assert detail["query"] == "question number 42"
    assert detail["stages"]["unit"] == "ms"  # G1
    by_stage = {s["stage"]: s for s in detail["stages"]["stages"]}
    assert by_stage["llm"]["duration_ms"] == 162.0
    assert by_stage["reranker"]["duration_ms"] is None  # G2: null, not 0
    assert "trace_id" not in detail  # G9


async def test_overview_monitoring_and_export(client, db_session, seeded):
    overview = (await _get(client, "overview")).json()
    assert overview["health"]["status"] in {"Healthy", "Degraded", "Incident"}
    assert all(len(k["sparkline"]) == 24 for k in overview["kpis"].values())

    monitoring = (await _get(client, "monitoring")).json()
    assert len(monitoring["hourly"]) == 24
    # G7: counts are labelled with window and source.
    assert all(
        {"value", "window", "source"} == set(c) for c in monitoring["counts"].values()
    )

    response = await _get(
        client,
        "logs/export.csv",
        **{
            "from": iso(seeded["start"]),
            "to": iso(seeded["logs_end"]),
            "level": "ERROR",
        },
    )
    rows = list(csv.reader(io.StringIO(response.text.lstrip("﻿"))))
    assert len(rows) == 1 + LOGS // len(LEVEL_FIXTURES)
    assert {row[1] for row in rows[1:]} == {"ERROR"}
    assert all(json.loads(row[-1])["path"] == "/query" for row in rows[1:])


async def test_stream_delivers_new_incident_to_every_subscriber(db_session, seeded):
    bus = EventBus(dsn="postgresql://unused")
    runner = shared_session_runner(db_session)
    hub = GovernanceStreamHub(
        snapshot_fn=lambda audience: overview_payload(runner, audience),
        interval=60,
        bus=bus,
    )
    gens = [hub.subscribe("overview") for _ in range(5)]
    try:
        first = [await asyncio.wait_for(anext(g), 10) for g in gens]
        assert all(e.event == "snapshot" for e in first)
        ticks = hub.ticks["overview"]

        incident = AuditLog(
            request_id="integration-incident",
            created_at=datetime.utcnow() + timedelta(seconds=30),
            actor="system",
            action="query",
            detail={"guardrail_flags": ["secret_leak_detected"]},
        )
        db_session.add(incident)
        await db_session.flush()
        bus.dispatch(Event(TOPIC_GOVERNANCE_INCIDENT, str(incident.id), "replica-2"))

        for gen in gens:
            async with asyncio.timeout(10):
                while True:
                    event = await anext(gen)
                    if event.event == "incident":
                        break
            assert event.id == str(incident.id)
            assert event.data["level"] == "CRITICAL"
        # One snapshot for five subscribers.
        assert hub.ticks["overview"] == ticks + 1
    finally:
        for gen in gens:
            await gen.aclose()
        await hub.aclose()
