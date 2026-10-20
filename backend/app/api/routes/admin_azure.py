"""Azure Insights (free Foundry / Azure data) for the admin console.

Every read is served from stored snapshots (``dashboard_snapshots``) built
from Postgres; a page view never calls Azure. Background jobs poll Azure and
announce fresh data on the console live stream (``/admin/live``).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.azure_insights import jobs as azure_jobs  # noqa: F401  # pylint: disable=unused-import
from app.azure_insights.billed import range_params
from app.azure_insights.cost_schemas import AzureCostsRead, CostsPreset
from app.azure_insights.costs import COSTS_KIND
from app.azure_insights.scheduler import STATUS_KIND
from app.azure_insights.schemas import AzureStatusRead
from app.core.concurrency import ScopedRunner, get_scoped_runner
from app.core.config import get_settings
from app.dashboard.snapshots import snapshot_response
from app.jobs.queue import utcnow

router = APIRouter(prefix="/admin/azure", tags=["Azure Insights"])

_PROBLEM = {"content": {"application/problem+json": {}}}
READ_ERRORS: dict[int | str, dict[str, Any]] = {
    304: {"description": "Not modified (`If-None-Match`)."},
    401: {"description": "Not authenticated.", **_PROBLEM},
    403: {"description": "Needs the dashboard:read permission.", **_PROBLEM},
}
read_guard = require_permission(Permission.DASHBOARD_READ)


@router.get(
    "/status",
    response_model=AzureStatusRead,
    summary="Azure Insights connection status",
    description=(
        "Per source (retail prices, metrics, costs, capacity, health): last "
        "run, last success, last error (sanitised), next scheduled run, and "
        "metrics calls used this month against the hard cap. "
        "`connected=false` with `reason=not_configured` when "
        "AZURE_INSIGHTS_ENABLED is off. Precomputed (ETag / 304, "
        "`X-Data-As-Of`); never calls Azure."
    ),
    responses=READ_ERRORS,
)
async def get_azure_status(
    request: Request,
    user: AuthUser = Depends(read_guard),
    runner: ScopedRunner = Depends(get_scoped_runner),
) -> Response:
    del user
    return await snapshot_response(request, STATUS_KIND, {}, runner.gather)


_COSTS_ERRORS: dict[int | str, dict[str, Any]] = {
    **READ_ERRORS,
    422: {"description": "Invalid range (reversed, in the future or too long)."},
}


def _costs_params(
    preset: CostsPreset | None, from_date: date | None, to_date: date | None
) -> dict[str, Any]:
    today = utcnow().date()
    if preset is None and (from_date is None) != (to_date is None):
        raise HTTPException(422, "Send both from_date and to_date, or neither.")
    if from_date is not None and to_date is not None:
        if from_date > to_date:
            raise HTTPException(422, "from_date must be on or before to_date.")
        if to_date > today:
            to_date = today
            from_date = min(from_date, today)
        max_days = get_settings().usage_max_range_days
        if to_date - from_date > timedelta(days=max_days):
            raise HTTPException(
                422, f"The range exceeds the maximum of {max_days} days."
            )
    return range_params(preset, from_date, to_date, today)


@router.get(
    "/costs",
    response_model=AzureCostsRead,
    summary="Azure billed vs estimated cost",
    description=(
        "Daily Azure billed cost (Cost Management, ActualCost, billing "
        "currency) next to AskDIQ's own estimate (answers + ingestion + "
        "classifier), the variance per day and month to date, and a status "
        "per day: `final` (month closed + 3 days), `estimated` (can still be "
        "rerated) or `pending` (Azure lags 8-72 h). A non-USD bill is "
        "compared with the estimate re-priced from Azure retail prices in "
        "that currency; without them both are shown and no variance is "
        "computed (`currency_mismatch`). `state` is `not_connected` when "
        "AZURE_INSIGHTS_ENABLED is off and `unsupported` when the "
        "subscription type cannot use Cost Management. Range: a `range` "
        "preset or `from_date`/`to_date` (inclusive UTC days; default the "
        "last 30 days); the presets are precomputed after every poll. "
        "ETag / 304, `X-Data-As-Of`; never calls Azure."
    ),
    responses=_COSTS_ERRORS,
)
async def get_azure_costs(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    request: Request,
    preset: CostsPreset | None = Query(
        None, alias="range", description="Preset (overrides from/to)."
    ),
    from_date: date | None = Query(None, description="First UTC day (YYYY-MM-DD)."),
    to_date: date | None = Query(None, description="Last UTC day (YYYY-MM-DD)."),
    user: AuthUser = Depends(read_guard),
    runner: ScopedRunner = Depends(get_scoped_runner),
) -> Response:
    del user
    params = _costs_params(preset, from_date, to_date)
    return await snapshot_response(request, COSTS_KIND, params, runner.gather)
