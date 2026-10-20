"""Evaluation page views of the live-eval snapshot (summary, cases, case).

The views are pure functions of the report payload: the summary is the
report without its case lists, the cases page is exactly the slice the page
used to filter and search in the browser, and the routes answer from the same
snapshot as ``GET /metrics/evaluation/live`` (parity).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard import evaluation_views as views
from app.dashboard.window import utcnow
from app.models.db_models import EvaluationRecord

DAY = "2020-02-11"
AT = datetime(2020, 2, 11, 9, 0)


def _case(i: int, **kw: object) -> dict:
    base = {
        "id": f"c{i}",
        "request_id": f"r{i}",
        "type": "answered",
        "query": f"query {i}",
        "answer": f"answer {i}",
        "passed": i % 2 == 0,
        "known_issue": False,
        "grounded": i % 3 != 0,
        "confidence": 0.5,
        "recall": 0.9,
        "citation_precision": 0.8,
        "faithfulness": None,
        "latency_ms": 1200.0,
        "retrieved_chunks": [{"content": "chunk"}],
        "created_at": None,
    }
    base.update(kw)
    return base


def _browser_filter(cases: list[dict], outcome: str, grounded: str, q: str) -> list:
    """The page's former client-side filter (the parity reference)."""
    out = []
    for c in cases:
        if outcome == "passed" and not c["passed"]:
            continue
        if outcome == "failed" and c["passed"]:
            continue
        if grounded == "grounded" and not c["grounded"]:
            continue
        if grounded == "ungrounded" and c["grounded"]:
            continue
        if q.strip():
            n = q.lower()
            if not any(
                n in (c.get(f) or "").lower() for f in ("query", "answer", "id", "type")
            ):
                continue
        out.append(c)
    return out


REPORT = {
    "pass_rate": 0.5,
    "n_cases": 30,
    "cases": [_case(i) for i in range(30)],
    "failed_cases": [],
    "by_case_type": {"answered": {"n": 30}},
}


def test_summary_drops_case_lists_and_adds_outcomes() -> None:
    report = {
        **REPORT,
        "cases": [*REPORT["cases"], _case(99, passed=False, known_issue=True)],
    }
    out = views.summary_view(report)
    assert "cases" not in out
    assert "failed_cases" not in out
    assert out["pass_rate"] == 0.5
    assert out["by_case_type"] == report["by_case_type"]
    # Known issues are neither passed nor failed (as the outcome chart drew).
    assert out["outcomes"] == {"passed": 15, "failed": 15}
    assert "cases" in report  # the snapshot payload is never mutated


@pytest.mark.parametrize(
    ("outcome", "grounded", "q"),
    [
        ("all", "all", ""),
        ("passed", "all", ""),
        ("failed", "ungrounded", ""),
        ("all", "grounded", "QUERY 1"),
        ("all", "all", "answer 2"),
        ("all", "all", "c7"),
        ("all", "all", "nothing matches"),
    ],
)
def test_cases_page_matches_the_former_browser_filter(
    outcome: str, grounded: str, q: str
) -> None:
    expected = _browser_filter(REPORT["cases"], outcome, grounded, q)
    page = views.cases_view(
        REPORT, outcome=outcome, grounded=grounded, q=q.lower(), offset=0, limit=10
    )
    assert page["total"] == len(expected)
    assert [r["id"] for r in page["items"]] == [c["id"] for c in expected[:10]]


def test_cases_rows_are_slim_and_past_the_end_shows_the_last_page() -> None:
    page = views.cases_view(REPORT, offset=1000, limit=25)
    assert page["offset"] == 25
    assert [r["id"] for r in page["items"]] == [f"c{i}" for i in range(25, 30)]
    row = page["items"][0]
    assert set(row) == set(views.ROW_FIELDS)
    assert "answer" not in row
    assert "retrieved_chunks" not in row
    empty = views.cases_view({"cases": []}, offset=50)
    assert empty == {"items": [], "total": 0, "offset": 0, "limit": 25}


def test_find_case_by_id_or_request_id() -> None:
    assert views.find_case(REPORT, "c3")["answer"] == "answer 3"
    assert views.find_case(REPORT, "r4")["id"] == "c4"
    assert views.find_case(REPORT, "nope") is None


def test_view_cache_encodes_once_per_payload_object() -> None:
    cache = views.ViewCache(max_size=2)
    calls: list[int] = []

    def build() -> dict:
        calls.append(1)
        return {"n": len(calls)}

    payload = {"a": 1}
    first = cache.get(("k",), payload, build)
    assert cache.get(("k",), payload, build) == first
    assert len(calls) == 1
    # A recomputed snapshot is a new object: rebuilt, new ETag.
    second = cache.get(("k",), {"a": 1}, build)
    assert len(calls) == 2
    assert second[1] != first[1]


async def _records(db: AsyncSession) -> list[EvaluationRecord]:
    rows = []
    for i, (passed, grounded, chunks) in enumerate(
        [(True, True, [{"content": "c"}]), (False, False, []), (True, True, [])]
    ):
        rec = EvaluationRecord(
            id=uuid.uuid4(),
            request_id=f"ev-views-{uuid.uuid4().hex[:10]}",
            query=f"views query {i}",
            answer=f"views answer {i}",
            passed=passed,
            grounded=grounded,
            faithfulness=4.0,
            retrieval_recall=1.0,
            safety_passed=True,
            guardrail_flags=["pii_leak_detected"] if not passed else [],
            retrieved_chunks=chunks,
            created_at=AT.replace(minute=i),
        )
        db.add(rec)
        rows.append(rec)
    await db.flush()
    return rows


@pytest.mark.asyncio
async def test_views_answer_from_the_same_report(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    rows = await _records(db_session)
    rng = {"audience": "users", "from": DAY, "to": DAY}
    full = (await client.get("/metrics/evaluation/live", params=rng)).json()

    res = await client.get("/metrics/evaluation/live/summary", params=rng)
    assert res.status_code == 200, res.text
    summary = res.json()
    assert "as_of" in summary
    expected = {k: v for k, v in full.items() if k not in views.CASE_LISTS}
    assert {k: v for k, v in summary.items() if k not in ("outcomes", "as_of")} == (
        expected
    )
    assert summary["outcomes"] == {"passed": 2, "failed": 1}

    etag = res.headers["etag"]
    again = await client.get(
        "/metrics/evaluation/live/summary", params=rng, headers={"If-None-Match": etag}
    )
    assert again.status_code == 304

    page = (
        await client.get(
            "/metrics/evaluation/live/cases",
            params={**rng, "outcome": "failed", "q": "VIEWS"},
        )
    ).json()
    assert page["total"] == 1
    assert page["items"][0]["id"] == str(rows[1].id)
    assert "answer" not in page["items"][0]

    # A case with chunks: the report's case.
    with_chunks = (
        await client.get(f"/metrics/evaluation/live/cases/{rows[0].id}", params=rng)
    ).json()
    assert with_chunks == next(c for c in full["cases"] if c["id"] == str(rows[0].id))
    # Without chunks: the record detail (what the drawer then loaded).
    detail = (
        await client.get(f"/metrics/evaluation/live/cases/{rows[1].id}", params=rng)
    ).json()
    record = (await client.get(f"/metrics/evaluation/record/{rows[1].id}")).json()
    assert detail == record
    assert detail["guardrail_flags"] == ["pii_leak_detected"]

    missing = await client.get("/metrics/evaluation/live/cases/nope", params=rng)
    assert missing.status_code == 404
    bad = await client.get(
        "/metrics/evaluation/live/cases", params={**rng, "outcome": "maybe"}
    )
    assert bad.status_code == 422


def test_only_payloads_reaching_today_follow_new_evaluations() -> None:
    as_of = datetime(2020, 2, 11, 9, 0, tzinfo=UTC)
    assert views.payload_may_be_behind({"from": None, "to": None}, as_of)
    today = utcnow().date().isoformat()
    assert views.payload_may_be_behind({"from": DAY, "to": today}, as_of)
    assert not views.payload_may_be_behind({"from": DAY, "to": DAY}, as_of)
    assert not views.payload_may_be_behind({"to": None}, None)
    # Table times are naive UTC.
    assert views.is_after(datetime(2020, 2, 11, 9, 1), as_of)
    assert not views.is_after(datetime(2020, 2, 11, 9, 0), as_of)
    # Never a rebuild loop: not for future rows, not right after a rebuild.
    now = datetime.now(UTC)
    future = (now + timedelta(hours=5)).replace(tzinfo=None)
    assert not views.is_after(future, as_of)
    assert not views.is_after(now.replace(tzinfo=None), now)


@pytest.mark.asyncio
async def test_rebuild_in_background_runs_once_per_key() -> None:
    calls: list[int] = []
    gate = asyncio.Event()

    async def build() -> None:
        calls.append(1)
        await gate.wait()

    views.rebuild_in_background("k-test", build)
    views.rebuild_in_background("k-test", build)
    await asyncio.sleep(0)
    gate.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert calls == [1]
