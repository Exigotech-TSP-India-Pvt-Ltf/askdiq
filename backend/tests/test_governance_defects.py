"""Regression tests for the Governance defects G1-G9 (one test per defect id).

Pure-function tests run anywhere; endpoint tests need the CI Postgres
(``client`` / ``db_session`` fixtures) and seed their own rows inside the
rolled-back test transaction.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete

from app.api.routes.admin import _log_level_for
from app.core.middleware import METRICS
from app.governance.audience import invalidate_audience_cache
from app.governance.live_cache import clear_live_caches
from app.governance.log_summary import SUMMARIZERS, service_for, summarize
from app.governance.monitoring import derive_trace_status, stage_latency_summary
from app.models import db_models
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    LatencyRecord,
)
from main import app

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _latency(total: float, **stages: float | None) -> LatencyRecord:
    return LatencyRecord(
        request_id=f"gov-{uuid.uuid4().hex[:16]}",
        created_at=datetime.utcnow(),
        total_ms=total,
        **{f"{k}_ms": v for k, v in stages.items()},
    )


async def _session_row(db_session, ip: str | None = None) -> ChatSession:
    now = datetime.utcnow()
    session = ChatSession(
        id=uuid.uuid4(),
        user_email="gov-defects@example.com",
        title="t",
        created_at=now,
        last_activity_at=now,
        expires_at=now + timedelta(hours=1),
        created_ip=ip,
    )
    db_session.add(session)
    await db_session.flush()
    return session


def _turn(
    session: ChatSession,
    request_id: str,
    outcome: str | None,
    flags: list[str] | None = None,
) -> ChatMessageRecord:
    return ChatMessageRecord(
        session_id=session.id,
        turn_index=0,
        role="assistant",
        content="a",
        request_id=request_id,
        outcome=outcome,
        guardrail_flags=flags,
    )


async def _monitoring(client, audience: str = "overview") -> dict:
    resp = await client.get(f"/admin/governance/monitoring?audience={audience}")
    assert resp.status_code == 200
    return resp.json()


# --------------------------------------------------------------------------
# G1 / G2: units in ms, null stage averages
# --------------------------------------------------------------------------


def test_g1_stage_latency_is_exact_ms_with_unit():
    rows = [
        _latency(1000, embedding=10, retrieval=20, llm=600),
        _latency(2000, embedding=30, retrieval=40, llm=800),
    ]
    summary = stage_latency_summary(rows)
    assert summary["embedding"] == {"avg_ms": 20.0, "sample_count": 2, "unit": "ms"}
    assert summary["retrieval"]["avg_ms"] == 30.0
    assert summary["llm"]["avg_ms"] == 700.0


def test_g2_stage_without_samples_is_null_not_zero():
    summary = stage_latency_summary([_latency(500, retrieval=5.0)])
    assert summary["reranker"] == {"avg_ms": None, "sample_count": 0, "unit": "ms"}
    assert summary["embedding"]["avg_ms"] is None
    assert stage_latency_summary([])["llm"]["sample_count"] == 0


async def test_g1_g2_monitoring_endpoint_units_and_nulls(client, db_session):
    await db_session.execute(delete(LatencyRecord))
    db_session.add_all(
        [
            _latency(1000, embedding=10, retrieval=20, llm=600),
            _latency(2000, embedding=30, retrieval=40, llm=800),
        ]
    )
    await db_session.flush()

    data = await _monitoring(client)
    assert data["latency_unit"] == "ms"
    assert data["stage_latency"]["embedding"] == {
        "avg_ms": 20.0,
        "sample_count": 2,
        "unit": "ms",
    }
    assert data["embedding_latency_ms"] == 20.0
    assert data["llm_latency_ms"] == 700.0
    assert data["stage_latency"]["reranker"]["avg_ms"] is None
    assert data["stage_latency"]["reranker"]["sample_count"] == 0
    assert data["reranker_latency_ms"] is None


# --------------------------------------------------------------------------
# G3: Overall and Monitoring share one total-latency field
# --------------------------------------------------------------------------


async def test_g3_total_latency_matches_monitoring_average(client, db_session):
    await db_session.execute(delete(LatencyRecord))
    db_session.add_all([_latency(1000, llm=0.1), _latency(1200, llm=0.2)])
    await db_session.flush()

    data = await _monitoring(client)
    assert data["total_latency_ms"] == 1100.0
    assert data["total_latency_ms"] == data["avg_latency_ms"]
    # The stage value the Overall tab used to show is a different metric.
    assert data["llm_latency_ms"] != data["total_latency_ms"]


# --------------------------------------------------------------------------
# G4: real traces total
# --------------------------------------------------------------------------


async def test_g4_traces_total_is_count_not_page_length(client, db_session):
    await db_session.execute(delete(LatencyRecord))
    db_session.add_all([_latency(100 + i) for i in range(150)])
    await db_session.flush()

    resp = await client.get("/admin/governance/traces")
    assert resp.status_code == 200
    assert len(resp.json()) == 100
    assert resp.headers["X-Total-Count"] == "150"
    assert (await _monitoring(client))["traces_total"] == 150


# --------------------------------------------------------------------------
# G5: derived trace status
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "flags", "expected"),
    [
        ("answered", [], "ok"),
        ("conversational", [], "ok"),
        ("ungrounded", ["groundedness_check_failed"], "ungrounded"),
        ("fallback", [], "ungrounded"),
        ("blocked", ["unsafe_input_detected"], "blocked"),
        ("llm_failed", ["llm_call_failed"], "failed"),
        ("empty_llm_output", ["empty_llm_output"], "failed"),
        (None, ["llm_call_failed"], "failed"),
        (None, ["spam_detected"], "blocked"),
        (None, ["possible_hallucination"], "ungrounded"),
        (None, [], "ok"),
        (None, None, "unknown"),
    ],
)
def test_g5_status_from_outcome_and_flags(outcome, flags, expected):
    assert derive_trace_status(outcome, flags) == expected


async def test_g5_traces_endpoint_returns_derived_status(client, db_session):
    await db_session.execute(delete(LatencyRecord))
    session = await _session_row(db_session)
    cases = {
        "answered": "ok",
        "ungrounded": "ungrounded",
        "blocked": "blocked",
        "llm_failed": "failed",
    }
    expected: dict[str, str] = {}
    for outcome, status in cases.items():
        rec = _latency(100)
        db_session.add(rec)
        db_session.add(_turn(session, rec.request_id, outcome))
        expected[rec.request_id] = status
    orphan = _latency(100)  # no persisted turn
    db_session.add(orphan)
    expected[orphan.request_id] = "unknown"
    await db_session.flush()

    resp = await client.get("/admin/governance/traces?limit=500")
    assert resp.status_code == 200
    got = {t["request_id"]: t["status"] for t in resp.json()}
    assert got == expected
    outcomes = {t["request_id"]: t["outcome"] for t in resp.json()}
    assert outcomes[orphan.request_id] is None


# --------------------------------------------------------------------------
# G6: persisted, audience-scoped error rate
# --------------------------------------------------------------------------


async def test_g6_error_rate_is_persisted_not_in_memory(
    client, db_session, monkeypatch
):
    before = (await _monitoring(client))["error_counts"]
    session = await _session_row(db_session)
    for outcome in ("llm_failed", "answered", "answered"):
        db_session.add(_turn(session, f"gov-{uuid.uuid4().hex[:12]}", outcome))
    db_session.add(
        AuditLog(
            request_id="gov-5xx",
            actor="system",
            action="server_error",
            detail={"path": "/query", "status_code": 500},
        )
    )
    await db_session.flush()
    clear_live_caches()  # the memory copy follows within one interval

    # Simulate an app restart: in-memory counters are back to zero.
    monkeypatch.setitem(METRICS, "errors", 0)
    monkeypatch.setitem(METRICS, "requests", 0)
    data = await _monitoring(client)
    counts = data["error_counts"]

    assert counts["llm_failed"] == before["llm_failed"] + 1
    assert counts["server_errors"] == before["server_errors"] + 1
    assert counts["requests"] == before["requests"] + 4
    assert data["error_rate"] == pytest.approx(
        counts["errors"] * 100 / counts["requests"]
    )
    assert data["error_rate"] > 0


async def test_g6_error_rate_is_audience_scoped(client, db_session):
    test_ip = "203.0.113.61"
    db_session.add(db_models.TestIpConfig(ip_address=test_ip, description="g6"))
    await db_session.flush()
    invalidate_audience_cache()
    try:
        testing_before = (await _monitoring(client, "testing"))["error_counts"]
        users_before = (await _monitoring(client, "users"))["error_counts"]

        session = await _session_row(db_session, ip=test_ip)
        request_id = f"gov-{uuid.uuid4().hex[:12]}"
        db_session.add(_turn(session, request_id, "llm_failed"))
        db_session.add(
            AuditLog(
                request_id=request_id,
                actor="gov-defects@example.com",
                action="query",
                detail={"outcome": "llm_failed"},
                ip_address=test_ip,
            )
        )
        await db_session.flush()
        # The classification only knows IPs already present in the DB.
        invalidate_audience_cache()

        testing = (await _monitoring(client, "testing"))["error_counts"]
        users = (await _monitoring(client, "users"))["error_counts"]
        assert testing["llm_failed"] == testing_before["llm_failed"] + 1
        assert users["llm_failed"] == users_before["llm_failed"]
    finally:
        invalidate_audience_cache()


# --------------------------------------------------------------------------
# G7: every count carries window and source
# --------------------------------------------------------------------------


async def test_g7_counts_are_labelled_with_window_and_source(client):
    data = await _monitoring(client)
    counts = data["counts"]
    assert {
        "api_requests",
        "queries_24h",
        "active_users",
        "active_queries",
        "errors",
        "traces_total",
    } <= set(counts)
    for name, entry in counts.items():
        assert set(entry) == {"value", "window", "source"}, name
        assert entry["window"] and entry["source"], name
    assert counts["queries_24h"]["value"] == data["queries_24h"]
    assert counts["queries_24h"]["window"] == "24h"
    assert data["error_counts"]["window"] == "24h"
    assert "audit_log" in data["error_counts"]["source"]


# --------------------------------------------------------------------------
# G9: duplicate trace_id dropped, documented in OpenAPI
# --------------------------------------------------------------------------


def test_g9_openapi_documents_trace_id_removal():
    paths = app.openapi()["paths"]
    for path in ("/admin/governance/traces", "/admin/governance/logs"):
        description = paths[path]["get"]["description"]
        assert "trace_id" in description
        assert "request_id" in description


async def test_g9_trace_and_log_items_have_no_trace_id(client, db_session):
    db_session.add(_latency(100))
    db_session.add(
        AuditLog(request_id="gov-g9", actor="system", action="query", detail={})
    )
    await db_session.flush()
    traces = (await client.get("/admin/governance/traces")).json()
    logs = (await client.get("/admin/governance/logs")).json()["items"]
    assert traces and logs
    assert all("trace_id" not in t for t in traces)
    assert all("trace_id" not in item for item in logs)


# --------------------------------------------------------------------------
# G11: structured log messages
# --------------------------------------------------------------------------

_SUMMARY_CASES: dict[str, tuple[dict, str]] = {
    "query": (
        {
            "query": "What is X?",
            "outcome": "answered",
            "source_ids": ["a", "b"],
            "guardrail_flags": [],
        },
        'Query "What is X?" · answered · 2 sources',
    ),
    "ingest": (
        {
            "source_name": "a.md",
            "status": "updated",
            "chunks_added": 3,
            "chunks_kept": 5,
            "chunks_removed": 1,
        },
        "Ingested a.md (updated): +3 / =5 / -1 chunks",
    ),
    "document_delete": (
        {"source_name": "a.md", "chunks_removed": 4},
        "Deleted document a.md (4 chunks)",
    ),
    "user_feedback": ({"rating": 2, "comment": "meh"}, 'Feedback 2/5: "meh"'),
    "negative_feedback_spike": (
        {"negative_rate": 0.4, "total_1h": 10},
        "Negative feedback spike: 40% of 10 ratings in 1h",
    ),
    "feedback_promoted_to_eval_case": (
        {"case_id": "c-1"},
        "Feedback promoted to eval case c-1",
    ),
    "online_eval": (
        {"guardrail_flags": ["online_unfaithful"]},
        "Online evaluation flagged answer [online_unfaithful]",
    ),
    "test_ip_added": ({"ip_address": "10.0.0.1"}, "Test IP 10.0.0.1 added"),
    "test_ip_updated": ({"ip_address": "10.0.0.1"}, "Test IP 10.0.0.1 updated"),
    "test_ip_removed": ({"ip_address": "10.0.0.1"}, "Test IP 10.0.0.1 removed"),
    "usage_export": (
        {"tab": "llm", "audience": "users"},
        "Usage export (llm, audience users)",
    ),
    "governance_logs_export": (
        {"filters": {"level": "ERROR", "q": "", "audience": "overview"}},
        "Governance logs export (audience=overview, level=ERROR)",
    ),
    "rate_limited": (
        {"scope": "per IP", "limit": 30, "retry_after_s": 12},
        "Rate limited (per IP): limit 30/min, retry in 12s",
    ),
    "server_error": ({"path": "/query", "status_code": 503}, "HTTP 503 on /query"),
    "llm_call_failed": (
        {
            "error_type": "APITimeoutError",
            "attempt": 1,
            "max_attempts": 2,
            "will_retry": True,
        },
        "LLM call failed: APITimeoutError (attempt 1/2) (retrying)",
    ),
    "llm_retry": (
        {"attempt": 2, "max_attempts": 2, "reason": "empty_llm_output"},
        "LLM retry 2/2 after empty_llm_output",
    ),
    "session_created": ({"session_id": "s1"}, "Session s1 created"),
    "session_expired": ({"session_id": "s1"}, "Session s1 expired"),
    "session_resumed": (
        {"session_id": "s1", "idle_minutes": 90},
        "Session s1 resumed after 90 min idle",
    ),
    "session_rotated": (
        {"from_session_id": "a", "to_session_id": "b", "reason": "expired"},
        "Session rotated (expired): a -> b",
    ),
}


def test_g11_every_registered_action_has_a_summary_case():
    assert set(_SUMMARY_CASES) == set(SUMMARIZERS)


@pytest.mark.parametrize("action", sorted(_SUMMARY_CASES))
def test_g11_summary_per_action(action):
    detail, expected = _SUMMARY_CASES[action]
    assert summarize(action, detail) == expected


def test_g11_unknown_action_and_bad_detail_fall_back_safely(monkeypatch):
    assert summarize("weird_action", {"b": 1, "a": 2}) == "Weird action (a, b)"
    assert summarize("weird_action", None) == "Weird action"
    assert summarize("", "not-a-dict") == "Event"  # type: ignore[arg-type]
    monkeypatch.setitem(SUMMARIZERS, "boom", lambda _d: str(1 / 0))
    assert summarize("boom", {"k": 1}) == "Boom (k)"
    assert len(summarize("query", {"query": "x" * 1000})) == 200


@pytest.mark.parametrize(
    ("action", "service"),
    [
        ("query", "query"),
        ("llm_call_failed", "query"),
        ("session_rotated", "query"),
        ("session_resumed", "query"),
        ("ingest", "ingest"),
        ("document_delete", "ingest"),
        ("user_feedback", "feedback"),
        ("test_ip_added", "admin"),
        ("usage_export", "admin"),
        ("rate_limited", "system"),
        ("server_error", "system"),
        ("login_failed", "auth"),
        ("auth_token_rejected", "auth"),
        ("something_new", "system"),
    ],
)
def test_g11_service_category_map(action, service):
    assert service_for(action) == service


def test_g11_levels_for_new_actions_keep_flag_precedence():
    assert _log_level_for({"guardrail_flags": ["llm_call_failed"]}) == "CRITICAL"
    assert _log_level_for({}, "server_error") == "ERROR"
    assert _log_level_for({}, "rate_limited") == "WARNING"
    assert _log_level_for({}, "session_created") == "INFO"


async def test_g11_logs_endpoint_returns_structured_items(client, db_session):
    request_id = f"gov-{uuid.uuid4().hex[:12]}"
    db_session.add(
        AuditLog(
            request_id=request_id,
            actor="gov-defects@example.com",
            action="user_feedback",
            detail={"rating": 2, "comment": "meh"},
        )
    )
    await db_session.flush()

    body = (await client.get("/admin/governance/logs")).json()
    item = next(i for i in body["items"] if i["request_id"] == request_id)
    assert item["service"] == "feedback"
    assert item["action"] == "user_feedback"
    assert item["summary"] == 'Feedback 2/5: "meh"'
    assert item["detail"] == {"rating": 2, "comment": "meh"}
    assert isinstance(item["message"], str)  # deprecated, kept for compat
