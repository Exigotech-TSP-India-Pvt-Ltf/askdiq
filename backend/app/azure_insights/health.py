"""Azure health (Phase 4): Resource Health of the account and Service Health
events for our service and region.

- ``health`` (every ``AZURE_HEALTH_INTERVAL_SECONDS``, 10 min):
  ``{account}/providers/Microsoft.ResourceHealth/availabilityStatuses/current``
  (api 2025-05-01). One row per status *change* in ``azure_health_status``
  (a poll with the same status only moves ``checked_at``).
- ``service_health`` (every ``AZURE_SERVICE_HEALTH_INTERVAL_SECONDS``, 30 min,
  only with ``AZURE_SERVICE_HEALTH_ENABLED``): subscription
  ``Microsoft.ResourceHealth/events`` (api 2025-05-01). Only events whose
  impacted service is OpenAI / Foundry / Cognitive Services in our region
  (or global) are kept, upserted by tracking id.

Both refresh ``azure.health`` (the console-wide banner and the Monitoring
health card) and announce it on the live stream, so a Degraded / Unavailable
status or an active incident shows within one poll, without a reload.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.azure_insights.capacity_schemas import (
    AzureHealthRead,
    HealthBannerState,
    ResourceHealth,
    ServiceEvent,
)
from app.azure_insights.scheduler import (
    AzureSource,
    SourceContext,
    SourceResult,
    register_source,
)
from app.core.config import Settings, get_settings
from app.dashboard.snapshots import Gather, register
from app.jobs.queue import utcnow
from app.models.db_models import AzureHealthStatus, AzureServiceEvent

HEALTH_SOURCE = "health"
SERVICE_HEALTH_SOURCE = "service_health"
HEALTH_KIND = "azure.health"
RESOURCE_HEALTH_API_VERSION = "2025-05-01"
SERVICE_HEALTH_API_VERSION = "2025-05-01"
# Service Health look-back on every poll (events are upserted by tracking id).
EVENTS_LOOKBACK_DAYS = 30
EVENTS_SHOWN = 20
HISTORY_SHOWN = 20
HISTORY_RETENTION_DAYS = 400
# A status / event not confirmed by this many intervals is ignored (polling
# stopped, or Azure no longer lists it).
STALE_INTERVALS = 3
MAX_TEXT = 2000

AVAILABLE = "Available"
DEGRADED = "Degraded"
UNAVAILABLE = "Unavailable"
UNKNOWN = "Unknown"
SERVICE_ISSUE = "ServiceIssue"
ACTIVE = "Active"
SERVICE_KEYWORDS = ("openai", "foundry", "cognitive")
GLOBAL_REGION = "global"
HEALTH_HREF = "/admin/governance?tab=Monitoring#azure-health"

_TAG = re.compile(r"<[^>]+>")


# ---------------------------------------------------------------------------
# Parsing (pure)
# ---------------------------------------------------------------------------


def parse_time(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # ARM times are UTC; a value without an offset is read as UTC.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def plain_text(value: Any, size: int = MAX_TEXT) -> str | None:
    """Service Health summaries are HTML: tags removed, one line, capped."""
    if not value:
        return None
    text = " ".join(_TAG.sub(" ", str(value)).split())
    return text[:size] or None


def parse_availability(body: dict[str, Any]) -> dict[str, Any]:
    props = body.get("properties") or {}
    state = str(props.get("availabilityState") or UNKNOWN)
    return {
        "availability_state": state[:24],
        "summary": plain_text(props.get("summary")),
        "reason_type": plain_text(props.get("reasonType"), 64),
        # The API spells it "occuredTime".
        "occurred_at": parse_time(
            props.get("occuredTime") or props.get("occurredTime")
        ),
        "reported_at": parse_time(props.get("reportedTime")),
        "resolution_eta": parse_time(props.get("resolutionETA")),
    }


def normalise_region(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _relevant_service(name: Any) -> bool:
    lowered = str(name or "").lower()
    return any(word in lowered for word in SERVICE_KEYWORDS)


def event_scope(
    item: dict[str, Any], region: str
) -> tuple[list[str], list[str]] | None:
    """(services, regions) of the impacts on our service in our region or
    global; None when the event does not concern us. An impact without
    regions counts as global."""
    props = item.get("properties") or {}
    ours = normalise_region(region)
    services: list[str] = []
    regions: list[str] = []
    for impact in props.get("impact") or []:
        if not isinstance(impact, dict) or not _relevant_service(
            impact.get("impactedService")
        ):
            continue
        names = [
            str(r.get("impactedRegion") or "")
            for r in impact.get("impactedRegions") or []
            if isinstance(r, dict)
        ]
        matched = [
            n for n in names if normalise_region(n) in {ours, GLOBAL_REGION}
        ] or ([] if names else [GLOBAL_REGION])
        if matched:
            services.append(str(impact.get("impactedService"))[:120])
            regions.extend(matched)
    if not services:
        return None
    return sorted(set(services)), sorted(set(regions))


def parse_event(
    item: dict[str, Any], region: str, now: datetime
) -> dict[str, Any] | None:
    """An in-scope event as a row; None when out of scope or unnamed."""
    tracking_id = item.get("name")
    scope = event_scope(item, region)
    if not tracking_id or scope is None:
        return None
    props = item.get("properties") or {}
    return {
        "tracking_id": str(tracking_id)[:64],
        "event_type": plain_text(props.get("eventType"), 48),
        "status": plain_text(props.get("status"), 24),
        "level": plain_text(props.get("level"), 24),
        "title": plain_text(props.get("title"), 500),
        "summary": plain_text(props.get("summary")),
        "services": scope[0],
        "regions": scope[1],
        "impact_start": parse_time(props.get("impactStartTime")),
        "impact_mitigation": parse_time(props.get("impactMitigationTime")),
        "last_update": parse_time(props.get("lastUpdateTime")),
        "first_seen_at": now,
        "last_seen_at": now,
    }


def query_start(now: datetime) -> str:
    """``queryStartTime`` (MM/DD/YYYY) of the look-back window."""
    return (now - timedelta(days=EVENTS_LOOKBACK_DAYS)).strftime("%m/%d/%Y")


# ---------------------------------------------------------------------------
# Polls
# ---------------------------------------------------------------------------


async def record_status(
    session: AsyncSession, resource_id: str, values: dict[str, Any], now: datetime
) -> bool:
    """Store a poll; returns True when the status changed (a new history row)."""
    latest = (
        await session.execute(
            select(AzureHealthStatus)
            .where(AzureHealthStatus.resource_id == resource_id)
            .order_by(AzureHealthStatus.started_at.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if latest is not None and latest.availability_state == values["availability_state"]:
        for key, value in values.items():
            setattr(latest, key, value)
        latest.checked_at = now
        return False
    if latest is not None:
        latest.ended_at = now
    await session.execute(
        insert(AzureHealthStatus)
        .values(resource_id=resource_id, started_at=now, checked_at=now, **values)
        .on_conflict_do_nothing()
    )
    return True


async def run_health(ctx: SourceContext) -> SourceResult:
    account = str(ctx.settings.azure_account_resource_id)
    async with ctx.arm_client() as api:
        body = await api.get_json(
            f"{account}/providers/Microsoft.ResourceHealth/availabilityStatuses/current",
            {"api-version": RESOURCE_HEALTH_API_VERSION},
        )
    values = parse_availability(body)
    async with ctx.session_factory() as session:
        changed = await record_status(session, account.lower(), values, ctx.now)
        await session.execute(
            delete(AzureHealthStatus).where(
                AzureHealthStatus.ended_at.isnot(None),
                AzureHealthStatus.ended_at
                < ctx.now - timedelta(days=HISTORY_RETENTION_DAYS),
            )
        )
        await session.commit()
    return SourceResult(
        rows=1, detail={"state": values["availability_state"], "changed": changed}
    )


async def upsert_events(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    stmt = insert(AzureServiceEvent).values(rows)
    update = {
        c: stmt.excluded[c]
        for c in rows[0]
        if c not in {"tracking_id", "first_seen_at"}
    }
    await session.execute(
        stmt.on_conflict_do_update(index_elements=["tracking_id"], set_=update)
    )


async def run_service_health(ctx: SourceContext) -> SourceResult:
    settings, now = ctx.settings, ctx.now
    region = str(settings.azure_openai_region or "")
    seen = 0
    rows: dict[str, dict[str, Any]] = {}
    async with ctx.arm_client() as api:
        async for item in api.paged(
            f"/subscriptions/{settings.azure_subscription_id}/providers/"
            "Microsoft.ResourceHealth/events",
            {
                "api-version": SERVICE_HEALTH_API_VERSION,
                "queryStartTime": query_start(now),
            },
        ):
            seen += 1
            row = parse_event(item, region, now)
            if row is not None:
                rows[row["tracking_id"]] = row
    async with ctx.session_factory() as session:
        await upsert_events(session, list(rows.values()))
        await session.execute(
            delete(AzureServiceEvent).where(
                AzureServiceEvent.last_seen_at
                < now - timedelta(days=HISTORY_RETENTION_DAYS)
            )
        )
        await session.commit()
    return SourceResult(rows=len(rows), detail={"events": seen, "in_scope": len(rows)})


# ---------------------------------------------------------------------------
# Read model (snapshot ``azure.health``) and the banner
# ---------------------------------------------------------------------------


def _resource(row: AzureHealthStatus, stale_before: datetime) -> ResourceHealth:
    return ResourceHealth(
        availability_state=row.availability_state,
        summary=row.summary,
        reason_type=row.reason_type,
        occurred_at=row.occurred_at,
        reported_at=row.reported_at,
        resolution_eta=row.resolution_eta,
        since=row.started_at,
        checked_at=row.checked_at,
        ended_at=row.ended_at,
        stale=row.ended_at is None and row.checked_at < stale_before,
    )


def _event(row: AzureServiceEvent, active_after: datetime | None) -> ServiceEvent:
    return ServiceEvent(
        tracking_id=row.tracking_id,
        event_type=row.event_type,
        status=row.status,
        level=row.level,
        title=row.title,
        summary=row.summary,
        services=list(row.services or []),
        regions=list(row.regions or []),
        impact_start=row.impact_start,
        impact_mitigation=row.impact_mitigation,
        last_update=row.last_update,
        last_seen_at=row.last_seen_at,
        active=(
            active_after is not None
            and (row.status or "").lower() == ACTIVE.lower()
            and row.last_seen_at >= active_after
        ),
    )


def banner_state(
    connected: bool,
    resource: ResourceHealth | None,
    events: list[ServiceEvent],
) -> HealthBannerState:
    """Shown for an Unavailable / Degraded account (fresh status) or an
    active ServiceIssue for our service and region; hidden otherwise."""
    if not connected:
        return HealthBannerState(show=False)
    candidates: list[HealthBannerState] = []
    if resource is not None and not resource.stale:
        state = resource.availability_state
        if state in {UNAVAILABLE, DEGRADED}:
            candidates.append(
                HealthBannerState(
                    show=True,
                    level="critical" if state == UNAVAILABLE else "warning",
                    title=f"Azure OpenAI resource {state.lower()}",
                    message=resource.summary
                    or "Azure Resource Health reports a problem with the resource.",
                    href=HEALTH_HREF,
                )
            )
    for event in events:
        if event.active and event.event_type == SERVICE_ISSUE:
            candidates.append(
                HealthBannerState(
                    show=True,
                    level="critical" if (event.level or "") == "Error" else "warning",
                    title=f"Azure service incident: {event.title or event.tracking_id}",
                    message=(
                        f"{', '.join(event.services)} · {', '.join(event.regions)}"
                        f" · tracking id {event.tracking_id}"
                    ),
                    href=HEALTH_HREF,
                    tracking_id=event.tracking_id,
                )
            )
    if not candidates:
        return HealthBannerState(show=False)
    # Critical first; the resource's own status before an incident.
    return min(candidates, key=lambda b: 0 if b.level == "critical" else 1)


async def read_health(
    session: AsyncSession, settings: Settings, now: datetime
) -> tuple[ResourceHealth | None, list[ResourceHealth], list[ServiceEvent]]:
    resource_id = (settings.azure_account_resource_id or "").lower()
    stale_before = now - timedelta(
        seconds=STALE_INTERVALS * settings.azure_health_interval_seconds
    )
    history_rows = (
        (
            await session.execute(
                select(AzureHealthStatus)
                .where(AzureHealthStatus.resource_id == resource_id)
                .order_by(AzureHealthStatus.started_at.desc())
                .limit(HISTORY_SHOWN)
            )
        )
        .scalars()
        .all()
    )
    history = [_resource(r, stale_before) for r in history_rows]
    active_after = (
        now
        - timedelta(
            seconds=STALE_INTERVALS * settings.azure_service_health_interval_seconds
        )
        if settings.azure_service_health_enabled
        else None
    )
    event_rows = (
        (
            await session.execute(
                select(AzureServiceEvent)
                .where(
                    AzureServiceEvent.last_seen_at
                    >= now - timedelta(days=EVENTS_LOOKBACK_DAYS)
                )
                .order_by(
                    func.coalesce(
                        AzureServiceEvent.last_update, AzureServiceEvent.last_seen_at
                    ).desc()
                )
                .limit(EVENTS_SHOWN)
            )
        )
        .scalars()
        .all()
    )
    events = [_event(r, active_after) for r in event_rows]
    return (history[0] if history else None), history, events


async def build_health(
    session: AsyncSession, settings: Settings, now: datetime | None = None
) -> AzureHealthRead:
    now = now or utcnow()
    connected = settings.azure_insights_configured
    resource, history, events = await read_health(session, settings, now)
    return AzureHealthRead(
        connected=connected,
        service_health_enabled=settings.azure_service_health_enabled,
        banner=banner_state(connected, resource, events),
        resource=resource,
        history=history,
        events=events,
        generated_at=now,
    )


async def _health_snapshot(gather: Gather, _params: dict[str, Any]) -> dict:
    settings = get_settings()
    (payload,) = await gather(lambda db: build_health(db, settings))
    return payload.model_dump(mode="json")


register(HEALTH_KIND, _health_snapshot, [{}])

register_source(
    AzureSource(
        name=HEALTH_SOURCE,
        label="Resource Health",
        interval=lambda s: s.azure_health_interval_seconds,
        run=run_health,
        snapshot_kinds=(HEALTH_KIND,),
        order=50,
    )
)
register_source(
    AzureSource(
        name=SERVICE_HEALTH_SOURCE,
        label="Service Health events",
        interval=lambda s: s.azure_service_health_interval_seconds,
        run=run_service_health,
        enabled=lambda s: s.azure_service_health_enabled,
        snapshot_kinds=(HEALTH_KIND,),
        order=55,
    )
)
