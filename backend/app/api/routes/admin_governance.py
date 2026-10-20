"""Governance endpoints under ``/admin/governance``.

Monitoring, traces (legacy array + keyset page + detail), logs (keyset page +
streaming CSV export), the one-call overview and the live SSE stream. All are
``async``, guarded with ``dashboard:read`` and keep the ``audience`` scope.
Routers only validate input and delegate to ``app.governance.*``.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.batch import BATCH_RESPONSES, batch_ids, batch_response, collect
from app.auth.audit import AUTH_AUDIT_ACTIONS
from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.cache import dashboard_cached
from app.core.concurrency import ScopedRunner, get_scoped_runner
from app.core.config import get_settings
from app.core.database import get_db
from app.core.http_cache import encoded_response, weak_etag
from app.core.logging_config import get_logger
from app.core.serialization import sanitize_csv_cell
from app.core.sse import event_stream
from app.dashboard.snapshots import GatherRunner, get_snapshot, register
from app.governance import logs, timeseries, traces
from app.governance.audience import Audience, audience_scope
from app.governance.audit import record_audit_event
from app.governance.live_cache import encode
from app.governance.monitoring import count_traces
from app.governance.monitoring_snapshot import monitoring_cache, with_live_values
from app.governance.overview import overview_cache
from app.governance.pagination import Cursor
from app.governance.stream import GovernanceStreamHub, get_governance_hub
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
_REQUEST_ID_RE = re.compile(REQUEST_ID_PATTERN)
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


# A ``from`` this close to a whole number of hours before now (and no ``to``)
# is a rolling preset ("Last 7 days"): one snapshot per span.
_ROLLING_SLACK = timedelta(minutes=5)
_HOUR = timedelta(hours=1)


def _window_params(
    start: datetime | None, end: datetime | None
) -> dict[str, Any] | None:
    """Snapshot parameters of a first-page date window, or ``None`` when the
    window is not one (an open-ended ``from`` that is not a rolling preset).
    Rolling presets are keyed by their span (recomputed for "now" by the
    refresher); custom ranges by their bounds."""
    if start is None and end is None:
        return {}
    if start is not None and end is None:
        span = timeseries.utcnow().replace(tzinfo=None) - start
        hours = round(span / _HOUR)
        if hours >= 1 and abs(span - hours * _HOUR) <= _ROLLING_SLACK:
            return {"span_hours": hours}
        return None
    if start is not None and end is not None:
        return {"from": start.isoformat(), "to": end.isoformat()}
    return None


def _window_bounds(params: dict) -> tuple[datetime | None, datetime | None]:
    """Inverse of :func:`_window_params` (rolling spans end now)."""
    if params.get("span_hours"):
        now = timeseries.utcnow().replace(tzinfo=None)
        return now - params["span_hours"] * _HOUR, None
    if params.get("from"):
        return datetime.fromisoformat(params["from"]), datetime.fromisoformat(
            params["to"]
        )
    return None, None


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
    "- `traces_total` is the true number of traces for the audience.\n"
    "- `resources` aggregates every live API worker of every replica "
    "(sampled every `METRICS_SAMPLE_INTERVAL_SECONDS`): `host` (the server), "
    "`container` (cgroup limits, null outside a container), `process` (summed "
    "worker RSS / CPU), `event_loop` lag, `db_pool` use, `workers` and a "
    "15-minute `history`. `effective` is the container value when limited, "
    "else the host's. `process_cpu_percent` / `process_ram_percent` mirror "
    "`effective`; `process_ram_mb` is the summed worker RSS."
)


@router.get(
    "/governance/monitoring",
    summary="Governance monitoring snapshot",
    description=_MONITORING_DESCRIPTION,
    responses={**_ERRORS, 304: {"description": "Not modified (ETag match)."}},
)
async def governance_monitoring(
    request: Request,
    audience: Audience = Depends(audience_scope),
    hub: GovernanceStreamHub = Depends(get_governance_hub),
    runner: ScopedRunner = Depends(get_scoped_runner),
    user: AuthUser = Depends(read_guard),
) -> Response:
    # Database part from memory (refreshed in the background every sample
    # interval); live process values and the streamed gauges overlaid.
    entry = await monitoring_cache.get(audience, runner)
    payload = with_live_values(entry.payload, audience, hub.latest_resources)
    body = encode(payload)
    return encoded_response(
        request, body, weak_etag(body), {"X-Data-As-Of": entry.as_of.isoformat()}
    )


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
    window = _window_params(start, end)
    if position is None and filters.q is None and window is not None:
        # First pages (the Overall tab's total, the Traces tab's default and
        # range presets): precomputed, refreshed within seconds of new traces.
        return await get_snapshot(
            GOVERNANCE_TRACES_KIND,
            {**_traces_params(filters, limit), **window},
            runner.gather,
        )
    if filters.q is None and window is not None:
        # Later pages: the total does not depend on the cursor, so it comes
        # from the (precomputed) first page; only the keyset page is read.
        first = await get_snapshot(
            GOVERNANCE_TRACES_KIND,
            {**_traces_params(filters, limit), **window},
            runner.gather,
        )
        ((items, next_cursor),) = await runner.gather(
            lambda s: traces.fetch_page(s, filters, position, limit)
        )
        return _with_page(first, items, next_cursor)
    return await _governance_traces_page(runner, filters, position, limit)


def _with_page(
    first: dict[str, Any], items: list[Any], next_cursor: Cursor | None
) -> dict[str, Any]:
    """A later page: ``first``'s totals / counts with this page's rows."""
    return {
        **first,
        "items": items,
        "next_cursor": next_cursor.encode() if next_cursor else None,
        "has_more": next_cursor is not None,
    }


GOVERNANCE_TRACES_KIND = "governance.traces"


def _traces_params(filters: traces.TraceFilters, limit: int) -> dict[str, Any]:
    return {
        "audience": filters.audience,
        "statuses": list(filters.statuses),
        "outcomes": list(filters.outcomes),
        "latency_buckets": list(filters.latency_buckets),
        "q": filters.q,
        "limit": limit,
    }


async def _governance_traces_snapshot(gather: Any, params: dict) -> dict[str, Any]:
    start, end = _window_bounds(params)
    filters = traces.TraceFilters(
        audience=params["audience"],
        statuses=tuple(params["statuses"]),
        outcomes=tuple(params["outcomes"]),
        latency_buckets=tuple(params["latency_buckets"]),
        q=params["q"],
        date_from=start,
        date_to=end,
    )
    return await _governance_traces_page(
        GatherRunner(gather),  # type: ignore[arg-type]
        filters,
        None,
        params["limit"],
    )


async def _governance_traces_page(
    runner: ScopedRunner,
    filters: traces.TraceFilters,
    position: Cursor | None,
    limit: int,
) -> dict[str, Any]:
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
    "/governance/traces/batch",
    summary="Trace details of several requests",
    description=(
        "`items[request_id]` is exactly `GET /admin/governance/traces/{request_id}` "
        "for each id inside the requested audience; unknown ids and ids outside "
        "the audience are listed in `missing` (object-level scope). The Traces "
        "table warms the drawer of its visible rows with this one request."
    ),
    responses=BATCH_RESPONSES,
)
async def governance_traces_batch(
    request: Request,
    ids: list[str] = Depends(batch_ids),
    audience: Audience = Depends(audience_scope),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(read_guard),
) -> Response:
    valid = [i for i in ids if _REQUEST_ID_RE.fullmatch(i)]

    async def load(request_id: str) -> dict[str, Any] | None:
        if request_id not in valid:
            return None
        return await traces.fetch_detail(db, request_id, audience)

    return batch_response(request, await collect(ids, load))


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
    user: AuthUser,
    audience: str,
    level: list[LogLevel] | None,
    service: list[LogService] | None,
    q: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> logs.LogFilters:
    start, end = _date_range(date_from, date_to)
    return logs.LogFilters(
        audience=audience,
        levels=_unique(level),
        services=_unique(service),
        q=_clean_q(q),
        date_from=start,
        date_to=end,
        # Auth events are superadmin-only (`audit:auth:read`).
        hidden_actions=frozenset()
        if user.has(Permission.AUDIT_AUTH_READ)
        else AUTH_AUDIT_ACTIONS,
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
    filters = _log_filters(user, audience, level, service, q, date_from, date_to)
    position = Cursor.decode(cursor)
    cap = get_settings().governance_exact_count_cap
    # The filters carry the caller's hidden actions (permission scope), so
    # the key never shares a page across permission levels.
    window = _window_params(filters.date_from, filters.date_to)
    if position is None and window is not None:
        # First pages (the Overall tab's totals, the Logs tab's default and
        # range presets): precomputed and refreshed in the background.
        return await get_snapshot(
            GOVERNANCE_LOGS_KIND,
            {**_logs_params(filters, limit), **window},
            runner.gather,
        )
    scope = replace(filters, hidden_actions=frozenset())
    hidden = ",".join(sorted(filters.hidden_actions))
    key = f"gov-logs:{scope!r}:{hidden}:{cursor}:{limit}"
    if window is not None:
        # Later pages: chip counts and total do not depend on the cursor, so
        # they come from the (precomputed) first page; only the keyset page
        # is read.
        async def later_page() -> dict[str, Any]:
            first = await get_snapshot(
                GOVERNANCE_LOGS_KIND,
                {**_logs_params(filters, limit), **window},
                runner.gather,
            )
            ((rows, next_cursor),) = await runner.gather(
                lambda s: logs.fetch_page(s, filters, position, limit)
            )
            return _with_page(first, [logs.to_item(row) for row in rows], next_cursor)

        return await dashboard_cached(key, later_page)
    return await dashboard_cached(
        key, lambda: _governance_logs_page(runner, filters, position, limit, cap)
    )


GOVERNANCE_LOGS_KIND = "governance.logs"


def _logs_params(filters: logs.LogFilters, limit: int) -> dict[str, Any]:
    return {
        "audience": filters.audience,
        "levels": list(filters.levels),
        "services": list(filters.services),
        "q": filters.q,
        "hidden": sorted(filters.hidden_actions),
        "limit": limit,
    }


async def _governance_logs_snapshot(gather: Any, params: dict) -> dict[str, Any]:
    start, end = _window_bounds(params)
    filters = logs.LogFilters(
        audience=params["audience"],
        levels=tuple(params["levels"]),
        services=tuple(params["services"]),
        q=params["q"],
        date_from=start,
        date_to=end,
        hidden_actions=frozenset(params["hidden"]),
    )
    cap = get_settings().governance_exact_count_cap
    return await _governance_logs_page(
        GatherRunner(gather),  # type: ignore[arg-type]
        filters,
        None,
        params["limit"],
        cap,
    )


# The Overall tab's totals (limit 1) and the Logs / Traces tabs' first page.
_FIRST_PAGE_LIMITS = (1, 10)

# No range (Overall totals) plus the range picker's presets (24 h .. 90 d).
_FIRST_PAGE_WINDOWS: tuple[dict[str, Any], ...] = (
    {},
    *({"span_hours": hours} for hours in (24, 7 * 24, 30 * 24, 90 * 24)),
)

register(
    GOVERNANCE_LOGS_KIND,
    _governance_logs_snapshot,
    [
        {
            **_logs_params(logs.LogFilters(audience=aud, hidden_actions=hidden), limit),
            **window,
        }
        for aud in ("overview", "users", "testing")
        for hidden in (frozenset(), AUTH_AUDIT_ACTIONS)
        for limit in _FIRST_PAGE_LIMITS
        for window in (_FIRST_PAGE_WINDOWS if limit != 1 else ({},))
    ],
)
register(
    GOVERNANCE_TRACES_KIND,
    _governance_traces_snapshot,
    [
        {**_traces_params(traces.TraceFilters(audience=aud), limit), **window}
        for aud in ("overview", "users", "testing")
        for limit in _FIRST_PAGE_LIMITS
        for window in (_FIRST_PAGE_WINDOWS if limit != 1 else ({},))
    ],
)


async def _governance_logs_page(
    runner: ScopedRunner,
    filters: logs.LogFilters,
    position: Cursor | None,
    limit: int,
    cap: int,
) -> dict[str, Any]:
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
    csv.writer(out).writerow([sanitize_csv_cell(v) for v in values])
    return out.getvalue()


@router.get(
    "/governance/logs/export.csv",
    summary="Export governance logs as CSV (streamed)",
    description=(
        "Streams every row matching the `/governance/logs` filters (newest "
        "first, at most `GOVERNANCE_EXPORT_MAX_ROWS`) with flat memory. Cells "
        "starting with `=`, `+`, `-`, `@`, tab or CR are prefixed with `'` "
        "(formula-injection safe). The export itself is audited. Needs "
        "`usage:export` (bulk data export, admin and up)."
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
    user: AuthUser = Depends(require_permission(Permission.USAGE_EXPORT)),
) -> StreamingResponse:
    filters = _log_filters(user, audience, level, service, q, date_from, date_to)
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
    # Served from the memory copy the stream producer keeps fresh: encoded
    # once per change, so a request (or a 304) costs a lookup.
    entry = await overview_cache.get(audience, runner)
    return encoded_response(
        request, entry.body, entry.etag, {"X-Data-As-Of": entry.as_of.isoformat()}
    )


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
