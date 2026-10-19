"""Phase B5 integration: chat thumbs-down -> review queue -> triage ->
promotion -> golden cases -> JSONL preference pair -> analytics."""

from __future__ import annotations

import json
import uuid

import pytest

from app.core.cache import dashboard_cache
from app.core.rate_limit import _feedback_ip_log, _feedback_user_log
from tests.feedback_helpers import make_exchange, make_session, marker


@pytest.fixture(autouse=True)
def _reset_feedback_rate_limit():
    _feedback_user_log.clear()
    _feedback_ip_log.clear()


async def test_feedback_loop_end_to_end(client, db_session):
    mk = marker()
    actor = f"loop-{uuid.uuid4().hex[:8]}@example.com"
    client.headers["X-Client-Id"] = actor
    before = (await client.get("/admin/feedback/analytics")).json()

    session = await make_session(db_session, actor)
    _u, asst = await make_exchange(
        db_session,
        session,
        turn=1,
        question=f"{mk} How do I rotate the signing keys?",
        answer="Signing keys never need rotation.",
        grounded=False,
        outcome="ungrounded",
        flags=["low_confidence"],
        sources=[{"source_name": "keys.md", "chunk_id": "chunk-keys-1", "score": 0.31}],
        confidence=0.2,
        latency_ms=2100.0,
    )

    # 1. Chat: thumbs-down with reasons and a corrected answer.
    chat = await client.post(
        f"/sessions/{session.id}/messages/{asst.id}/feedback",
        json={
            "rating": 1,
            "comment": "This is wrong",
            "reason_tags": ["wrong", "incomplete"],
            "corrected_answer": "Rotate keys every 90 days via the admin console.",
        },
    )
    assert chat.status_code == 200, chat.text

    # 2. The row is in the queue with the full exchange context.
    queue = await client.get(f"/admin/feedback?q={mk}&triage_status=new")
    assert queue.status_code == 200
    items = queue.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["user_query"].endswith("How do I rotate the signing keys?")
    assert item["answer"] == "Signing keys never need rotation."
    assert item["outcome"] == "ungrounded"
    assert item["grounded"] is False
    assert item["guardrail_flags"] == ["low_confidence"]
    assert item["sources"][0]["chunk_id"] == "chunk-keys-1"
    assert item["latency_ms"] == 2100.0
    assert item["actor"] == actor
    assert item["reason_tags"] == ["wrong", "incomplete"]
    assert item["corrected_answer"].startswith("Rotate keys")

    # 3. Triage: mark reviewed, then promote.
    reviewed = await client.patch(
        f"/admin/feedback/{item['id']}",
        json={"version": item["version"], "triage_status": "reviewed"},
    )
    assert reviewed.status_code == 200, reviewed.text
    promoted = await client.patch(
        f"/admin/feedback/{item['id']}",
        json={
            "version": reviewed.json()["version"],
            "triage_status": "promoted",
            "review_note": "Missing key-rotation doc",
        },
    )
    assert promoted.status_code == 200, promoted.text
    case_id = promoted.json()["promoted_case_id"]
    assert case_id

    # 4. Visible in golden cases.
    golden = await client.get("/admin/feedback/golden?limit=200")
    case = next(c for c in golden.json()["items"] if c["id"] == case_id)
    assert case["source_message_id"] == str(asst.id)
    assert case["reference_answer"].startswith("Rotate keys")
    assert case["expect_grounded"] is False

    # 5. The JSONL export contains it as a complete preference pair.
    export = await client.get("/admin/feedback/export?format=jsonl")
    assert export.status_code == 200
    pairs = [json.loads(line) for line in export.text.splitlines() if line]
    pair = next(p for p in pairs if p["feedback_id"] == item["id"])
    assert pair["chosen"].startswith("Rotate keys")
    assert pair["rejected"] == "Signing keys never need rotation."
    assert pair["source_ids"] == ["chunk-keys-1"]
    assert pair["triage_status"] == "promoted"
    assert pair["complete"] is True

    # 6. Analytics reflect the new signal.
    # Analytics are precomputed: rows written straight to the DB show up
    # once the snapshot refreshes (the jobs worker does it within seconds).
    dashboard_cache.clear()
    after = (await client.get("/admin/feedback/analytics")).json()
    assert after["total"] - before["total"] == 1
    assert after["negative_count"] - before["negative_count"] == 1
    assert after["triage_funnel"]["promoted"] - before["triage_funnel"]["promoted"] == 1
    reasons = {r["key"]: r["count"] for r in after["top_reasons"]}
    reasons_before = {r["key"]: r["count"] for r in before["top_reasons"]}
    assert reasons["incomplete"] - reasons_before.get("incomplete", 0) == 1
    assert (
        after["disagreement"]["negative_ungrounded"]
        - before["disagreement"]["negative_ungrounded"]
        == 1
    )
    assert any(g["query"].startswith(mk) for g in after["knowledge_gaps"])
