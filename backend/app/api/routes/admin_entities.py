"""Visitors and conversations entity-page stats under ``/admin`` (B7.2).

Contract: ``frontend/docs/contracts/entity-stats.md``. Every route is async,
guarded with ``dashboard:read`` and audience-scoped; ranges are inclusive UTC
days (default: the last 7 days). Stats use ``gather_scoped`` and an ETag;
the per-visitor timeline is not cached. Routers only validate and delegate.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.concurrency import ScopedRunner, get_scoped_runner
from app.core.config import get_settings
from app.core.database import get_db
from app.core.http_cache import cached_response
from app.dashboard.snapshots import Gather, GatherRunner, get_snapshot, register
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
    body = await get_snapshot(VISITOR_STATS_KIND, params, runner.gather)
    return cached_response(request, body)


VISITOR_STATS_KIND = "visitors.stats"


async def _visitor_stats_snapshot(gather: Gather, params: dict) -> dict:
    days = DayRange(
        date.fromisoformat(params["from"]), date.fromisoformat(params["to"])
    )
    payload = await visitors.visitor_stats(
        GatherRunner(gather), params["audience"], days, utcnow()
    )
    return VisitorStats.model_validate(payload).model_dump(mode="json", by_alias=True)


def _visitor_stats_defaults() -> list[dict]:
    """The Visitors page's default view (last 7 UTC days) for each audience."""
    days = resolve_days(
        None, None, today=utcnow().date(), max_days=get_settings().usage_max_range_days
    )
    return [
        {
            "audience": audience,
            "from": days.first.isoformat(),
            "to": days.last.isoformat(),
        }
        for audience in ("overview", "users", "testing")
    ]


register(VISITOR_STATS_KIND, _visitor_stats_snapshot, _visitor_stats_defaults)


@router.get(
    "/visitors/{visitor_id}/timeline",
    response_model=VisitorTimeline,
    summary="One visitor's sessions, queries, daily cost and feedback",
    responses={404: {"description": "Unknown visitor."}, **_ERRORS},
)
async def visitor_timeline(
    visitor_id: str = Path(
        min_length=1,
        max_length=VISITOR_ID_MAX,
        description="`client-<ip>` or a user UUID (the id of `GET /admin/users/{id}`).",
    ),
    days: DayRange = Depends(day_range),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(read_guard),
) -> dict[str, Any]:
    visitor = await visitors.resolve_visitor(db, visitor_id)
    if visitor is None:
        raise HTTPException(status_code=404, detail="Visitor not found")
    return await visitors.visitor_timeline(db, visitor, days)


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
    db: AsyncSession = Depends(get_db),
    runner: ScopedRunner = Depends(get_scoped_runner),
    user: AuthUser = Depends(read_guard),
) -> Response:
    """Same `ip` / `q` meaning as the list. Totals and the histogram cover
    conversations started in the range (with at least one turn); outcomes per
    day count assistant turns in the range (NULL outcome = answered)."""
    payload = await conversations.conversation_stats(runner, db, audience, days, ip, q)
    body = ConversationStats.model_validate(payload).model_dump(
        mode="json", by_alias=True
    )
    return cached_response(request, body)
