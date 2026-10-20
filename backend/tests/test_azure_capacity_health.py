"""Azure Insights Phase 4: capacity / quota, Resource + Service Health, Advisor.

Recorded-shape responses (tests/fixtures/azure, placeholder ids) go through
``httpx.MockTransport``; nothing calls Azure. Parsing, banner and gauge
tests need no database; the poll / snapshot / alert tests run on the CI DB.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.alerts import catalogue as c
from app.alerts.azure_rules import (
    azure_quota_rule,
    azure_sync_failing_rule,
    azure_unhealthy_rule,
)
from app.alerts.catalogue import Severity
from app.alerts.rules import DATABASE_RULE_FUNCS
from app.azure_insights import advisor, capacity, health, metrics, scheduler
from app.azure_insights.capacity_schemas import ResourceHealth, ServiceEvent
from app.core.cache import dashboard_cache
from app.core.config import get_settings
from app.core.events import TOPIC_CONSOLE_CHANGED
from app.core.quota import (
    LIMIT_SOURCE_AZURE,
    LIMIT_SOURCE_SETTINGS,
    ExternalLimit,
    QuotaMonitor,
    quota_monitor,
)
from app.dashboard import snapshots
from app.models.db_models import (
    AzureAdvisorRec,
    AzureDeploymentSnapshot,
    AzureHealthStatus,
    AzureMetricPoint,
    AzureQuotaUsage,
    AzureServiceEvent,
    AzureSyncStatus,
    ChatMessageRecord,
    ChatSession,
)
from tests.conftest import shared_session_factory, shared_session_runner

FIXTURES = Path(__file__).parent / "fixtures" / "azure"
REGION = "australiaeast"
_AZURE_IDS = {
    "azure_tenant_id": "11111111-1111-4111-8111-111111111111",
    "azure_client_id": "22222222-2222-4222-8222-222222222222",
    "azure_client_secret": "fixture-secret-value-0123456789",
    "azure_subscription_id": "33333333-3333-4333-8333-333333333333",
    "azure_resource_group": "rg-askdiq",
    "azure_openai_resource_name": "askdiq-foundry",
    "azure_openai_region": REGION,
}


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Capacity parsing (no DB)
# ---------------------------------------------------------------------------


def test_rate_limits_are_normalised_by_renewal_period() -> None:
    assert capacity.per_minute(250, 60) == 250
    assert capacity.per_minute(120, 10) == 720
    assert capacity.per_minute(None, 60) is None
    assert capacity.per_minute(5, 0) is None
    limits = [
        {"key": "token", "renewalPeriod": 60, "count": 300000},
        {"key": "token", "renewalPeriod": 60, "count": 250000},
        {"key": "request", "renewalPeriod": 10, "count": 50},
        {"key": "other", "renewalPeriod": 60, "count": 1},
    ]
    # Several entries per key: the most restrictive wins.
    assert capacity.rate_limits_per_minute(limits) == (250000, 300)
    assert capacity.rate_limits_per_minute(None) == (None, None)


def test_deployments_fixture_gives_real_tpm_rpm() -> None:
    now = datetime(2026, 10, 20, tzinfo=UTC)
    rows = {
        r["name"]: r
        for r in (
            capacity.parse_deployment(i, now)
            for i in fixture("deployments.json")["value"]
        )
        if r
    }
    chat = rows["gpt-5-mini"]
    assert (chat["tpm_limit"], chat["rpm_limit"]) == (250000, 250)
    assert (chat["sku_name"], chat["sku_capacity"], chat["model_version"]) == (
        "GlobalStandard",
        250,
        "2025-08-07",
    )
    assert chat["provisioning_state"] == "Succeeded"
    embed = rows["embeddings"]
    assert (embed["tpm_limit"], embed["rpm_limit"]) == (120000, 720)
    legacy = rows["gpt-4o-legacy"]
    # No rateLimits reported: no limit is invented.
    assert (legacy["tpm_limit"], legacy["rpm_limit"], legacy["rate_limits"]) == (
        None,
        None,
        None,
    )


def test_usages_keep_quotas_in_use_or_of_deployed_models() -> None:
    now = datetime(2026, 10, 20, tzinfo=UTC)
    models = {"gpt-5-mini", "text-embedding-3-small", "gpt-4o"}
    rows = [
        r
        for i in fixture("usages.json")["value"]
        if (r := capacity.parse_usage(i, REGION, now))
        and capacity.keep_usage(r, models)
    ]
    assert [r["name"] for r in rows] == [
        "OpenAI.GlobalStandard.gpt-5-mini",
        "OpenAI.Standard.text-embedding-3-small",
        "OpenAI.Standard.gpt-4o",
        "AccountCount",
    ]
    assert rows[0]["current_value"] == 250 and rows[0]["limit_value"] == 1000
    assert capacity.usage_pct(250, 1000) == 25.0
    assert capacity.usage_pct(1, 0) is None


def test_limits_map_deployment_and_model_names() -> None:
    rows = [
        AzureDeploymentSnapshot(
            name="gpt-5-mini", model_name="gpt-5-mini", tpm_limit=250000, rpm_limit=250
        ),
        AzureDeploymentSnapshot(
            name="embeddings",
            model_name="text-embedding-3-small",
            tpm_limit=120000,
            rpm_limit=720,
        ),
        AzureDeploymentSnapshot(name="gpt-4o-legacy", model_name="gpt-4o"),
    ]
    limits = capacity.limits_by_name(rows)
    assert limits["gpt-5-mini"] == ExternalLimit(250000, 250)
    # The embedding tracker is keyed by EMBEDDING_MODEL (the model name).
    assert limits["text-embedding-3-small"] == ExternalLimit(120000, 720)
    assert "gpt-4o-legacy" not in limits and "gpt-4o" not in limits


# ---------------------------------------------------------------------------
# Quota gauges: real limits first, configured defaults otherwise
# ---------------------------------------------------------------------------


def test_gauges_use_azure_limits_then_fall_back_to_settings() -> None:
    cfg = get_settings()
    monitor = QuotaMonitor()
    tracker = monitor.get_tracker("gpt-5-mini")
    before = tracker.get_metrics(now=100.0)
    assert before["tpm_limit"] == cfg.azure_openai_tpm_limit
    assert before["tpm_limit_source"] == LIMIT_SOURCE_SETTINGS

    monitor.set_external_limits({"GPT-5-mini": ExternalLimit(250000, 250)})
    tracker.record(200000, now=100.0)
    metrics = tracker.get_metrics(now=100.5)
    assert (metrics["tpm_limit"], metrics["rpm_limit"]) == (250000, 250)
    assert (
        metrics["tpm_limit_source"] == metrics["rpm_limit_source"] == LIMIT_SOURCE_AZURE
    )
    assert metrics["tpm_headroom_pct"] == 20.0
    # A tracker created later picks the limits up too; RPM only = TPM default.
    monitor.set_external_limits(
        {
            "gpt-5-mini": ExternalLimit(250000, 250),
            "embeddings": ExternalLimit(None, 720),
        }
    )
    late = monitor.get_tracker("embeddings").get_metrics(now=1.0)
    assert (late["rpm_limit"], late["rpm_limit_source"]) == (720, LIMIT_SOURCE_AZURE)
    assert late["tpm_limit_source"] == LIMIT_SOURCE_SETTINGS

    monitor.set_external_limits({})
    reverted = tracker.get_metrics(now=101.0)
    assert reverted["tpm_limit"] == cfg.azure_openai_tpm_limit
    assert reverted["rpm_limit"] == cfg.azure_openai_rpm_limit
    assert reverted["tpm_limit_source"] == LIMIT_SOURCE_SETTINGS


# ---------------------------------------------------------------------------
# Health parsing and the banner (no DB)
# ---------------------------------------------------------------------------


def test_availability_reads_occured_time_and_strips_html() -> None:
    values = health.parse_availability(fixture("health_degraded.json"))
    assert values["availability_state"] == "Degraded"
    assert values["summary"] == (
        "Your Azure OpenAI resource is experiencing increased latency ."
    )
    assert values["occurred_at"] == datetime(2026, 10, 20, 10, 2, tzinfo=UTC)
    assert values["resolution_eta"] == datetime(2026, 10, 20, 12, tzinfo=UTC)
    assert health.parse_availability({})["availability_state"] == "Unknown"


def test_service_events_are_filtered_to_our_service_and_region() -> None:
    now = datetime(2026, 10, 20, 11, tzinfo=UTC)
    rows = {
        r["tracking_id"]: r
        for r in (
            health.parse_event(i, REGION, now)
            for i in fixture("service_events.json")["value"]
        )
        if r
    }
    # Other region (East US 2) and other service (Virtual Machines) dropped.
    assert set(rows) == {"FIX1-AE1", "FIX4-GL4", "FIX5-AE5"}
    assert rows["FIX1-AE1"]["regions"] == ["Australia East"]
    assert rows["FIX1-AE1"]["summary"].startswith("Starting at 09:40 UTC")
    assert "<" not in rows["FIX1-AE1"]["summary"]
    assert rows["FIX4-GL4"]["regions"] == ["Global"]
    assert rows["FIX5-AE5"]["regions"] == ["Australia East"]
    assert rows["FIX5-AE5"]["impact_mitigation"] == datetime(
        2026, 10, 15, 2, tzinfo=UTC
    )
    assert health.query_start(now) == "09/20/2026"


def _resource(state: str, *, stale: bool = False) -> ResourceHealth:
    now = datetime(2026, 10, 20, tzinfo=UTC)
    return ResourceHealth(
        availability_state=state,
        summary=f"{state} summary",
        since=now,
        checked_at=now,
        stale=stale,
    )


def _incident(
    *, active: bool = True, etype: str = "ServiceIssue", level: str = "Warning"
) -> ServiceEvent:
    return ServiceEvent(
        tracking_id="FIX1-AE1",
        event_type=etype,
        status="Active" if active else "Resolved",
        level=level,
        title="Elevated errors",
        services=["Azure OpenAI Service"],
        regions=["Australia East"],
        last_seen_at=datetime(2026, 10, 20, tzinfo=UTC),
        active=active,
    )


@pytest.mark.parametrize(
    ("connected", "resource", "events", "expected"),
    [
        (True, _resource("Available"), [], (False, None)),
        (True, _resource("Unknown"), [], (False, None)),
        (True, _resource("Degraded"), [], (True, "warning")),
        (True, _resource("Unavailable"), [], (True, "critical")),
        # Not confirmed by recent polls: never a stuck banner.
        (True, _resource("Unavailable", stale=True), [], (False, None)),
        (True, None, [_incident()], (True, "warning")),
        (True, None, [_incident(level="Error")], (True, "critical")),
        (True, None, [_incident(active=False)], (False, None)),
        (True, None, [_incident(etype="HealthAdvisory")], (False, None)),
        (False, _resource("Unavailable"), [_incident()], (False, None)),
    ],
)
def test_banner_state(connected, resource, events, expected) -> None:
    banner = health.banner_state(connected, resource, events)
    assert (banner.show, banner.level) == expected
    if banner.show:
        assert banner.href.startswith("/admin/governance")
        assert banner.title


def test_banner_prefers_critical_and_names_the_incident() -> None:
    banner = health.banner_state(
        True, _resource("Degraded"), [_incident(level="Error")]
    )
    assert banner.level == "critical"
    assert banner.tracking_id == "FIX1-AE1"
    assert "Australia East" in (banner.message or "")


# ---------------------------------------------------------------------------
# Advisor parsing (no DB)
# ---------------------------------------------------------------------------


def test_advisor_flags_model_retirements() -> None:
    now = datetime(2026, 10, 20, tzinfo=UTC)
    rows = [
        advisor.parse_recommendation(i, now) for i in fixture("advisor.json")["value"]
    ]
    retire, cost = rows
    assert retire is not None and cost is not None
    assert advisor.is_retirement(
        retire["problem"], retire["solution"], retire["extended"]
    )
    assert advisor.retirement_date(retire["extended"]) == "2027-03-31"
    assert not advisor.is_retirement(cost["problem"], cost["solution"], {})
    assert cost["extended"] is None


def test_endpoints_and_rules_are_registered() -> None:
    from main import app  # noqa: PLC0415

    paths = app.openapi()["paths"]
    for path in (
        "/admin/azure/capacity",
        "/admin/azure/health",
        "/admin/azure/advisor",
    ):
        op = paths[path]["get"]
        assert op["summary"] and {"401", "403"} <= set(op["responses"]), path
    for rule in (
        c.AZURE_QUOTA_NEAR_LIMIT,
        c.AZURE_RESOURCE_UNHEALTHY,
        c.AZURE_SYNC_FAILING,
    ):
        assert rule in DATABASE_RULE_FUNCS and c.rule_for(rule) is not None
    kinds = snapshots.registered_kinds()
    assert {capacity.CAPACITY_KIND, health.HEALTH_KIND, advisor.ADVISOR_KIND} <= set(
        kinds
    )
    names = {s.name for s in scheduler.registered_sources()}
    assert {"capacity", "health", "service_health", "advisor"} <= names


# ---------------------------------------------------------------------------
# Polls (Postgres, CI): persist with history -> snapshot -> announce
# ---------------------------------------------------------------------------


class _Token:
    async def token(self) -> str:
        return "fixture-token"

    def invalidate(self) -> None:
        return None


class Arm:
    """Mock ARM: path suffix -> (status, body)."""

    def __init__(self, routes: dict[str, tuple[int, Any]]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for suffix, (status, body) in self.routes.items():
            if request.url.path.endswith(suffix):
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={"error": {"code": "NotFound", "message": "x"}})


@pytest.fixture
def enabled(monkeypatch) -> Iterator[Any]:
    cfg = get_settings()
    for name, value in {**_AZURE_IDS, "azure_insights_enabled": True}.items():
        monkeypatch.setattr(
            cfg, name, SecretStr(value) if name == "azure_client_secret" else value
        )
    monkeypatch.setattr(cfg, "dashboard_snapshots_persist", True)
    monkeypatch.setattr(scheduler, "get_token_provider", lambda _s=None: _Token())
    yield cfg
    quota_monitor.set_external_limits({})
    dashboard_cache.invalidate_prefix(snapshots.CACHE_PREFIX)


@pytest.fixture
def published(monkeypatch) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []

    async def fake_publish(topic: str, item_id: str) -> None:
        seen.append((topic, str(item_id)))

    monkeypatch.setattr(snapshots, "publish_event", fake_publish)
    monkeypatch.setattr(scheduler, "publish_event", fake_publish)
    return seen


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def _poll(db_session, name: str, arm: Arm, now: datetime | None = None) -> Any:
    result = await scheduler.run_source(
        name,
        now=now,
        session_factory=shared_session_factory(db_session),
        gather=shared_session_runner(db_session).gather,
        transport=arm.transport,
        rearm=False,
    )
    await _settle()
    return result


async def _snapshot(db_session, kind: str) -> dict:
    return await snapshots.get_snapshot(
        kind, {}, shared_session_runner(db_session).gather
    )


def _changed(published: list[tuple[str, str]]) -> set[str]:
    return {item for topic, item in published if topic == TOPIC_CONSOLE_CHANGED}


async def test_capacity_poll_keeps_history_feeds_gauges_and_announces(
    db_session, enabled, published
) -> None:
    deployments = fixture("deployments.json")
    arm = Arm(
        {"/deployments": (200, deployments), "/usages": (200, fixture("usages.json"))}
    )
    t0 = datetime.now(UTC) - timedelta(hours=6)
    result = await _poll(db_session, "capacity", arm, now=t0)
    assert result is not None and result.detail["usages"] == "ok"
    paths = {r.url.path for r in arm.requests}
    assert any(p.endswith("/accounts/askdiq-foundry/deployments") for p in paths)
    assert any(p.endswith(f"/locations/{REGION}/usages") for p in paths)
    assert all(r.url.params["api-version"] == "2024-10-01" for r in arm.requests)
    assert all(
        r.headers["authorization"] == "Bearer fixture-token" for r in arm.requests
    )

    # Second poll: capacity raised in Azure.
    raised = copy.deepcopy(deployments)
    chat = raised["value"][0]
    chat["sku"]["capacity"] = 300
    chat["properties"]["rateLimits"] = [
        {"key": "request", "renewalPeriod": 60, "count": 300},
        {"key": "token", "renewalPeriod": 60, "count": 300000},
    ]
    arm.routes["/deployments"] = (200, raised)
    await _poll(db_session, "capacity", arm)

    history = await db_session.scalar(
        select(func.count())
        .select_from(AzureDeploymentSnapshot)
        .where(AzureDeploymentSnapshot.name == "gpt-5-mini")
    )
    assert history == 2
    payload = await _snapshot(db_session, capacity.CAPACITY_KIND)
    by_name = {d["name"]: d for d in payload["deployments"]}
    assert by_name["gpt-5-mini"]["tpm_limit"] == 300000
    assert by_name["gpt-5-mini"]["role"] == "chat"
    assert by_name["embeddings"]["role"] == "embedding"
    assert payload["regional"]["state"] == "ok"
    assert payload["regional"]["items"][0]["pct"] == 25.0
    change = next(ch for ch in payload["changes"] if ch["name"] == "gpt-5-mini")
    assert (change["prev_tpm_limit"], change["tpm_limit"]) == (250000, 300000)
    # Every process's gauges: this one at once (others on the announcement).
    assert quota_monitor.external_limit("gpt-5-mini") == ExternalLimit(300000, 300)
    assert {
        capacity.CAPACITY_KIND,
        capacity.MONITORING_SCOPE,
        scheduler.STATUS_KIND,
    } <= _changed(published)


async def test_regional_quota_forbidden_is_recorded_cleanly(
    db_session, enabled, published
) -> None:
    arm = Arm(
        {
            "/deployments": (200, fixture("deployments.json")),
            "/usages": (403, fixture("arm_forbidden.json")),
        }
    )
    result = await _poll(db_session, "capacity", arm)
    assert result is not None
    assert (result.detail["usages"], result.detail["usages_error"]) == (
        "forbidden",
        "AuthorizationFailed",
    )
    row = await db_session.get(AzureSyncStatus, "capacity")
    assert row.state == "ok" and row.consecutive_failures == 0
    payload = await _snapshot(db_session, capacity.CAPACITY_KIND)
    assert payload["regional"]["state"] == "forbidden"
    assert payload["regional"]["items"] == []
    assert len(payload["deployments"]) == 3


async def test_health_poll_keeps_status_changes_and_updates_the_banner(
    db_session, enabled, published
) -> None:
    arm = Arm(
        {"/availabilityStatuses/current": (200, fixture("health_available.json"))}
    )
    base = datetime.now(UTC) - timedelta(minutes=5)
    await _poll(db_session, "health", arm, now=base)
    await _poll(db_session, "health", arm, now=base + timedelta(minutes=1))
    assert (await _snapshot(db_session, health.HEALTH_KIND))["banner"]["show"] is False

    arm.routes["/availabilityStatuses/current"] = (200, fixture("health_degraded.json"))
    await _poll(db_session, "health", arm, now=base + timedelta(minutes=2))
    payload = await _snapshot(db_session, health.HEALTH_KIND)
    assert payload["banner"]["show"] is True and payload["banner"]["level"] == "warning"
    assert payload["resource"]["availability_state"] == "Degraded"
    assert health.HEALTH_KIND in _changed(published)

    arm.routes["/availabilityStatuses/current"] = (
        200,
        fixture("health_available.json"),
    )
    await _poll(db_session, "health", arm, now=base + timedelta(minutes=3))
    payload = await _snapshot(db_session, health.HEALTH_KIND)
    assert payload["banner"]["show"] is False
    states = [h["availability_state"] for h in payload["history"]]
    assert states == ["Available", "Degraded", "Available"]
    rows = (
        (
            await db_session.execute(
                select(AzureHealthStatus).order_by(AzureHealthStatus.started_at)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 3
    # The first status was confirmed by the second poll, then ended.
    assert rows[0].checked_at == base + timedelta(minutes=1)
    assert rows[0].ended_at == base + timedelta(minutes=2)
    assert rows[2].ended_at is None


async def test_service_health_upserts_by_tracking_id(
    db_session, enabled, published, monkeypatch
) -> None:
    monkeypatch.setattr(enabled, "azure_service_health_enabled", True)
    events = fixture("service_events.json")
    arm = Arm({"/Microsoft.ResourceHealth/events": (200, events)})
    await _poll(db_session, "service_health", arm)
    await _poll(db_session, "service_health", arm)
    assert "queryStartTime" in arm.requests[0].url.params
    count = await db_session.scalar(select(func.count()).select_from(AzureServiceEvent))
    assert count == 3
    payload = await _snapshot(db_session, health.HEALTH_KIND)
    assert payload["banner"]["show"] is True
    assert payload["banner"]["tracking_id"] == "FIX1-AE1"
    active = {e["tracking_id"] for e in payload["events"] if e["active"]}
    assert active == {"FIX1-AE1", "FIX4-GL4"}

    resolved = copy.deepcopy(events)
    resolved["value"][0]["properties"]["status"] = "Resolved"
    arm.routes["/Microsoft.ResourceHealth/events"] = (200, resolved)
    await _poll(db_session, "service_health", arm)
    payload = await _snapshot(db_session, health.HEALTH_KIND)
    assert payload["banner"]["show"] is False
    assert (
        await db_session.scalar(select(func.count()).select_from(AzureServiceEvent))
        == 3
    )


async def test_advisor_poll_resolves_recommendations_no_longer_returned(
    db_session, enabled, published, monkeypatch
) -> None:
    monkeypatch.setattr(enabled, "azure_advisor_enabled", True)
    recs = fixture("advisor.json")
    arm = Arm({"/Microsoft.Advisor/recommendations": (200, recs)})
    await _poll(db_session, "advisor", arm)
    assert arm.requests[0].url.params["$filter"] == "ResourceGroup eq 'rg-askdiq'"
    assert all("generateRecommendations" not in r.url.path for r in arm.requests)
    only_first = {"value": recs["value"][:1]}
    arm.routes["/Microsoft.Advisor/recommendations"] = (200, only_first)
    await _poll(db_session, "advisor", arm)
    gone = await db_session.get(AzureAdvisorRec, "aaaaaaaa-0000-4000-8000-000000000002")
    assert gone is not None and gone.resolved_at is not None
    payload = await _snapshot(db_session, advisor.ADVISOR_KIND)
    assert [r["rec_id"] for r in payload["recommendations"]] == [
        "aaaaaaaa-0000-4000-8000-000000000001"
    ]
    assert payload["recommendations"][0]["retirement"] is True
    assert payload["recommendations"][0]["retirement_date"] == "2027-03-31"
    assert advisor.ADVISOR_KIND in _changed(published)


async def test_endpoints_not_connected_shape(client) -> None:
    dashboard_cache.invalidate_prefix(snapshots.CACHE_PREFIX)
    for path in (
        "/admin/azure/capacity",
        "/admin/azure/health",
        "/admin/azure/advisor",
    ):
        response = await client.get(path)
        assert response.status_code == 200, path
        body = response.json()
        assert body["connected"] is False
        again = await client.get(
            path, headers={"If-None-Match": response.headers["etag"]}
        )
        assert again.status_code == 304
    body = (await client.get("/admin/azure/health")).json()
    assert body["banner"]["show"] is False
    capacity_body = (await client.get("/admin/azure/capacity")).json()
    assert capacity_body["regional"]["state"] == "never_run"


# ---------------------------------------------------------------------------
# Alert rules (Postgres, CI)
# ---------------------------------------------------------------------------


async def _seed_turns(db_session, tokens: list[int]) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    session = ChatSession(
        user_email="azure-alerts@example.com",
        created_at=now,
        last_activity_at=now,
        expires_at=now + timedelta(hours=1),
    )
    db_session.add(session)
    await db_session.flush()
    minute = now.replace(second=0, microsecond=0)
    for i, total in enumerate(tokens):
        db_session.add(
            ChatMessageRecord(
                session_id=session.id,
                turn_index=i + 1,
                role="assistant",
                content="answer",
                total_tokens=total,
                created_at=minute,
            )
        )
    await db_session.flush()


async def test_quota_rule_uses_real_limits_and_regional_quota(
    db_session, enabled
) -> None:
    now = datetime.now(UTC)
    assert await azure_quota_rule(db_session, now, enabled) == []
    db_session.add_all(
        [
            AzureDeploymentSnapshot(
                name=enabled.azure_deployment_name,
                fetched_at=now,
                tpm_limit=1000,
                rpm_limit=2,
            ),
            AzureQuotaUsage(
                location=REGION,
                name="OpenAI.GlobalStandard.gpt-5-mini",
                fetched_at=now,
                current_value=900,
                limit_value=1000,
                unit="Count",
            ),
        ]
    )
    await _seed_turns(db_session, [500, 400])
    findings = {
        f.dedupe_key: f for f in await azure_quota_rule(db_session, now, enabled)
    }
    chat = enabled.azure_deployment_name
    tpm = findings[f"{c.AZURE_QUOTA_NEAR_LIMIT}:{chat}:tpm"]
    assert tpm.severity is Severity.WARNING and tpm.details["value"] == 90.0
    assert (
        findings[f"{c.AZURE_QUOTA_NEAR_LIMIT}:{chat}:rpm"].severity is Severity.CRITICAL
    )
    regional = findings[
        f"{c.AZURE_QUOTA_NEAR_LIMIT}:region:OpenAI.GlobalStandard.gpt-5-mini"
    ]
    assert regional.details["value"] == 90.0


def _metric_point(
    deployment: str, metric: str, ts: datetime, total: float, status_code: str = ""
) -> AzureMetricPoint:
    return AzureMetricPoint(
        deployment=deployment,
        metric=metric,
        status_code=status_code,
        grain=metrics.GRAIN_MINUTE,
        ts=ts,
        total=total,
    )


async def test_quota_rule_prefers_fresh_azure_minute_peaks(db_session, enabled) -> None:
    now = datetime.now(UTC)
    chat = enabled.azure_deployment_name
    db_session.add(
        AzureDeploymentSnapshot(name=chat, fetched_at=now, tpm_limit=1000, rpm_limit=10)
    )
    await _seed_turns(db_session, [100])  # app-recorded: 100 tokens, 1 request
    stale = now.replace(second=0, microsecond=0) - timedelta(hours=2)
    db_session.add_all(
        [
            _metric_point(chat, metrics.TOKEN_TRANSACTION, stale, 990),
            _metric_point(chat, metrics.REQUESTS, stale, 10, "200"),
        ]
    )
    await db_session.flush()
    # Azure points outside the window are stale: fall back to chat_messages.
    assert await azure_quota_rule(db_session, now, enabled) == []

    minute = now.replace(second=0, microsecond=0) - timedelta(minutes=3)
    db_session.add_all(
        [
            _metric_point(chat.upper(), metrics.TOKEN_TRANSACTION, minute, 950),
            _metric_point(chat, metrics.REQUESTS, minute, 6, "200"),
            _metric_point(chat, metrics.REQUESTS, minute, 3, "429"),
            _metric_point("other-deployment", metrics.TOKEN_TRANSACTION, minute, 5000),
        ]
    )
    await db_session.flush()
    findings = {
        f.dedupe_key: f for f in await azure_quota_rule(db_session, now, enabled)
    }
    tpm = findings[f"{c.AZURE_QUOTA_NEAR_LIMIT}:{chat}:tpm"]
    assert tpm.details["value"] == 95.0
    assert tpm.details["peak_source"] == "azure_monitor"
    rpm = findings[f"{c.AZURE_QUOTA_NEAR_LIMIT}:{chat}:rpm"]
    # Requests are summed over status codes per minute (6 OK + 3 throttled).
    assert rpm.details["value"] == 90.0
    assert rpm.details["counts"]["peak_requests_per_minute"] == 9


async def test_unhealthy_rule_raises_for_status_and_active_incident(
    db_session, enabled, monkeypatch
) -> None:
    now = datetime.now(UTC)
    assert await azure_unhealthy_rule(db_session, now, enabled) == []
    monkeypatch.setattr(enabled, "azure_service_health_enabled", True)
    db_session.add_all(
        [
            AzureHealthStatus(
                resource_id=enabled.azure_account_resource_id.lower(),
                started_at=now,
                checked_at=now,
                availability_state="Unavailable",
                summary="down",
            ),
            AzureServiceEvent(
                tracking_id="FIX1-AE1",
                event_type="ServiceIssue",
                status="Active",
                level="Warning",
                title="Elevated errors",
                services=["Azure OpenAI Service"],
                regions=["Australia East"],
                first_seen_at=now,
                last_seen_at=now,
            ),
        ]
    )
    await db_session.flush()
    findings = {
        f.dedupe_key: f for f in await azure_unhealthy_rule(db_session, now, enabled)
    }
    assert (
        findings[f"{c.AZURE_RESOURCE_UNHEALTHY}:resource"].severity is Severity.CRITICAL
    )
    assert (
        findings[f"{c.AZURE_RESOURCE_UNHEALTHY}:event:FIX1-AE1"].severity
        is Severity.WARNING
    )


async def test_sync_failing_rule_counts_consecutive_failures(
    db_session, enabled, monkeypatch
) -> None:
    now = datetime.now(UTC)
    db_session.add(
        AzureSyncStatus(
            source="capacity",
            state="error",
            consecutive_failures=2,
            last_error_code="AuthorizationFailed",
        )
    )
    await db_session.flush()
    assert await azure_sync_failing_rule(db_session, now, enabled) == []
    row = await db_session.get(AzureSyncStatus, "capacity")
    row.consecutive_failures = 3
    await db_session.flush()
    (finding,) = await azure_sync_failing_rule(db_session, now, enabled)
    assert finding.dedupe_key == f"{c.AZURE_SYNC_FAILING}:capacity"
    assert finding.severity is Severity.WARNING
    assert "AuthorizationFailed" in finding.summary
    monkeypatch.setattr(enabled, "azure_insights_enabled", False)
    assert await azure_sync_failing_rule(db_session, now, enabled) == []
