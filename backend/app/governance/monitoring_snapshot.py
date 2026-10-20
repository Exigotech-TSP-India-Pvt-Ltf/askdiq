"""Governance Monitoring tab payload, kept in memory per audience.

The database part (latency, traffic, guardrail flags, outcome mix, trace
total and the resource aggregate) is computed in parallel and kept in a
:class:`~app.governance.live_cache.LiveCache` (refreshed in the background
once older than one metrics sample interval). Process-local live values
(request / active-query counters, Azure quota telemetry, online quality and
the resource gauges the stream last pushed) are overlaid on every read, so
they are always current.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.concurrency import ScopedRunner
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.middleware import METRICS
from app.dashboard import active_visitors
from app.governance import timeseries
from app.governance.live_cache import LiveCache
from app.governance.monitoring import (
    LATENCY_UNIT,
    count_traces,
    labelled,
    persisted_error_counts,
    scoped_latency_query,
    stage_latency_summary,
)
from app.governance.online_eval import quality_tracker
from app.governance.quota_series import quota_series
from app.governance.resource_store import resources_snapshot
from app.models.db_models import LatencyRecord

logger = get_logger(__name__)

ERROR_SOURCE = "chat_messages.outcome+audit_log.server_error"


async def _resources(session: AsyncSession) -> dict[str, Any]:
    """Aggregated resource snapshot of all live workers (never fails the page)."""
    try:
        return await resources_snapshot(session)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("resources_snapshot_failed", exc_info=True)
        return {}


async def _latency_records(
    session: AsyncSession, audience: str, since: Any
) -> list[LatencyRecord]:
    stmt = await scoped_latency_query(
        session,
        audience,
        select(LatencyRecord).where(LatencyRecord.created_at >= since),
    )
    return list((await session.execute(stmt)).scalars().all())


async def monitoring_payload(
    audience: str, runner: ScopedRunner | None = None
) -> dict[str, Any]:
    """The database-derived part of ``GET /governance/monitoring``."""
    runner = runner or ScopedRunner()
    window_hours = timeseries.HOURS
    window = f"{window_hours}h"
    now = timeseries.utcnow()
    since = now - timedelta(hours=window_hours)
    starts = timeseries.hour_starts(now)
    start = starts[0]
    (
        records,
        active_users,
        errors,
        traces_total,
        percentiles,
        lat,
        turns,
        errs,
        flags,
        mix,
        resources,
    ) = await runner.gather(
        lambda s: _latency_records(s, audience, since),
        lambda s: active_visitors.count_active(s, audience, since, now),
        lambda s: persisted_error_counts(s, audience, since),
        lambda s: count_traces(s, audience),
        lambda s: timeseries.stage_percentiles(s, audience, since),
        lambda s: timeseries.latency_rollup(s, audience, start, now),
        lambda s: timeseries.turns_rollup(s, audience, start, now, now),
        lambda s: timeseries.server_error_rollup(s, audience, start, now),
        lambda s: timeseries.guardrail_flag_breakdown(s, audience, since),
        lambda s: timeseries.outcome_mix(s, audience, since),
        _resources,
    )
    totals = [r.total_ms for r in records]
    stages = stage_latency_summary(records)
    avg_total_ms = sum(totals) / len(totals) if totals else None
    return jsonable_encoder(
        {
            "window_hours": window_hours,
            "latency_unit": LATENCY_UNIT,
            "queries_24h": len(records),
            "active_users": active_users,
            "avg_latency_ms": avg_total_ms,
            "total_latency_ms": avg_total_ms,
            "p95_latency_ms": percentiles["total"]["p95_ms"],
            "error_rate": errors["error_rate"],
            "error_counts": {**errors, "window": window, "source": ERROR_SOURCE},
            "traces_total": traces_total,
            "embedding_latency_ms": stages["embedding"]["avg_ms"],
            "retrieval_latency_ms": stages["retrieval"]["avg_ms"],
            "llm_latency_ms": stages["llm"]["avg_ms"],
            "reranker_latency_ms": stages["reranker"]["avg_ms"],
            "stage_latency": stages,
            "stage_percentiles": percentiles,
            "hourly": timeseries.hourly_series(starts, lat, turns, errs),
            "guardrail_flag_breakdown": flags,
            "outcome_mix": mix,
            "counts": {
                "queries_24h": labelled(len(records), window, "latency_metrics"),
                "active_users": labelled(active_users, window, active_visitors.SOURCE),
                "errors": labelled(errors["errors"], window, ERROR_SOURCE),
                "traces_total": labelled(traces_total, "all_time", "latency_metrics"),
            },
            "resources": resources,
        }
    )


def with_live_values(
    base: dict[str, Any], audience: str, resources: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The full response: ``base`` plus the process-local live values.

    Keys keep the order the route always returned.
    """
    window = f"{base['window_hours']}h"
    process_counter = audience == "overview"
    requests_total = METRICS["requests"] if process_counter else base["queries_24h"]
    res = resources if resources is not None else base["resources"]
    effective = res.get("effective") or {}
    counts = base["counts"]
    return jsonable_encoder(
        {
            "window_hours": base["window_hours"],
            "latency_unit": base["latency_unit"],
            "api_requests": requests_total,
            "queries_24h": base["queries_24h"],
            "active_users": base["active_users"],
            "active_queries": METRICS["active_queries"],
            "avg_latency_ms": base["avg_latency_ms"],
            "total_latency_ms": base["total_latency_ms"],
            "p95_latency_ms": base["p95_latency_ms"],
            "error_rate": base["error_rate"],
            "error_counts": base["error_counts"],
            "traces_total": base["traces_total"],
            "embedding_latency_ms": base["embedding_latency_ms"],
            "retrieval_latency_ms": base["retrieval_latency_ms"],
            "llm_latency_ms": base["llm_latency_ms"],
            "reranker_latency_ms": base["reranker_latency_ms"],
            "stage_latency": base["stage_latency"],
            "stage_percentiles": base["stage_percentiles"],
            "hourly": base["hourly"],
            "guardrail_flag_breakdown": base["guardrail_flag_breakdown"],
            "outcome_mix": base["outcome_mix"],
            "counts": {
                "api_requests": labelled(
                    requests_total,
                    "since_process_start" if process_counter else window,
                    "in_memory_counter" if process_counter else "latency_metrics",
                ),
                "queries_24h": counts["queries_24h"],
                "active_users": counts["active_users"],
                "active_queries": labelled(
                    METRICS["active_queries"], "now", "in_memory_counter"
                ),
                "errors": counts["errors"],
                "traces_total": counts["traces_total"],
            },
            # Legacy keys, now from the shared aggregate of every live worker:
            # CPU / memory % of the container when it is limited, else the
            # host; RAM MB = summed RSS of all API workers.
            "process_cpu_percent": effective.get("cpu_percent"),
            "process_ram_mb": (res.get("process") or {}).get("rss_mb"),
            "process_ram_percent": effective.get("memory_percent"),
            "resources": res,
            "azure_quota_telemetry": quota_series.sample(),
            "azure_quota_series": quota_series.points(),
            "online_quality_kpis": quality_tracker.get_kpis(),
        }
    )


async def _compute(audience: str, runner: ScopedRunner) -> dict[str, Any]:
    return await monitoring_payload(audience, runner)


monitoring_cache = LiveCache(
    "monitoring",
    _compute,
    lambda: get_settings().metrics_sample_interval_seconds,
)
