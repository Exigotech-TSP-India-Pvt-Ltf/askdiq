"""Azure Insights alert rules (database rules, ``alerts.evaluate``).

They read only what the Azure Insights polls stored (never call Azure) and
return nothing while AZURE_INSIGHTS_ENABLED is off:

- ``azure_quota_near_limit``: peak 1-minute tokens / requests of the chat
  deployment over ALERT_AZURE_QUOTA_WINDOW_MINUTES at or above
  ALERT_AZURE_QUOTA_PCT of the Azure-reported TPM / RPM, or regional quota
  ``currentValue / limit`` at or above it. The peak prefers Azure Monitor's
  PT1M ``TokenTransaction`` / ``AzureOpenAIRequests`` points
  (``azure_metric_points``, every caller of the deployment) when they cover
  the window, and falls back to app-recorded assistant turns
  (``chat_messages``) otherwise;
- ``azure_resource_unhealthy``: Resource Health Degraded / Unavailable, or an
  active ServiceIssue for our service and region;
- ``azure_insights_sync_failing``: a scheduled source failed at least
  ALERT_AZURE_SYNC_FAILURES consecutive runs (``azure_sync_status``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import catalogue as c
from app.alerts.catalogue import Severity
from app.alerts.service import Finding
from app.azure_insights import status as sync_status
from app.azure_insights.capacity import latest_deployments, usage_pct
from app.azure_insights.health import (
    DEGRADED,
    SERVICE_ISSUE,
    UNAVAILABLE,
    read_health,
)
from app.azure_insights.metrics import GRAIN_MINUTE, REQUESTS, TOKEN_TRANSACTION
from app.azure_insights.scheduler import is_scheduled, registered_sources
from app.core.config import Settings
from app.model_settings.service import current_model_settings
from app.models.db_models import AzureMetricPoint, AzureQuotaUsage, ChatMessageRecord

COMPONENT = "Azure OpenAI"
FAILING_STATES = frozenset(
    {sync_status.STATE_ERROR, sync_status.STATE_BUDGET_EXHAUSTED}
)
FULL_PCT = 100.0


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


async def peak_minute(db: AsyncSession, since: datetime) -> tuple[int, int]:
    """(peak tokens, peak requests) in one minute since ``since`` (naive UTC)."""
    minute = func.date_trunc("minute", ChatMessageRecord.created_at)
    per_minute = (
        select(
            func.coalesce(func.sum(ChatMessageRecord.total_tokens), 0).label("tokens"),
            func.count().label("requests"),
        )
        .where(
            ChatMessageRecord.role == "assistant",
            ChatMessageRecord.created_at >= since,
        )
        .group_by(minute)
        .subquery()
    )
    row = (
        await db.execute(
            select(
                func.coalesce(func.max(per_minute.c.tokens), 0),
                func.coalesce(func.max(per_minute.c.requests), 0),
            )
        )
    ).one()
    return int(row[0]), int(row[1])


async def azure_peak_minute(
    db: AsyncSession, deployment: str, since: datetime
) -> tuple[int | None, int | None]:
    """(peak tokens, peak requests) per minute since ``since`` (aware UTC) from
    Azure Monitor PT1M points of ``deployment``; None for a metric with no
    point in the window (not polled yet, metrics off or stale)."""
    pts = AzureMetricPoint
    per_minute = (
        select(
            pts.metric.label("metric"),
            func.sum(pts.total).label("total"),
        )
        .where(
            pts.grain == GRAIN_MINUTE,
            pts.metric.in_((TOKEN_TRANSACTION, REQUESTS)),
            func.lower(pts.deployment) == deployment.lower(),
            pts.ts >= since,
            pts.total.is_not(None),
        )
        .group_by(pts.metric, pts.ts)  # requests: sum over status codes
        .subquery()
    )
    rows = await db.execute(
        select(per_minute.c.metric, func.max(per_minute.c.total)).group_by(
            per_minute.c.metric
        )
    )
    peaks = {metric: int(peak) for metric, peak in rows if peak is not None}
    return peaks.get(TOKEN_TRANSACTION), peaks.get(REQUESTS)


async def window_peaks(
    db: AsyncSession, deployment: str, since: datetime
) -> tuple[int, int, dict[str, str]]:
    """(peak tokens, peak requests, source per metric) since ``since``.

    Azure counts every caller of the deployment, so a minute Azure reported
    is never below what AskDIQ recorded for it; taking the larger peak
    therefore prefers Azure wherever its points cover the window and lets
    the app-recorded turns cover only minutes Azure has not ingested yet."""
    app_tokens, app_requests = await peak_minute(db, _naive(since))
    az_tokens, az_requests = await azure_peak_minute(db, deployment, since)
    sources: dict[str, str] = {}
    peaks: list[int] = []
    for metric, app, azure in (
        ("tokens", app_tokens, az_tokens),
        ("requests", app_requests, az_requests),
    ):
        use_azure = azure is not None and azure >= app
        sources[metric] = "azure_monitor" if use_azure else "chat_messages"
        peaks.append(azure if use_azure and azure is not None else app)
    return peaks[0], peaks[1], sources


def _quota_severity(pct: float) -> Severity:
    return Severity.CRITICAL if pct >= FULL_PCT else Severity.WARNING


async def _deployment_findings(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    name = (current_model_settings().llm_deployment or "").lower()
    chat = next(
        (r for r in await latest_deployments(db) if r.name.lower() == name), None
    )
    if chat is None or not (chat.tpm_limit or chat.rpm_limit):
        return []
    window = cfg.alert_azure_quota_window_minutes
    tokens, requests, sources = await window_peaks(
        db, chat.name, now - timedelta(minutes=window)
    )
    findings = []
    for metric, unit, peak, limit in (
        ("tokens", "TPM", tokens, chat.tpm_limit),
        ("requests", "RPM", requests, chat.rpm_limit),
    ):
        if not limit:
            continue
        pct = peak * 100.0 / limit
        if pct < cfg.alert_azure_quota_pct:
            continue
        findings.append(
            Finding(
                rule=c.AZURE_QUOTA_NEAR_LIMIT,
                dedupe_key=f"{c.AZURE_QUOTA_NEAR_LIMIT}:{chat.name}:{unit.lower()}",
                severity=_quota_severity(pct),
                title=f"{chat.name}: peak {pct:.0f}% of the Azure {unit} limit",
                summary=(
                    f"Peak 1-minute {metric} on {chat.name} reached {peak:,} in the "
                    f"last {window} min, {pct:.0f}% of the {limit:,} {unit} limit "
                    f"reported by Azure (alert at {cfg.alert_azure_quota_pct:.0f}%)."
                ),
                component=COMPONENT,
                details={
                    "value": round(pct, 1),
                    "threshold": cfg.alert_azure_quota_pct,
                    "unit": "%",
                    "window_minutes": window,
                    "counts": {f"peak_{metric}_per_minute": peak, "limit": limit},
                    "peak_source": sources[metric],
                },
            )
        )
    return findings


async def _regional_findings(db: AsyncSession, cfg: Settings) -> list[Finding]:
    region = cfg.azure_openai_region or ""
    last = (
        select(func.max(AzureQuotaUsage.fetched_at))
        .where(AzureQuotaUsage.location == region)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(AzureQuotaUsage).where(
                AzureQuotaUsage.location == region, AzureQuotaUsage.fetched_at == last
            )
        )
    ).scalars()
    findings = []
    for row in rows:
        pct = usage_pct(row.current_value, row.limit_value)
        if pct is None or pct < cfg.alert_azure_quota_pct:
            continue
        label = row.localized_name or row.name
        findings.append(
            Finding(
                rule=c.AZURE_QUOTA_NEAR_LIMIT,
                dedupe_key=f"{c.AZURE_QUOTA_NEAR_LIMIT}:region:{row.name}",
                severity=_quota_severity(pct),
                title=f"Regional quota {label}: {pct:.0f}% allocated",
                summary=(
                    f"{label} in {region} has {row.current_value:,.0f} of "
                    f"{row.limit_value:,.0f} {row.unit or ''} allocated ({pct:.0f}%, "
                    f"alert at {cfg.alert_azure_quota_pct:.0f}%): little room to "
                    "raise a deployment's capacity."
                ),
                component=COMPONENT,
                details={
                    "value": pct,
                    "threshold": cfg.alert_azure_quota_pct,
                    "unit": "%",
                    "counts": {
                        "current": row.current_value,
                        "limit": row.limit_value,
                    },
                },
            )
        )
    return findings


async def azure_quota_rule(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    if not cfg.azure_insights_configured:
        return []
    return [
        *await _deployment_findings(db, now, cfg),
        *await _regional_findings(db, cfg),
    ]


async def azure_unhealthy_rule(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    if not cfg.azure_insights_configured:
        return []
    resource, _history, events = await read_health(db, cfg, now)
    findings = []
    if (
        resource is not None
        and not resource.stale
        and resource.availability_state in {DEGRADED, UNAVAILABLE}
    ):
        state = resource.availability_state
        findings.append(
            Finding(
                rule=c.AZURE_RESOURCE_UNHEALTHY,
                dedupe_key=f"{c.AZURE_RESOURCE_UNHEALTHY}:resource",
                severity=Severity.CRITICAL
                if state == UNAVAILABLE
                else Severity.WARNING,
                title=f"Azure OpenAI resource {state.lower()}",
                summary=resource.summary
                or "Azure Resource Health reports a problem with the resource.",
                component=COMPONENT,
                details={
                    "value": state,
                    "since": resource.since.isoformat(),
                    "counts": {"reason": resource.reason_type or "unknown"},
                },
            )
        )
    for event in events:
        if not (event.active and event.event_type == SERVICE_ISSUE):
            continue
        findings.append(
            Finding(
                rule=c.AZURE_RESOURCE_UNHEALTHY,
                dedupe_key=f"{c.AZURE_RESOURCE_UNHEALTHY}:event:{event.tracking_id}",
                severity=(
                    Severity.CRITICAL if event.level == "Error" else Severity.WARNING
                ),
                title=f"Azure service incident: {event.title or event.tracking_id}",
                summary=(
                    f"Active Service Health incident {event.tracking_id} for "
                    f"{', '.join(event.services)} in {', '.join(event.regions)}."
                ),
                component=COMPONENT,
                details={
                    "value": event.status,
                    "counts": {"tracking_id": event.tracking_id},
                },
            )
        )
    return findings


async def azure_sync_failing_rule(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    del now
    if not cfg.azure_insights_configured:
        return []
    rows = await sync_status.read_all(db)
    threshold = cfg.alert_azure_sync_failures
    findings = []
    for source in registered_sources():
        row = rows.get(source.name)
        if (
            row is None
            or not is_scheduled(source, cfg)
            or row.state not in FAILING_STATES
            or row.consecutive_failures < threshold
        ):
            continue
        findings.append(
            Finding(
                rule=c.AZURE_SYNC_FAILING,
                dedupe_key=f"{c.AZURE_SYNC_FAILING}:{source.name}",
                severity=(
                    Severity.CRITICAL
                    if row.consecutive_failures >= 2 * threshold
                    else Severity.WARNING
                ),
                title=(
                    f"Azure Insights {source.label}: "
                    f"{row.consecutive_failures} failed runs in a row"
                ),
                summary=(
                    f"The {source.label} poll failed {row.consecutive_failures} "
                    f"consecutive runs (alert at {threshold}). Last error: "
                    f"{row.last_error_code or 'unknown'}"
                    + (f" ({row.last_error_message})" if row.last_error_message else "")
                    + "."
                ),
                component="Azure Insights",
                details={
                    "value": row.consecutive_failures,
                    "threshold": threshold,
                    "unit": "runs",
                    "counts": {"source": source.name, "state": row.state},
                },
            )
        )
    return findings
