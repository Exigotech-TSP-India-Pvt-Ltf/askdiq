"""Azure Insights (free Foundry / Azure data) for the admin console.

Every read is served from stored snapshots (``dashboard_snapshots``) built
from Postgres; a page view never calls Azure. Background jobs poll Azure and
announce fresh data on the console live stream (``/admin/live``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.azure_insights import jobs as azure_jobs  # noqa: F401  # pylint: disable=unused-import
from app.azure_insights.scheduler import STATUS_KIND
from app.azure_insights.schemas import AzureStatusRead
from app.core.concurrency import ScopedRunner, get_scoped_runner
from app.dashboard.snapshots import snapshot_response

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
