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
from app.azure_insights.advisor import ADVISOR_KIND
from app.azure_insights.capacity import CAPACITY_KIND
from app.azure_insights.capacity_schemas import (
    AzureAdvisorRead,
    AzureCapacityRead,
    AzureHealthRead,
)
from app.azure_insights.health import HEALTH_KIND
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


@router.get(
    "/capacity",
    response_model=AzureCapacityRead,
    summary="Azure deployments, real TPM/RPM limits and regional quota",
    description=(
        "Latest capacity poll (every AZURE_CAPACITY_INTERVAL_SECONDS): per "
        "deployment SKU, capacity, model and the Azure-reported TPM / RPM "
        "(`rateLimits`, normalised by `renewalPeriod`); the regional quota "
        "`currentValue / limit` (`regional.state = forbidden` without the "
        "optional Cognitive Services Usages Reader role); recent capacity "
        "changes. Precomputed (ETag / 304, `X-Data-As-Of`); never calls Azure."
    ),
    responses=READ_ERRORS,
)
async def get_azure_capacity(
    request: Request,
    user: AuthUser = Depends(read_guard),
    runner: ScopedRunner = Depends(get_scoped_runner),
) -> Response:
    del user
    return await snapshot_response(request, CAPACITY_KIND, {}, runner.gather)


@router.get(
    "/health",
    response_model=AzureHealthRead,
    summary="Azure resource and service health",
    description=(
        "Resource Health of the Azure OpenAI account (current status plus "
        "recent status changes), recent Service Health events for OpenAI / "
        "Foundry / Cognitive Services in our region or global (when "
        "AZURE_SERVICE_HEALTH_ENABLED), and the console banner state "
        "(`banner.show` only for Degraded / Unavailable or an active "
        "incident). Precomputed (ETag / 304, `X-Data-As-Of`); never calls Azure."
    ),
    responses=READ_ERRORS,
)
async def get_azure_health(
    request: Request,
    user: AuthUser = Depends(read_guard),
    runner: ScopedRunner = Depends(get_scoped_runner),
) -> Response:
    del user
    return await snapshot_response(request, HEALTH_KIND, {}, runner.gather)


@router.get(
    "/advisor",
    response_model=AzureAdvisorRead,
    summary="Azure Advisor recommendations",
    description=(
        "Open Advisor recommendations for the resource group (daily poll when "
        "AZURE_ADVISOR_ENABLED), model / version retirements first. "
        "Precomputed (ETag / 304, `X-Data-As-Of`); never calls Azure."
    ),
    responses=READ_ERRORS,
)
async def get_azure_advisor(
    request: Request,
    user: AuthUser = Depends(read_guard),
    runner: ScopedRunner = Depends(get_scoped_runner),
) -> Response:
    del user
    return await snapshot_response(request, ADVISOR_KIND, {}, runner.gather)
