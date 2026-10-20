"""Read models of the Azure Insights API (served from snapshots; never calls Azure)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.azure_insights import status as sync_status
from app.azure_insights.scheduler import (
    STATUS_KIND,
    can_run,
    is_scheduled,
    registered_sources,
)
from app.azure_insights.schemas import AzureSourceStatus, AzureStatusRead
from app.core.config import Settings, get_settings
from app.dashboard.snapshots import Gather, register
from app.jobs.queue import utcnow

METRICS_SOURCE = "metrics"


async def build_status(
    session: AsyncSession, settings: Settings, now: datetime | None = None
) -> AzureStatusRead:
    now = now or utcnow()
    rows = await sync_status.read_all(session)
    sources = []
    for source in registered_sources():
        row = rows.get(source.name)
        cap = source.call_cap(settings) if source.call_cap else None
        if not can_run(source, settings):
            state = "not_configured"
        elif row is None:
            state = "never_run"
        else:
            state = row.state
        sources.append(
            AzureSourceStatus(
                source=source.name,
                label=source.label,
                scheduled=is_scheduled(source, settings),
                needs_credentials=source.needs_credentials,
                interval_seconds=source.interval(settings),
                state=state,
                last_run_at=row.last_run_at if row else None,
                last_success_at=row.last_success_at if row else None,
                last_error_at=row.last_error_at if row else None,
                last_error_code=row.last_error_code if row else None,
                last_error_message=row.last_error_message if row else None,
                consecutive_failures=row.consecutive_failures if row else 0,
                next_run_at=(
                    row.next_run_at if row and is_scheduled(source, settings) else None
                ),
                calls_this_month=sync_status.calls_this_month(row, now.date()),
                call_cap=cap,
            )
        )
    metrics_row = rows.get(METRICS_SOURCE)
    return AzureStatusRead(
        connected=settings.azure_insights_configured,
        reason=None if settings.azure_insights_configured else "not_configured",
        region=settings.azure_openai_region or None,
        deployment_type=settings.azure_deployment_type,
        metrics_calls_this_month=sync_status.calls_this_month(metrics_row, now.date()),
        metrics_call_cap=settings.azure_metrics_monthly_call_cap,
        sources=sources,
        generated_at=now,
    )


async def _status_snapshot(gather: Gather, _params: dict[str, Any]) -> dict:
    settings = get_settings()
    (payload,) = await gather(lambda db: build_status(db, settings))
    return payload.model_dump(mode="json")


register(STATUS_KIND, _status_snapshot, [{}])
