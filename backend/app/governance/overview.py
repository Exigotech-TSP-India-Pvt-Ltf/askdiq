"""Governance Overall tab in one call (newplan §4.2, B4.2.4).

Health status (Healthy / Degraded / Incident) from configured thresholds on
error rate, p95 latency and Azure quota utilisation; KPI cards with 24 hourly
sparkline points; and the most recent CRITICAL / ALERT incidents.

The payload is built from five single-statement queries run in parallel with
``gather_scoped`` (one session each), so its query count does not grow with
data volume (asserted in tests with a SQLAlchemy event counter).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.concurrency import ScopedRunner
from app.core.config import Settings, get_settings
from app.dashboard import active_visitors
from app.governance import logs, timeseries
from app.governance.log_summary import INCIDENT_LEVELS
from app.governance.monitoring import LATENCY_UNIT
from app.governance.online_eval import quality_tracker
from app.governance.quota_series import quota_series, utilization_pct
from app.models.db_models import AuditLog

HEALTHY, DEGRADED, INCIDENT = "Healthy", "Degraded", "Incident"
_SEVERITY = {HEALTHY: 0, DEGRADED: 1, INCIDENT: 2}
# Upper bound on SQL statements per /governance/overview call (5 aggregate
# queries + at most one audience classification + one test-IP rules read).
OVERVIEW_MAX_QUERIES = 7


def classify(value: float | None, degraded: float, incident: float) -> str:
    """``value >= incident`` -> Incident, ``>= degraded`` -> Degraded; None is Healthy."""
    if value is None:
        return HEALTHY
    if value >= incident:
        return INCIDENT
    if value >= degraded:
        return DEGRADED
    return HEALTHY


def health_status(
    error_rate_pct: float | None,
    p95_ms: float | None,
    quota_utilization_pct: float | None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    signals = {
        "error_rate": (
            error_rate_pct,
            "%",
            cfg.governance_health_error_rate_degraded_pct,
            cfg.governance_health_error_rate_incident_pct,
        ),
        "p95_latency": (
            p95_ms,
            LATENCY_UNIT,
            cfg.governance_health_p95_degraded_ms,
            cfg.governance_health_p95_incident_ms,
        ),
        "quota_utilization": (
            quota_utilization_pct,
            "%",
            cfg.governance_health_quota_degraded_pct,
            cfg.governance_health_quota_incident_pct,
        ),
    }
    out: dict[str, Any] = {}
    worst = HEALTHY
    for name, (value, unit, degraded, incident) in signals.items():
        status = classify(value, degraded, incident)
        if _SEVERITY[status] > _SEVERITY[worst]:
            worst = status
        out[name] = {
            "value": value,
            "unit": unit,
            "status": status,
            "degraded_at": degraded,
            "incident_at": incident,
        }
    return {
        "status": worst,
        "window_minutes": cfg.governance_health_window_minutes,
        "signals": out,
    }


async def recent_incidents(
    db: AsyncSession, audience: str, since: datetime, limit: int
) -> list[dict[str, Any]]:
    stmt = (
        select(AuditLog)
        .where(
            AuditLog.created_at >= since,
            logs.level_sql().in_(INCIDENT_LEVELS),
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
    )
    stmt = await timeseries.scope_audit(db, audience, stmt)
    return [logs.to_item(row) for row in (await db.execute(stmt)).scalars().all()]


def _rate(num: float, den: float) -> float:
    return num * 100 / den if den else 0.0


def _kpi(
    value: Any,
    unit: str | None,
    sparkline: list[Any],
    source: str,
    window: str = "24h",
) -> dict[str, Any]:
    return {
        "value": value,
        "unit": unit,
        "sparkline": sparkline,
        "window": window,
        "source": source,
    }


def latency_population(lat: timeseries.Rollup) -> tuple[tuple[str, str], str]:
    """(p50 key, p95 key) and source for the latency KPIs.

    Both percentiles always come from the same rows: RAG pipeline queries when
    the window has any (instant conversational replies would drag p50 to a few
    ms while p95 stays in seconds), else every latency row.
    """
    if int(lat.total.get("pipeline_requests") or 0) > 0:
        return ("pipeline_p50_ms", "pipeline_p95_ms"), "latency_metrics.pipeline"
    return ("p50_ms", "p95_ms"), "latency_metrics"


def build_kpis(
    starts: list[datetime],
    lat: timeseries.Rollup,
    turns: timeseries.Rollup,
    errs: timeseries.Rollup,
    visitors: timeseries.Rollup,
) -> dict[str, Any]:
    series = timeseries.hourly_series(starts, lat, turns, errs)
    t, lt = turns.total, lat.total
    server_errors = int(errs.total.get("errors") or 0)
    requests = int(t.get("turns") or 0) + server_errors
    errors = int(t.get("failed") or 0) + server_errors

    def hourly(rollup: timeseries.Rollup, key: str, default: Any = 0) -> list[Any]:
        return [rollup.hours.get(s, {}).get(key, default) for s in starts]

    grounded = hourly(turns, "grounded")
    known = hourly(turns, "grounded_known")
    turns_src = "chat_messages"
    lat_keys, lat_src = latency_population(lat)
    return {
        "requests": _kpi(
            requests, None, [p["requests"] for p in series], f"{turns_src}+audit_log"
        ),
        "error_rate": _kpi(
            _rate(errors, requests),
            "%",
            [p["error_rate"] for p in series],
            f"{turns_src}.outcome+audit_log.server_error",
        ),
        "latency_p50": _kpi(
            lt.get(lat_keys[0]),
            LATENCY_UNIT,
            hourly(lat, lat_keys[0], None),
            lat_src,
        ),
        "latency_p95": _kpi(
            lt.get(lat_keys[1]),
            LATENCY_UNIT,
            hourly(lat, lat_keys[1], None),
            lat_src,
        ),
        "active_users": _kpi(
            int(visitors.total.get(active_visitors.VISITORS) or 0),
            None,
            [int(v or 0) for v in hourly(visitors, active_visitors.VISITORS)],
            active_visitors.SOURCE,
        ),
        "groundedness": _kpi(
            _rate(int(t.get("grounded") or 0), int(t.get("grounded_known") or 0))
            if t.get("grounded_known")
            else None,
            "%",
            [
                _rate(g or 0, k) if k else None
                for g, k in zip(grounded, known, strict=True)
            ],
            f"{turns_src}.grounded",
        ),
        "blocked_or_flagged": _kpi(
            int(t.get("blocked_or_flagged") or 0),
            None,
            [int(v or 0) for v in hourly(turns, "blocked_or_flagged")],
            f"{turns_src}.outcome+guardrail_flags",
        ),
        "cost_today": _kpi(
            float(t.get("cost_today_usd") or 0.0),
            "USD",
            [float(v or 0.0) for v in hourly(turns, "cost_usd")],
            f"{turns_src}.cost_usd",
            # The value sums from 00:00 UTC, not over the trailing 24 hours.
            window="today_utc",
        ),
    }


def _recent_error_rate(turns: timeseries.Rollup, errs: timeseries.Rollup) -> float:
    server = int(errs.total.get("recent_errors") or 0)
    requests = int(turns.total.get("recent_turns") or 0) + server
    failed = int(turns.total.get("recent_failed") or 0) + server
    return _rate(failed, requests)


async def overview_payload(
    runner: ScopedRunner, audience: str, now: datetime | None = None
) -> dict[str, Any]:
    cfg = get_settings()
    now = now or timeseries.utcnow()
    starts = timeseries.hour_starts(now)
    start = starts[0]
    recent_since = now - timedelta(minutes=cfg.governance_health_window_minutes)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    incidents_since = now - timedelta(hours=cfg.governance_incidents_window_hours)

    lat, turns, errs, incidents, visitors = await runner.gather(
        lambda s: timeseries.latency_rollup(s, audience, start, recent_since),
        lambda s: timeseries.turns_rollup(
            s, audience, start, recent_since, today_start
        ),
        lambda s: timeseries.server_error_rollup(s, audience, start, recent_since),
        lambda s: recent_incidents(
            s, audience, incidents_since, cfg.governance_incidents_limit
        ),
        lambda s: active_visitors.hourly_active(s, audience, start, now),
    )
    return {
        "audience": audience,
        "window_hours": timeseries.HOURS,
        "hours": [s.isoformat() + "Z" for s in starts],
        "health": health_status(
            _recent_error_rate(turns, errs),
            lat.total.get("recent_p95_ms"),
            utilization_pct(quota_series.sample()),
            cfg,
        ),
        "kpis": build_kpis(starts, lat, turns, errs, visitors),
        "incidents": incidents,
        "online_quality_kpis": quality_tracker.get_kpis(),
    }
