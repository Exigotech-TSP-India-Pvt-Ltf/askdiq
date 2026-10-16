"""Governance endpoints under ``/admin/governance`` (newplan §4.5, B4.2).

Monitoring, traces (legacy array + keyset page + detail), logs (keyset page +
streaming CSV export), the one-call overview and the live SSE stream. All are
``async``, guarded with ``dashboard:read`` and keep the ``audience`` scope.
Routers only validate input and delegate to ``app.governance.*``.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.admin_usage import _sanitize_csv_cell
from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.concurrency import ScopedRunner, get_scoped_runner
from app.core.config import get_settings
from app.core.database import get_db
from app.core.http_cache import cached_response
from app.core.logging_config import get_logger
from app.core.middleware import METRICS
from app.core.sse import event_stream
from app.governance import logs, timeseries, traces
from app.governance.audience import Audience, audience_scope, build_audience_filter
from app.governance.audit import record_audit_event
from app.governance.monitoring import (
    LATENCY_UNIT,
    count_traces,
    labelled,
    persisted_error_counts,
    scoped_latency_query,
    stage_latency_summary,
)
from app.governance.online_eval import quality_tracker
from app.governance.overview import overview_payload
from app.governance.pagination import Cursor
from app.governance.quota_series import quota_series
from app.governance.stream import GovernanceStreamHub, get_governance_hub
from app.models.db_models import ChatSession, LatencyRecord
from app.schemas.governance import (
    GovernanceLogPage,
    GovernanceOverview,
    GovernanceTraceDetail,
    GovernanceTraceItem,
    GovernanceTracePage,
    LatencyBucketFilter,
    LogLevel,
    LogService,
    TraceOutcomeFilter,
    TraceStatusFilter,
)

router = APIRouter(tags=["governance"])
logger = get_logger(__name__)

MAX_PAGE = 200
DEFAULT_PAGE = 50
MAX_Q = 200
LEGACY_TRACES_MAX = 500
REQUEST_ID_PATTERN = r"^[A-Za-z0-9._:\-]{1,64}$"
CURSOR_DOC = "Opaque cursor from the previous page's `next_cursor`."

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not authenticated."},
    403: {"description": "Missing `dashboard:read`."},
    422: {"description": "Invalid filter, cursor or date range."},
}

read_guard = require_permission(Permission.DASHBOARD_READ)


def _naive_utc(value: datetime | None) -> datetime | None:
    """DB columns are naive UTC; accept aware input and normalise it."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _date_range(
    date_from: datetime | None, date_to: datetime | None
) -> tuple[datetime | None, datetime | None]:
    start, end = _naive_utc(date_from), _naive_utc(date_to)
    if start and end and start > end:
        raise HTTPException(422, detail="`from` must be before `to`.")
    return start, end


def _clean_q(q: str | None) -> str | None:
    return q.strip() or None if q else None


def _unique(values: list[Any] | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(v) for v in values or ()))


# ============================================================================
# MONITORING
# ============================================================================

_MONITORING_DESCRIPTION = (
    "Rolling 24h snapshot for the Governance Monitoring and Overall tabs.\n\n"
    '- Every latency is in **milliseconds** (`latency_unit: "ms"`).\n'
    "- `stage_latency.<stage>` gives `avg_ms` plus `sample_count`; a stage with "
    "no samples is `null` (never 0). The flat `*_latency_ms` fields mirror it.\n"
    "- `stage_percentiles.<stage|total>`: `p50_ms`, `p95_ms`, `p99_ms` "
    "(`percentile_cont`), null when the stage has no samples.\n"
    "- `hourly`: 24 zero-filled hourly buckets (oldest first) with `requests`, "
    "`errors`, `error_rate` and latency avg/p50/p95 (null when empty).\n"
    "- `azure_quota_series`: per-process headroom samples; "
    "`azure_quota_telemetry` is the live 60 s window.\n"
    "- `guardrail_flag_breakdown` and `outcome_mix`: assistant turns in 24h.\n"
    "- `total_latency_ms` is the canonical end-to-end turn latency (same value as "
    "`avg_latency_ms`); use it instead of `llm_latency_ms` for overall latency.\n"
    "- `error_rate` (percent) is derived from persisted rows (`llm_failed` turns "
    "plus `server_error` audit rows), audience-scoped; `error_counts` has the "
    "inputs. It no longer uses in-memory counters.\n"
    "- `counts` labels every count with its `window` and `source`.\n"
    "- `traces_total` is the true number of traces for the audience."
)


async def _process_sample() -> tuple[float | None, float | None, float | None]:
    try:
        import psutil  # noqa: PLC0415 - optional at runtime; import failure is tolerated

        def _sample() -> tuple[float, float, float]:
            proc = psutil.Process()
            return (
                psutil.cpu_percent(None),
                proc.memory_info().rss / 1024 / 1024,
                proc.memory_percent(),
            )

        return await asyncio.to_thread(_sample)
    except Exception:
        logger.debug("psutil_metrics_unavailable", exc_info=True)
        return None, None, None


@router.get(
    "/governance/monitoring",
    summary="Governance monitoring snapshot",
    description=_MONITORING_DESCRIPTION,
    responses=_ERRORS,
)
async def governance_monitoring(
    audience: Audience = Depends(audience_scope),
    db: AsyncSession = Depends(get_db),
    runner: ScopedRunner = Depends(get_scoped_runner),
    user: AuthUser = Depends(read_guard),
) -> dict[str, Any]:
    window_hours = timeseries.HOURS
    window = f"{window_hours}h"
    now = timeseries.utcnow()
    since = now - timedelta(hours=window_hours)
    q_records = await scoped_latency_query(
        db, audience, select(LatencyRecord).where(LatencyRecord.created_at >= since)
    )
    records = (await db.execute(q_records)).scalars().all()
    totals = [r.total_ms for r in records]
    stages = stage_latency_summary(records)
    avg_total_ms = sum(totals) / len(totals) if totals else None

    aud_sess = await build_audience_filter(db, ChatSession.created_ip, audience)
    q_active = select(func.count(func.distinct(ChatSession.created_ip))).where(
        ChatSession.created_ip.isnot(None),
        ChatSession.last_activity_at >= since,
    )
    if aud_sess is not None:
        q_active = q_active.where(aud_sess)
    active_users = (await db.scalar(q_active)) or 0
    errors = await persisted_error_counts(db, audience, since)
    traces_total = await count_traces(db, audience)

    starts = timeseries.hour_starts(now)
    start = starts[0]
    percentiles, lat, turns, errs, flags, mix = await runner.gather(
        lambda s: timeseries.stage_percentiles(s, audience, since),
        lambda s: timeseries.latency_rollup(s, audience, start, now),
        lambda s: timeseries.turns_rollup(s, audience, start, now, now),
        lambda s: timeseries.server_error_rollup(s, audience, start, now),
        lambda s: timeseries.guardrail_flag_breakdown(s, audience, since),
        lambda s: timeseries.outcome_mix(s, audience, since),
    )
    cpu, ram_mb, ram_pct = await _process_sample()

    process_counter = audience == "overview"
    requests_total = METRICS["requests"] if process_counter else len(records)
    quota_data = quota_series.sample()
    error_source = "chat_messages.outcome+audit_log.server_error"

    return {
        "window_hours": window_hours,
        "latency_unit": LATENCY_UNIT,
        "api_requests": requests_total,
        "queries_24h": len(records),
        "active_users": active_users,
        "active_queries": METRICS["active_queries"],
        "avg_latency_ms": avg_total_ms,
        "total_latency_ms": avg_total_ms,
        "p95_latency_ms": percentiles["total"]["p95_ms"],
        "error_rate": errors["error_rate"],
        "error_counts": {**errors, "window": window, "source": error_source},
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
            "api_requests": labelled(
                requests_total,
                "since_process_start" if process_counter else window,
                "in_memory_counter" if process_counter else "latency_metrics",
            ),
            "queries_24h": labelled(len(records), window, "latency_metrics"),
            "active_users": labelled(active_users, window, "chat_sessions"),
            "active_queries": labelled(
                METRICS["active_queries"], "now", "in_memory_counter"
            ),
            "errors": labelled(errors["errors"], window, error_source),
            "traces_total": labelled(traces_total, "all_time", "latency_metrics"),
        },
        "process_cpu_percent": cpu,
        "process_ram_mb": ram_mb,
        "process_ram_percent": ram_pct,
        "azure_quota_telemetry": quota_data,
        "azure_quota_series": quota_series.points(),
        "online_quality_kpis": quality_tracker.get_kpis(),
    }


# ============================================================================
# TRACES
# ============================================================================

_TRACES_DESCRIPTION = (
    "**Legacy shape** (kept for existing callers): the most recent `/query` "
    "traces (newest first, at most `limit`, capped at 500) as a JSON array. "
    "The true number of traces for the audience is in the `X-Total-Count` "
    "header. New clients should use `GET /admin/governance/traces/page` "
    "(filters, keyset cursor, `total` in the body).\n\n"
    "`status` is derived from the turn's persisted `outcome` and "
    "`guardrail_flags`: `ok`, `ungrounded`, `blocked`, `failed`, or `unknown` "
    "when no turn was persisted. `total_ms` is milliseconds.\n\n"
    "**Deprecated / removed:** `trace_id` is no longer returned. It always "
    "equalled `request_id`; use `request_id`."
)


@router.get(
    "/governance/traces",
    summary="Recent pipeline traces (legacy array)",
    description=_TRACES_DESCRIPTION,
    response_model=list[GovernanceTraceItem],
    responses=_ERRORS,
)
async def governance_traces(
    response: Response,
    limit: int = Query(100, ge=1, description="Capped at 500."),
    audience: Audience = Depends(audience_scope),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(read_guard),
) -> list[dict[str, Any]]:
    items, _ = await traces.fetch_page(
        db, traces.TraceFilters(audience=audience), None, min(limit, LEGACY_TRACES_MAX)
    )
    response.headers["X-Total-Count"] = str(await count_traces(db, audience))
    return items


@router.get(
    "/governance/traces/page",
    summary="Filtered, cursor-paginated traces",
    description=(
        "Keyset pagination on `(created_at, id)`, newest first. Filters: "
        "`status`, `outcome` (normalised), `latency_bucket` (bounds from "
        "`GOVERNANCE_LATENCY_BUCKETS_MS`), `q` (redacted query text or request "
        "id, LIKE-escaped) and `from` / `to` (ISO-8601, UTC). `total` is exact up "
        "to `GOVERNANCE_EXACT_COUNT_CAP`, then a planner estimate "
        "(`total_is_estimate`). Each item has tokens, cost, latency bucket and "
        "the slowest stage."
    ),
    response_model=GovernanceTracePage,
    responses=_ERRORS,
)
async def governance_traces_page(
    limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    cursor: str | None = Query(None, max_length=200, description=CURSOR_DOC),
    status: list[TraceStatusFilter] | None = Query(None),
    outcome: list[TraceOutcomeFilter] | None = Query(None),
    latency_bucket: list[LatencyBucketFilter] | None = Query(None),
    q: str | None = Query(None, max_length=MAX_Q),
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
    audience: Audience = Depends(audience_scope),
    runner: ScopedRunner = Depends(get_scoped_runner),
    user: AuthUser = Depends(read_guard),
) -> dict[str, Any]:
    start, end = _date_range(date_from, date_to)
    position = Cursor.decode(cursor)
    filters = traces.TraceFilters(
        audience=audience,
        statuses=_unique(status),
        outcomes=_unique(outcome),
        latency_buckets=_unique(latency_bucket),
        q=_clean_q(q),
        date_from=start,
        date_to=end,
    )
    cap = get_settings().governance_exact_count_cap
    (items, next_cursor), total = await runner.gather(
        lambda s: traces.fetch_page(s, filters, position, limit),
        lambda s: traces.count(s, filters, cap),
    )
    return {
        "items": items,
        "total": total.value,
        "total_estimate": total.estimate,
        "total_is_estimate": total.is_estimate,
        "limit": limit,
        "next_cursor": next_cursor.encode() if next_cursor else None,
        "has_more": next_cursor is not None,
    }


@router.get(
    "/governance/traces/{request_id}",
    summary="Trace detail",
    description=(
        "Stage waterfall (ms offsets), retrieved sources with scores, guardrail "
        "flags and `conversation_id` for one request. 404 when the request is "
        "unknown **or outside the requested audience** (object-level scope)."
    ),
    response_model=GovernanceTraceDetail,
    responses={**_ERRORS, 404: {"description": "Unknown trace for this audience."}},
)
async def governance_trace_detail(
    request_id: str = Path(..., pattern=REQUEST_ID_PATTERN),
    audience: Audience = Depends(audience_scope),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(read_guard),
) -> dict[str, Any]:
    detail = await traces.fetch_detail(db, request_id, audience)
    if detail is None:
        raise HTTPException(404, detail="Trace not found.")
    return detail


# ============================================================================
# LOGS
# ============================================================================

_LOGS_DESCRIPTION = (
    "Audit-log entries, newest first, with server-side filters and keyset "
    "pagination on `(created_at, id)` (`limit` <= 200; pass `next_cursor` back "
    "as `cursor`).\n\n"
    "- Filters: `level` and `service` (repeatable), `q` (LIKE-escaped; matches "
    "the action, the event detail behind the summary, or the request id), "
    "`from` / `to` (ISO-8601, UTC).\n"
    "- `level_counts`: per-level chip counts for the other filters.\n"
    "- `total` is exact up to `GOVERNANCE_EXACT_COUNT_CAP` rows, then the "
    "planner estimate (`total_is_estimate`, `total_estimate`).\n"
    "- `summary`: one-line, human-readable message per action.\n"
    "- `service`: category of the action (`auth`, `query`, `ingest`, "
    "`feedback`, `admin`, `system`); the raw action is in `action`.\n"
    "- `detail`: the event detail as a JSON object.\n"
    "- `message` (**deprecated**): the legacy JSON-string form of `detail`, "
    "truncated to 300 chars; use `summary` / `detail`.\n\n"
    "**Removed:** `trace_id` (it always equalled `request_id`)."
)


def _log_filters(
    audience: str,
    level: list[LogLevel] | None,
    service: list[LogService] | None,
    q: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    include_auth: bool = False,
) -> logs.LogFilters:
    start, end = _date_range(date_from, date_to)
    return logs.LogFilters(
        audience=audience,
        levels=_unique(level),
        services=_unique(service),
        q=_clean_q(q),
        date_from=start,
        date_to=end,
        include_auth=include_auth,
    )


@router.get(
    "/governance/logs",
    summary="Governance audit-log entries",
    description=_LOGS_DESCRIPTION,
    response_model=GovernanceLogPage,
    responses=_ERRORS,
)
async def governance_logs(
    limit: int = Query(MAX_PAGE, ge=1, le=MAX_PAGE),
    cursor: str | None = Query(None, max_length=200, description=CURSOR_DOC),
    level: list[LogLevel] | None = Query(None),
    service: list[LogService] | None = Query(None),
    q: str | None = Query(None, max_length=MAX_Q),
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
    audience: Audience = Depends(audience_scope),
    runner: ScopedRunner = Depends(get_scoped_runner),
    user: AuthUser = Depends(read_guard),
) -> dict[str, Any]:
    filters = _log_filters(
        audience,
        level,
        service,
        q,
        date_from,
        date_to,
        include_auth=user.has(Permission.AUDIT_AUTH_READ),
    )
    position = Cursor.decode(cursor)
    cap = get_settings().governance_exact_count_cap
    (rows, next_cursor), (counts, capped, total) = await runner.gather(
        lambda s: logs.fetch_page(s, filters, position, limit),
        lambda s: logs.level_counts_and_total(s, filters, cap),
    )
    return {
        "items": [logs.to_item(row) for row in rows],
        "total": total.value,
        "total_estimate": total.estimate,
        "total_is_estimate": total.is_estimate,
        "level_counts": counts,
        "level_counts_capped": capped,
        "limit": limit,
        "next_cursor": next_cursor.encode() if next_cursor else None,
        "has_more": next_cursor is not None,
    }


_CSV_COLUMNS = (
    ("Created At", "created_at"),
    ("Level", "level"),
    ("Service", "service"),
    ("Action", "action"),
    ("Summary", "summary"),
    ("User", "user_id"),
    ("IP Address", "ip_address"),
    ("Request ID", "request_id"),
)


def _csv_line(values: list[Any]) -> str:
    out = io.StringIO()
    csv.writer(out).writerow([_sanitize_csv_cell(v) for v in values])
    return out.getvalue()


@router.get(
    "/governance/logs/export.csv",
    summary="Export governance logs as CSV (streamed)",
    description=(
        "Streams every row matching the `/governance/logs` filters (newest "
        "first, at most `GOVERNANCE_EXPORT_MAX_ROWS`) with flat memory. Cells "
        "starting with `=`, `+`, `-`, `@`, tab or CR are prefixed with `'` "
        "(formula-injection safe). The export itself is audited."
    ),
    response_class=StreamingResponse,
    responses={
        **_ERRORS,
        200: {"content": {"text/csv": {}}, "description": "CSV stream."},
    },
)
async def governance_logs_export(
    request: Request,
    level: list[LogLevel] | None = Query(None),
    service: list[LogService] | None = Query(None),
    q: str | None = Query(None, max_length=MAX_Q),
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
    audience: Audience = Depends(audience_scope),
    db: AsyncSession = Depends(get_db),
    runner: ScopedRunner = Depends(get_scoped_runner),
    user: AuthUser = Depends(read_guard),
) -> StreamingResponse:
    filters = _log_filters(
        audience,
        level,
        service,
        q,
        date_from,
        date_to,
        include_auth=user.has(Permission.AUDIT_AUTH_READ),
    )
    settings = get_settings()
    await record_audit_event(
        db,
        request_id=getattr(request.state, "request_id", "admin-export"),
        actor=user.email,
        action="governance_logs_export",
        detail={"filters": filters.as_audit_detail()},
        commit=True,
    )

    async def rows() -> AsyncIterator[str]:
        yield "﻿"
        yield _csv_line([title for title, _ in _CSV_COLUMNS] + ["Detail"])
        async with runner.session() as session:
            async for item in logs.stream_rows(
                session, filters, settings.governance_export_max_rows
            ):
                yield _csv_line(
                    [item[key] for _, key in _CSV_COLUMNS]
                    + [json.dumps(item["detail"], default=str)]
                )

    brand = settings.brand_name.lower().replace(" ", "-")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        rows(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{brand}-governance-logs-{audience}-{stamp}.csv"'
            ),
            "Cache-Control": "no-store",
        },
    )


# ============================================================================
# OVERVIEW + LIVE STREAM
# ============================================================================


@router.get(
    "/governance/overview",
    summary="Governance overview (Overall tab)",
    description=(
        "Health status (`Healthy` / `Degraded` / `Incident`) from configured "
        "thresholds on the recent error rate, p95 latency and Azure quota "
        "utilisation (each signal lists its thresholds); KPI cards (requests, "
        "error rate, p50/p95 latency, active users, groundedness, blocked or "
        "flagged turns, cost today) with 24 hourly sparkline points; recent "
        "CRITICAL / ALERT incidents. Computed in parallel (one DB session per "
        "query, fixed query count) and served with a weak ETag (304 on match)."
    ),
    response_model=GovernanceOverview,
    responses={**_ERRORS, 304: {"description": "Not modified (ETag match)."}},
)
async def governance_overview(
    request: Request,
    audience: Audience = Depends(audience_scope),
    runner: ScopedRunner = Depends(get_scoped_runner),
    user: AuthUser = Depends(read_guard),
) -> Response:
    payload = GovernanceOverview.model_validate(
        await overview_payload(runner, audience)
    )
    return cached_response(request, payload)


@router.get(
    "/governance/stream",
    summary="Live governance updates (SSE)",
    description=(
        "`text/event-stream` with events `snapshot` (full overview, on connect), "
        "`health` (status change), `incident` (new CRITICAL / ALERT row, `id` = "
        "audit id) and `monitoring` (changed KPI values). One shared producer "
        "per worker and audience computes a snapshot every "
        "`GOVERNANCE_STREAM_INTERVAL_SECONDS`, or immediately when an incident "
        "is NOTIFYed on any replica; clients never poll the database."
    ),
    response_class=StreamingResponse,
    responses={
        **_ERRORS,
        200: {"content": {"text/event-stream": {}}, "description": "SSE stream."},
    },
)
async def governance_stream(
    request: Request,
    audience: Audience = Depends(audience_scope),
    hub: GovernanceStreamHub = Depends(get_governance_hub),
    user: AuthUser = Depends(read_guard),
) -> StreamingResponse:
    return event_stream(request, hub.subscribe(audience))
