"""Azure Monitor metrics source + Azure vs AskDIQ reconciliation (Phase 2).

Recorded-shape Metrics - List responses (api-version 2023-10-01, placeholder
ids) in tests/fixtures/azure are served through ``httpx.MockTransport``;
nothing calls Azure. Rows are seeded in 2031 so no other test's data falls
in the windows. The DB tests run in CI.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import catalogue as c
from app.alerts import rules
from app.alerts.catalogue import Severity
from app.azure_insights import metrics as m
from app.azure_insights import reconciliation as rec
from app.azure_insights import scheduler
from app.azure_insights import status as sync_status
from app.azure_insights.metrics import MetricPoint, PreviewState
from app.core.config import get_settings
from app.core.events import TOPIC_CONSOLE_CHANGED, TOPIC_SNAPSHOT_UPDATED
from app.dashboard import snapshots
from app.jobs.queue import JobDeferredError
from app.models.db_models import (
    AuditLog,
    AzureMetricPoint,
    ChatMessageRecord,
    ChatSession,
    InsightLlmUsage,
    LatencyRecord,
    ModelSettings,
)
from tests.conftest import shared_session_factory, shared_session_runner

FIXTURES = Path(__file__).parent / "fixtures" / "azure"
POLL_NOW = datetime(2031, 3, 10, 10, 7, 30, tzinfo=UTC)
RECON_NOW = datetime(2031, 3, 10, 10, 30, tzinfo=UTC)
LLM, EMBED, OTHER = "gpt-5-mini", "text-embedding-3-small", "playground-gpt-4o"
AZURE_IDS = {
    "azure_tenant_id": "11111111-1111-4111-8111-111111111111",
    "azure_client_id": "22222222-2222-4222-8222-222222222222",
    "azure_client_secret": "fixture-secret-value-0123456789",
    "azure_subscription_id": "33333333-3333-4333-8333-333333333333",
    "azure_resource_group": "rg-askdiq",
    "azure_openai_resource_name": "askdiq-foundry",
    "azure_openai_region": "australiaeast",
}
TOKENS = "ProcessedPromptTokens,GeneratedTokens,TokenTransaction"
REQUESTS = "AzureOpenAIRequests"
LATENCY = (
    "AzureOpenAITTLTInMS,AzureOpenAINormalizedTTFTInMS,AzureOpenAIAvailabilityRate"
)
COST = "FoundryModelEstimatedCost"
RESPONSES = {
    (TOKENS, "PT1M"): "metrics_tokens_pt1m.json",
    (REQUESTS, "PT1M"): "metrics_requests_pt1m.json",
    (LATENCY, "PT1M"): "metrics_latency_pt1m.json",
    (COST, "PT1M"): "metrics_cost_empty.json",
    (TOKENS, "PT1H"): "metrics_tokens_pt1h.json",
    (REQUESTS, "PT1H"): "metrics_requests_pt1h.json",
    (LATENCY, "PT1H"): "metrics_latency_pt1h.json",
    (COST, "PT1H"): "metrics_cost_pt1h.json",
}


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class AzureMock:
    """Answers each Metrics - List call with its recorded response."""

    def __init__(self, overrides: dict[tuple[str, str], httpx.Response] | None = None):
        self.calls: list[dict[str, str]] = []
        self.paths: list[str] = []
        self.overrides = overrides or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append(params)
        self.paths.append(request.url.path)
        key = (params["metricnames"], params["interval"])
        if key in self.overrides:
            return self.overrides[key]
        return httpx.Response(200, json=fixture(RESPONSES[key]))

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


class FakeToken:
    async def token(self) -> str:
        return "test-access-token"

    def invalidate(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Unit: request parameters, parsing, windows, Preview state, drift math
# ---------------------------------------------------------------------------


def test_request_params_match_the_plan_exactly() -> None:
    start = datetime(2031, 3, 10, 8, 8, tzinfo=UTC)
    end = datetime(2031, 3, 10, 10, 8, tzinfo=UTC)
    tokens = m.request_params(m.CALL_TOKENS, "PT1M", start, end)
    assert tokens == {
        "api-version": "2023-10-01",
        "metricnamespace": "Microsoft.CognitiveServices/accounts",
        "metricnames": TOKENS,
        "aggregation": "Total",
        "interval": "PT1M",
        "timespan": "2031-03-10T08:08:00Z/2031-03-10T10:08:00Z",
        "$filter": "ModelDeploymentName eq '*'",
        "top": "50",
        "AutoAdjustTimegrain": "true",
        "ValidateDimensions": "false",
    }
    requests = m.request_params(m.CALL_REQUESTS, "PT1H", start, end)
    assert requests["$filter"] == "ModelDeploymentName eq '*' and StatusCode eq '*'"
    latency = m.request_params(m.CALL_LATENCY, "PT1M", start, end)
    assert (latency["metricnames"], latency["aggregation"]) == (
        LATENCY,
        "Average,Maximum",
    )
    cost = m.request_params(m.CALL_COST, "PT1M", start, end)
    assert (cost["metricnames"], cost["aggregation"]) == (COST, "Total")
    assert m.metrics_path("/subscriptions/x/acc") == (
        "/subscriptions/x/acc/providers/Microsoft.Insights/metrics"
    )


def test_parse_tokens_by_name_and_dimension_metadata() -> None:
    parsed = m.parse_response(fixture("metrics_tokens_pt1m.json"), "PT1M")
    assert parsed.grain == "PT1M" and parsed.failed == {}
    # Buckets without any aggregation (no data) are skipped; zeros are kept.
    assert len(parsed.points) == 18
    by = {(p.deployment, p.metric, p.ts.minute): p.total for p in parsed.points}
    assert by[(LLM, m.PROMPT_TOKENS, 1)] == 1500
    assert by[(EMBED, m.PROMPT_TOKENS, 1)] == 0
    assert (OTHER, m.PROMPT_TOKENS, 0) not in by
    assert {p.status_code for p in parsed.points} == {""}
    assert all(p.ts.tzinfo is not None for p in parsed.points)


def test_parse_requests_keeps_the_status_code() -> None:
    parsed = m.parse_response(fixture("metrics_requests_pt1m.json"), "PT1M")
    assert len(parsed.points) == 10
    throttled = sum(p.total or 0 for p in parsed.points if p.status_code == "429")
    assert throttled == 3
    assert {p.status_code for p in parsed.points} == {"200", "429"}


def test_parse_latency_averages_and_failed_metrics() -> None:
    minute = m.parse_response(fixture("metrics_latency_pt1m.json"), "PT1M")
    assert len(minute.points) == 11
    ttlt = [p for p in minute.points if p.metric == m.TTLT_MS and p.deployment == LLM]
    assert next((p.average, p.maximum, p.total) for p in ttlt) == (1200, 2000, None)
    hour = m.parse_response(fixture("metrics_latency_pt1h.json"), "PT1M")
    assert hour.grain == "PT1H"  # the response's interval wins
    assert hour.failed == {m.TTFT_MS: "MetricNotFound"}
    assert all(p.metric != m.TTFT_MS for p in hour.points)


def test_parse_drops_an_auto_adjusted_grain_and_bad_items() -> None:
    body = fixture("metrics_tokens_pt1m.json")
    assert m.parse_response({**body, "interval": "PT5M"}, "PT1M").points == []
    junk = {
        "interval": "PT1M",
        "value": [
            None,
            {"name": {}, "timeseries": []},
            {
                "name": {"value": m.PROMPT_TOKENS},
                "timeseries": [
                    {"metadatavalues": [], "data": [{"timeStamp": "x", "total": 1}]},
                    {
                        "metadatavalues": [
                            {"name": {"value": "ModelDeploymentName"}, "value": LLM}
                        ],
                        "data": [
                            {"timeStamp": "bad", "total": 1},
                            {"timeStamp": "2031-03-10T10:00:00Z", "total": True},
                            {"timeStamp": "2031-03-10T10:01:00Z", "total": 2},
                        ],
                    },
                ],
            },
        ],
    }
    points = m.parse_response(junk, "PT1M").points
    assert [(p.deployment, p.total) for p in points] == [(LLM, 2.0)]


def test_poll_windows_backfill_once_a_utc_day() -> None:
    first = m.poll_windows(POLL_NOW, None)
    assert first == [
        (
            "PT1M",
            datetime(2031, 3, 10, 8, 8, tzinfo=UTC),
            datetime(2031, 3, 10, 10, 8, tzinfo=UTC),
        ),
        (
            "PT1H",
            datetime(2031, 3, 7, 11, tzinfo=UTC),
            datetime(2031, 3, 10, 11, tzinfo=UTC),
        ),
    ]
    assert len(m.poll_windows(POLL_NOW, POLL_NOW.date())) == 1
    assert len(m.poll_windows(POLL_NOW, POLL_NOW.date() - timedelta(days=1))) == 2


def test_preview_cost_metric_auto_disables_after_three_empty_days() -> None:
    d0 = date(2031, 3, 1)
    state = PreviewState()
    state.observe(d0, has_data=False)
    state.observe(d0 + timedelta(days=2), has_data=False)
    assert not state.disabled and state.empty_since == d0
    state.observe(d0 + timedelta(days=3), has_data=False)
    assert state.disabled
    # Disabled: skipped on polls, re-probed weekly on the daily backfill.
    assert not state.should_call(d0 + timedelta(days=3), backfill=False)
    assert state.should_call(d0 + timedelta(days=3), backfill=True)
    state.observe(d0 + timedelta(days=3), has_data=False)
    assert not state.should_call(d0 + timedelta(days=5), backfill=True)
    assert state.should_call(d0 + timedelta(days=10), backfill=True)
    state.observe(d0 + timedelta(days=10), has_data=True)
    assert (state.disabled, state.empty_since, state.last_seen) == (
        False,
        None,
        d0 + timedelta(days=10),
    )
    again = PreviewState.from_detail({"preview_cost": state.as_detail()})
    assert again == state
    assert PreviewState.from_detail({"preview_cost": "junk"}) == PreviewState()


def test_drift_math() -> None:
    assert rec.drift_pct(100, 90) == 10.0
    assert rec.drift_pct(100, 110) == 10.0
    assert rec.drift_pct(0, 5) == 100.0
    assert rec.drift_pct(0, 0) is None
    assert rec.drift_pct(None, 5) is None
    assert rec.drift_pct(12, 7) == 41.67
    assert rec.cell(100, 89, 10).drifted
    assert not rec.cell(100, 95, 10).drifted
    assert not rec.cell(100, 0, 10, flag=False).drifted
    assert rec.cell(100, 89, 10).delta == 11
    assert rec.not_seen(100, 120) == 0
    assert rec.not_seen(None, 1) is None


def test_unused_deployment_is_never_flagged() -> None:
    azure = rec.AzureBucket()
    azure.add(m.REQUESTS, "200", (50.0, None, None))
    other = rec.build_row(OTHER, "other", None, azure, None, 10.0)
    assert other.requests.azure == 50 and other.requests.askdiq is None
    assert not other.drifted
    used = rec.build_row(LLM, "llm", None, azure, None, 10.0)
    assert used.requests.askdiq == 0 and used.requests.drift_pct == 100
    assert used.drifted
    assert used.throttled.azure == 0  # status split: no 429 series = none


def test_series_and_summary_routes_are_documented() -> None:
    from main import app  # noqa: PLC0415

    paths = app.openapi()["paths"]
    summary = paths["/admin/azure/metrics/summary"]["get"]
    series = paths["/admin/azure/metrics/series"]["get"]
    assert summary["summary"] == "Azure vs AskDIQ reconciliation"
    assert series["summary"] == "Azure Monitor metric series"
    for op in (summary, series):
        assert {"401", "403", "422"} <= set(op["responses"])
    names = {p["name"] for p in series["parameters"]}
    assert {"metric", "deployment", "range", "grain"} <= names


# ---------------------------------------------------------------------------
# Poll job (Postgres, CI)
# ---------------------------------------------------------------------------


@pytest.fixture
def azure_on(monkeypatch) -> None:
    cfg = get_settings()
    for name, value in {**AZURE_IDS, "azure_insights_enabled": True}.items():
        monkeypatch.setattr(
            cfg, name, SecretStr(value) if name == "azure_client_secret" else value
        )
    monkeypatch.setattr(scheduler, "get_token_provider", lambda _s=None: FakeToken())


@pytest.fixture
def published(monkeypatch) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []

    async def fake_publish(topic: str, item_id: str) -> None:
        seen.append((topic, str(item_id)))

    monkeypatch.setattr(snapshots, "publish_event", fake_publish)
    monkeypatch.setattr(scheduler, "publish_event", fake_publish)
    return seen


async def poll(
    db: AsyncSession, mock: AzureMock, now: datetime = POLL_NOW
) -> scheduler.SourceResult | None:
    return await scheduler.run_source(
        m.SOURCE_NAME,
        now=now,
        session_factory=shared_session_factory(db),
        gather=shared_session_runner(db).gather,
        transport=mock.transport,
        rearm=False,
    )


async def stored(db: AsyncSession) -> list[tuple]:
    rows = (
        await db.execute(
            select(
                AzureMetricPoint.deployment,
                AzureMetricPoint.metric,
                AzureMetricPoint.status_code,
                AzureMetricPoint.grain,
                AzureMetricPoint.ts,
                AzureMetricPoint.total,
                AzureMetricPoint.average,
                AzureMetricPoint.maximum,
            )
            .where(AzureMetricPoint.ts >= datetime(2031, 1, 1, tzinfo=UTC))
            .order_by(*AzureMetricPoint.__table__.primary_key.columns)
        )
    ).all()
    return [tuple(r) for r in rows]


async def status_row(db: AsyncSession) -> Any:
    return (await sync_status.read_all(db)).get(m.SOURCE_NAME)


async def test_poll_persists_counts_calls_and_records_status(
    db_session, azure_on, published
) -> None:
    mock = AzureMock()
    result = await poll(db_session, mock)
    assert result is not None
    # First poll: A-D at PT1M plus the daily PT1H backfill.
    assert [(c["metricnames"], c["interval"]) for c in mock.calls] == [
        (TOKENS, "PT1M"),
        (REQUESTS, "PT1M"),
        (LATENCY, "PT1M"),
        (COST, "PT1M"),
        (TOKENS, "PT1H"),
        (REQUESTS, "PT1H"),
        (LATENCY, "PT1H"),
        (COST, "PT1H"),
    ]
    account = get_settings().azure_account_resource_id
    assert set(mock.paths) == {f"{account}/providers/Microsoft.Insights/metrics"}
    rows = await stored(db_session)
    assert len(rows) == result.rows == 18 + 10 + 11 + 6 + 4 + 4 + 2
    row = await status_row(db_session)
    assert row.state == "ok"
    assert sync_status.calls_this_month(row, datetime.now(UTC).date()) == 8
    assert row.detail["last_backfill"] == "2031-03-10"
    assert row.detail["missing_metrics"] == {m.TTFT_MS: "MetricNotFound"}
    assert row.detail["deployments"] == sorted({LLM, EMBED, OTHER})
    assert row.detail["preview_cost"]["last_seen"] == "2031-03-10"
    changed = {item for topic, item in published if topic == TOPIC_CONSOLE_CHANGED}
    assert {m.SUMMARY_KIND, m.SERIES_KIND, scheduler.STATUS_KIND} <= changed


async def test_rerunning_a_slot_changes_nothing(
    db_session, azure_on, published
) -> None:
    await poll(db_session, AzureMock())
    first = await stored(db_session)
    again = AzureMock()
    await poll(db_session, again, POLL_NOW + timedelta(minutes=15))
    # Same UTC day: no second backfill, only the four PT1M calls.
    assert {c["interval"] for c in again.calls} == {"PT1M"}
    assert len(again.calls) == 4
    assert await stored(db_session) == first
    row = await status_row(db_session)
    assert sync_status.calls_this_month(row, datetime.now(UTC).date()) == 12


async def test_retention_prunes_old_minute_and_hour_points(db_session) -> None:
    now = datetime(2031, 6, 1, tzinfo=UTC)

    def point(grain: str, age: timedelta) -> MetricPoint:
        return MetricPoint(LLM, m.PROMPT_TOKENS, "", grain, now - age, 1.0, None, None)

    await m.upsert_points(
        db_session,
        [
            point("PT1M", timedelta(days=8)),
            point("PT1M", timedelta(days=6)),
            point("PT1H", timedelta(days=94)),
            point("PT1H", timedelta(days=10)),
        ],
        now,
    )
    assert await m.prune_points(db_session, now) == 2
    left = (
        await db_session.execute(
            select(AzureMetricPoint.grain, func.count())
            .where(AzureMetricPoint.ts >= now - timedelta(days=100))
            .group_by(AzureMetricPoint.grain)
            .order_by(AzureMetricPoint.grain)
        )
    ).all()
    assert [tuple(r) for r in left] == [("PT1H", 1), ("PT1M", 1)]


async def test_monthly_cap_stops_the_poll_and_keeps_fetched_points(
    db_session, azure_on, published, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "azure_metrics_monthly_call_cap", 2)
    mock = AzureMock()
    assert await poll(db_session, mock) is None
    assert len(mock.calls) == 2  # the third call never left the process
    row = await status_row(db_session)
    assert row.state == "budget_exhausted"
    assert {r[1] for r in await stored(db_session)} == {
        m.PROMPT_TOKENS,
        m.GENERATED_TOKENS,
        m.TOKEN_TRANSACTION,
        m.REQUESTS,
    }
    assert (TOPIC_CONSOLE_CHANGED, scheduler.STATUS_KIND) in published


async def test_throttled_poll_defers_and_keeps_fetched_points(
    db_session, azure_on, published
) -> None:
    throttled = httpx.Response(
        429, json=fixture("arm_throttled.json"), headers={"Retry-After": "120"}
    )
    mock = AzureMock({(REQUESTS, "PT1M"): throttled})
    with pytest.raises(JobDeferredError):
        await poll(db_session, mock)
    assert (await status_row(db_session)).state == "throttled"
    assert len(await stored(db_session)) == 18


async def test_poll_refreshes_stored_snapshots_and_announces_live(
    db_session, azure_on, published, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "dashboard_snapshots_persist", True)
    runner = shared_session_runner(db_session)
    await poll(db_session, AzureMock())
    for _ in range(5):
        await asyncio.sleep(0)
    summary = await snapshots.get_snapshot(
        m.SUMMARY_KIND, rec.summary_params(rec.DEFAULT_DAYS), runner.gather
    )
    assert summary["connected"] is True
    assert summary["state"] == "ok"
    assert summary["as_of"] is not None
    assert summary["last_point_at"].startswith("2031-03-10T10:02")
    for kind in (m.SUMMARY_KIND, m.SERIES_KIND):
        assert any(
            topic == TOPIC_SNAPSHOT_UPDATED and item.startswith(f"{kind}|")
            for topic, item in published
        ), kind


# ---------------------------------------------------------------------------
# Reconciliation against seeded AskDIQ records (Postgres, CI)
# ---------------------------------------------------------------------------


def naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None)


async def seed_askdiq(db: AsyncSession) -> None:
    await db.merge(ModelSettings(id=1, llm_deployment=LLM, embedding_model=EMBED))
    at = naive(datetime(2031, 3, 10, 10, 1, tzinfo=UTC))
    session = ChatSession(
        user_email="azure-recon@example.com",
        created_at=at,
        last_activity_at=at,
        expires_at=at + timedelta(hours=1),
    )
    db.add(session)
    await db.flush()
    turns: list[tuple[int, int, dict | None, int | None, int | None]] = [
        (1000, 300, {"llm_attempts": 1}, 50, 30),
        (900, 350, {"llm_attempts": 2}, None, None),
        (500, 150, None, None, None),
    ]
    for i, (prompt, completion, details, emb, guard) in enumerate(turns):
        db.add(
            ChatMessageRecord(
                session_id=session.id,
                turn_index=i,
                role="assistant",
                content="answer",
                created_at=at,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
                embedding_tokens=emb,
                guardrail_tokens=guard,
                token_details=details,
            )
        )
    for detail in (
        {"status_code": 429, "error_type": "RateLimitError"},
        {"status_code": 429, "error_type": "RateLimitError"},
        {"status_code": None, "error_type": "CircuitOpenError"},
    ):
        db.add(
            AuditLog(
                request_id=uuid.uuid4().hex,
                actor="system",
                action="llm_call_failed",
                detail=detail,
                created_at=at,
            )
        )
    for llm_ms in (1000.0, 1400.0):
        db.add(
            LatencyRecord(
                request_id=uuid.uuid4().hex,
                created_at=at,
                total_ms=llm_ms + 100,
                llm_ms=llm_ms,
                embedding_ms=40.0,
            )
        )
    db.add(
        InsightLlmUsage(
            created_at=datetime(2031, 3, 10, 10, 1, tzinfo=UTC),
            model=LLM,
            turns=3,
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cost_usd=0.0,
        )
    )
    await db.flush()


def find(rows: list, deployment: str, day: str | None = None) -> Any:
    return next(r for r in rows if r.deployment == deployment and r.day == day)


async def test_reconciliation_per_day_and_last_24h(
    db_session, azure_on, published
) -> None:
    await poll(db_session, AzureMock())
    await seed_askdiq(db_session)
    summary = await rec.build_summary(db_session, get_settings(), 7, now=RECON_NOW)
    assert (summary.llm_deployment, summary.embedding_deployment) == (LLM, EMBED)
    assert summary.drift_threshold_pct == get_settings().azure_reconciliation_drift_pct

    llm = find(summary.daily, LLM, "2031-03-10")
    assert llm.role == "llm"
    # The 10:00 PT1H point was fetched before its hour closed: minutes win.
    assert (llm.requests.azure, llm.requests.askdiq) == (
        12,
        7,
    )  # 4+2 attempts+1 insight
    assert llm.requests.drift_pct == 41.67
    assert (llm.tokens_in.azure, llm.tokens_in.askdiq) == (3000, 2500)
    assert (llm.tokens_out.azure, llm.tokens_out.askdiq) == (1000, 850)
    assert (llm.throttled.azure, llm.throttled.askdiq) == (
        3,
        2,
    )  # circuit-open excluded
    assert (llm.latency_avg_ms.azure, llm.latency_avg_ms.askdiq) == (1200, 1200)
    assert (llm.latency_max_ms.azure, llm.latency_max_ms.askdiq) == (2500, 1400)
    assert not llm.latency_avg_ms.drifted
    assert llm.ttft_avg_ms == 400 and llm.availability_pct == 100
    assert llm.not_seen_tokens == 4000 - 3350
    assert llm.drifted

    # A closed hour: the complete PT1H point is used; AskDIQ recorded nothing.
    older = find(summary.daily, LLM, "2031-03-09")
    assert (older.requests.azure, older.throttled.azure) == (21, 1)
    assert (older.tokens_in.azure, older.tokens_in.askdiq) == (5000, 0)
    assert older.latency_avg_ms.azure == 1000
    assert older.estimated_cost_usd == 0.42

    embed = find(summary.daily, EMBED, "2031-03-10")
    assert embed.role == "embedding"
    assert (embed.tokens_in.azure, embed.tokens_in.askdiq) == (300, 80)
    assert (embed.requests.azure, embed.requests.askdiq) == (3, 2)
    assert embed.latency_avg_ms.askdiq == 40

    other = find(summary.daily, OTHER, "2031-03-10")
    assert other.role == "other" and not other.drifted
    assert other.not_seen_tokens == 500

    recent = find(summary.last_24h, LLM)
    assert recent.requests.azure == 12  # 2031-03-09 09:00 is outside the 24 h
    assert [r.day for r in summary.daily][:1] == ["2031-03-10"]

    tokens = {t.deployment: t for t in summary.tokens}
    assert tokens[LLM].azure_tokens == 3000 + 1000 + 5000 + 2000
    assert tokens[LLM].askdiq_tokens == 3350
    assert tokens[OTHER].not_seen_pct == 100
    assert summary.preview_cost.enabled is True


async def test_series_merges_hours_and_serves_minutes(
    db_session, azure_on, published
) -> None:
    await poll(db_session, AzureMock())
    settings = get_settings()
    hourly = await rec.build_series(
        db_session, settings, metric=m.PROMPT_TOKENS, range_="7d", now=RECON_NOW
    )
    llm = next(s for s in hourly.series if s.deployment == LLM)
    assert [(p.t.isoformat(), p.total) for p in llm.points] == [
        ("2031-03-09T09:00:00+00:00", 5000),
        ("2031-03-10T10:00:00+00:00", 3000),
    ]
    minutes = await rec.build_series(
        db_session,
        settings,
        metric=m.REQUESTS,
        deployment=LLM,
        range_="1h",
        grain="PT1M",
        now=RECON_NOW,
    )
    assert {(s.deployment, s.status_code) for s in minutes.series} == {
        (LLM, "200"),
        (LLM, "429"),
    }
    assert [p.total for p in minutes.series[1].points] == [0, 2, 1]


# ---------------------------------------------------------------------------
# API (Postgres, CI)
# ---------------------------------------------------------------------------


async def test_summary_endpoint_not_connected_shape(client) -> None:
    response = await client.get("/admin/azure/metrics/summary")
    assert response.status_code == 200
    body = response.json()
    assert (body["connected"], body["state"], body["days"]) == (
        False,
        "not_configured",
        7,
    )
    assert body["llm_deployment"]
    assert isinstance(body["daily"], list) and isinstance(body["tokens"], list)
    etag = response.headers["etag"]
    again = await client.get(
        "/admin/azure/metrics/summary", headers={"If-None-Match": etag}
    )
    assert again.status_code == 304
    assert (await client.get("/admin/azure/metrics/summary?days=0")).status_code == 422


async def test_series_endpoint_validation(client) -> None:
    ok = await client.get("/admin/azure/metrics/series")
    assert ok.status_code == 200
    body = ok.json()
    assert (body["metric"], body["range"], body["grain"], body["deployment"]) == (
        "TokenTransaction",
        "24h",
        "PT1H",
        "*",
    )
    bad: list[str] = [
        "?grain=PT1M&range=24h",
        "?metric=TotalCalls",
        "?range=2y",
        "?deployment=a%20b",
    ]
    for query in bad:
        response = await client.get(f"/admin/azure/metrics/series{query}")
        assert response.status_code == 422, query
    minute = await client.get("/admin/azure/metrics/series?grain=PT1M&range=6h")
    assert minute.status_code == 200


# ---------------------------------------------------------------------------
# Alert rule azure_monitor_429_spike (Postgres, CI)
# ---------------------------------------------------------------------------


def throttled_points(
    latest: datetime, per_minute: Callable[[int], float]
) -> list[MetricPoint]:
    return [
        MetricPoint(
            LLM,
            m.REQUESTS,
            "429",
            "PT1M",
            latest - timedelta(minutes=i),
            per_minute(i),
            None,
            None,
        )
        for i in range(20)
    ]


async def test_azure_monitor_429_rule(db_session, azure_on) -> None:
    cfg = get_settings()
    now = datetime(2032, 1, 5, 12, 0, tzinfo=UTC)
    latest = now - timedelta(minutes=5)
    assert await rules.azure_monitor_429_rule(db_session, now, cfg) == []
    # One 429 a minute: 15 in the newest 15-minute window (> threshold 10).
    await m.upsert_points(db_session, throttled_points(latest, lambda _i: 1.0), now)
    [found] = await rules.azure_monitor_429_rule(db_session, now, cfg)
    assert found.rule == c.AZURE_MONITOR_429_SPIKE
    assert found.severity is Severity.WARNING
    assert found.details["value"] == cfg.alert_azure_monitor_429_window_minutes
    assert found.details["counts"] == {LLM: 15}
    await m.upsert_points(db_session, throttled_points(latest, lambda _i: 5.0), now)
    [critical] = await rules.azure_monitor_429_rule(db_session, now, cfg)
    assert critical.severity is Severity.CRITICAL
    # Stale points (the poll stopped) never alert.
    assert (
        await rules.azure_monitor_429_rule(db_session, now + timedelta(hours=2), cfg)
        == []
    )


async def test_azure_monitor_429_rule_quiet_when_disconnected(db_session) -> None:
    now = datetime(2032, 1, 5, 12, 0, tzinfo=UTC)
    await m.upsert_points(db_session, throttled_points(now, lambda _i: 50.0), now)
    assert await rules.azure_monitor_429_rule(db_session, now, get_settings()) == []


def test_rule_is_catalogued_and_evaluated() -> None:
    rule = c.rule_for(c.AZURE_MONITOR_429_SPIKE)
    assert rule is not None and rule.source == "database"
    assert "ALERT_AZURE_MONITOR_429_THRESHOLD" in rule.severity
    assert (
        rules.DATABASE_RULE_FUNCS[c.AZURE_MONITOR_429_SPIKE]
        is rules.azure_monitor_429_rule
    )
    assert get_settings().alert_azure_monitor_429_threshold == 10
