"""Visitors and conversations entity-page stats under ``/admin`` (B7.2).

Contract: ``docs/technical/frontend/contracts/entity-stats.md``. Every route is async,
guarded with ``dashboard:read`` and audience-scoped; ranges are inclusive UTC
days (default: the last 7 days). Stats use ``gather_scoped`` and an ETag;
the stats are precomputed snapshots (every preset range and audience kept
warm), the per-visitor timeline is precomputed once viewed. Routers only
validate and delegate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.cache import dashboard_cache
from app.core.concurrency import ScopedRunner, get_scoped_runner
from app.core.config import get_settings
from app.core.database import get_db
from app.core.http_cache import cached_response
from app.dashboard.range_params import preset_day_ranges
from app.dashboard.snapshots import (
    CACHE_PREFIX,
    Gather,
    GatherRunner,
    register,
    snapshot_key,
    snapshot_response,
)
from app.dashboard.window import DayRange, resolve_days, utcnow
from app.entities import conversations, visitors
from app.governance.audience import Audience, audience_scope
from app.schemas.entity_stats import ConversationStats, VisitorStats, VisitorTimeline

router = APIRouter(tags=["admin"])

MAX_Q = 200
MAX_IP = 64
VISITOR_ID_MAX = 128

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not authenticated."},
    403: {"description": "Missing `dashboard:read`."},
    422: {"description": "Invalid audience, date range or filter."},
}
read_guard = require_permission(Permission.DASHBOARD_READ)


def day_range(
    first: date | None = Query(
        None, alias="from", description="First day, YYYY-MM-DD (UTC), inclusive."
    ),
    last: date | None = Query(
        None, alias="to", description="Last day, YYYY-MM-DD (UTC), inclusive."
    ),
) -> DayRange:
    """Inclusive UTC day range; default the last 7 days ending today."""
    return resolve_days(
        first,
        last,
        today=utcnow().date(),
        max_days=get_settings().usage_max_range_days,
    )


@router.get(
    "/visitors/stats",
    summary="Visitor KPIs, new vs returning per day and the weekday x hour heatmap",
    responses={
        200: {"model": VisitorStats},
        304: {"description": "ETag matched."},
        **_ERRORS,
    },
)
async def visitor_stats(
    request: Request,
    audience: Audience = Depends(audience_scope),
    days: DayRange = Depends(day_range),
    runner: ScopedRunner = Depends(get_scoped_runner),
    user: AuthUser = Depends(read_guard),
) -> Response:
    """A visitor is one client IP (as `GET /admin/users`). `active_now` is the
    last 30 minutes, independent of the range."""
    params = {
        "audience": audience,
        "from": days.first.isoformat(),
        "to": days.last.isoformat(),
    }
    return await snapshot_response(request, VISITOR_STATS_KIND, params, runner.gather)


VISITOR_STATS_KIND = "visitors.stats"


async def _visitor_stats_snapshot(gather: Gather, params: dict) -> dict:
    days = DayRange(
        date.fromisoformat(params["from"]), date.fromisoformat(params["to"])
    )
    payload = await visitors.visitor_stats(
        GatherRunner(gather), params["audience"], days, utcnow()
    )
    return VisitorStats.model_validate(payload).model_dump(mode="json", by_alias=True)


AUDIENCES = ("overview", "users", "testing")


def _visitor_stats_defaults() -> list[dict]:
    """Every range preset of the Visitors page for each audience (a preset or
    audience switch reads a stored row)."""
    return [
        {
            "audience": audience,
            "from": days.first.isoformat(),
            "to": days.last.isoformat(),
        }
        for days in preset_day_ranges()
        if days is not None
        for audience in AUDIENCES
    ]


register(VISITOR_STATS_KIND, _visitor_stats_snapshot, _visitor_stats_defaults)


@router.get(
    "/visitors/{visitor_id}/timeline",
    response_model=VisitorTimeline,
    summary="One visitor's sessions, queries, daily cost and feedback",
    responses={404: {"description": "Unknown visitor."}, **_ERRORS},
)
async def visitor_timeline(
    request: Request,
    visitor_id: str = Path(
        min_length=1,
        max_length=VISITOR_ID_MAX,
        description="`client-<ip>` or a user UUID (the id of `GET /admin/users/{id}`).",
    ),
    days: DayRange = Depends(day_range),
    db: AsyncSession = Depends(get_db),
    runner: ScopedRunner = Depends(get_scoped_runner),
    user: AuthUser = Depends(read_guard),
) -> Response:
    """Precomputed per visitor and range once viewed (``dashboard_snapshots``,
    refreshed within seconds of new activity while watched)."""
    params = {
        "id": visitor_id,
        "from": days.first.isoformat(),
        "to": days.last.isoformat(),
    }
    return await snapshot_response(
        request,
        VISITOR_TIMELINE_KIND,
        params,
        runner.gather,
        lambda _gather: _visitor_timeline(db, visitor_id, days),
    )


VISITOR_TIMELINE_KIND = "visitors.timeline"


async def _visitor_timeline(
    db: AsyncSession, visitor_id: str, days: DayRange
) -> dict[str, Any]:
    visitor = await visitors.resolve_visitor(db, visitor_id)
    if visitor is None:
        raise HTTPException(status_code=404, detail="Visitor not found")
    payload = await visitors.visitor_timeline(db, visitor, days)
    return VisitorTimeline.model_validate(payload).model_dump(
        mode="json", by_alias=True
    )


async def _visitor_timeline_snapshot(gather: Gather, params: dict) -> dict:
    days = DayRange(
        date.fromisoformat(params["from"]), date.fromisoformat(params["to"])
    )
    (payload,) = await gather(lambda db: _visitor_timeline(db, params["id"], days))
    return payload


register(VISITOR_TIMELINE_KIND, _visitor_timeline_snapshot)


@router.get(
    "/conversations/stats",
    summary="Conversation totals, turns histogram and outcomes per day",
    responses={
        200: {"model": ConversationStats},
        304: {"description": "ETag matched."},
        **_ERRORS,
    },
)
async def conversation_stats(
    request: Request,
    audience: Audience = Depends(audience_scope),
    days: DayRange = Depends(day_range),
    ip: str | None = Query(None, max_length=MAX_IP),
    q: str | None = Query(None, max_length=MAX_Q),
    runner: ScopedRunner = Depends(get_scoped_runner),
    user: AuthUser = Depends(read_guard),
) -> Response:
    """Same `ip` / `q` meaning as the list. Totals and the histogram cover
    conversations started in the range (with at least one turn); outcomes per
    day count assistant turns in the range (NULL outcome = answered).

    Precomputed (``dashboard_snapshots``): every preset range and audience is
    kept warm; other filters are computed once, then refreshed in the
    background. A text search (`q`) is cached in memory only (typing must
    not leave stored snapshots behind). ``X-Data-As-Of`` is the payload's age."""
    params = _conversation_stats_params(audience, days, ip, q)
    if params["q"] is not None:
        payload = await memory_snapshot(
            CONVERSATION_STATS_KIND,
            params,
            lambda: _conversation_stats_snapshot(runner.gather, params),
        )
        return cached_response(request, payload, max_age=0)
    return await snapshot_response(
        request, CONVERSATION_STATS_KIND, params, runner.gather
    )


CONVERSATION_STATS_KIND = "conversations.stats"
# In-memory (``dashboard_cache``) key prefix of ad-hoc payloads (text searches).
# Under the snapshot prefix, so dropping snapshots (test IPs) clears them too.
MEMORY_PREFIX = f"{CACHE_PREFIX}adhoc:"


async def memory_snapshot(
    kind: str, params: dict[str, Any], build: Callable[[], Awaitable[Any]]
) -> Any:
    """``build()`` cached stale-while-revalidate in this process only, for
    parameter sets too many to store (each search term): never persisted, so
    the refresher never keeps them warm."""
    cfg = get_settings()
    if cfg.dashboard_cache_fresh_seconds <= 0:
        return await build()
    return await dashboard_cache.get_or_compute(
        MEMORY_PREFIX + snapshot_key(kind, params),
        build,
        fresh_seconds=cfg.dashboard_cache_fresh_seconds,
        stale_seconds=cfg.dashboard_cache_stale_seconds,
    )


def _conversation_stats_params(
    audience: str, days: DayRange, ip: str | None, q: str | None
) -> dict[str, Any]:
    return {
        "audience": audience,
        "from": days.first.isoformat(),
        "to": days.last.isoformat(),
        "ip": ip or None,
        "q": q or None,
    }


async def _conversation_stats_snapshot(gather: Gather, params: dict) -> dict:
    days = DayRange(
        date.fromisoformat(params["from"]), date.fromisoformat(params["to"])
    )
    (payload,) = await gather(
        lambda db: conversations.conversation_stats(
            GatherRunner(gather),
            db,
            params["audience"],
            days,
            params["ip"],
            params["q"],
        )
    )
    return ConversationStats.model_validate(payload).model_dump(
        mode="json", by_alias=True
    )


def _conversation_stats_defaults() -> list[dict]:
    """The Conversations tab's unfiltered view for every preset and audience."""
    return [
        _conversation_stats_params(audience, days, None, None)
        for days in preset_day_ranges()
        if days is not None
        for audience in AUDIENCES
    ]


register(
    CONVERSATION_STATS_KIND,
    _conversation_stats_snapshot,
    _conversation_stats_defaults,
)
