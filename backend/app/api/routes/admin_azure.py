"""Azure Insights (free Foundry / Azure data) for the admin console.

Every read is served from stored snapshots (``dashboard_snapshots``) built
from Postgres; a page view never calls Azure. Background jobs poll Azure and
announce fresh data on the console live stream (``/admin/live``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.azure_insights import jobs as azure_jobs  # noqa: F401  # pylint: disable=unused-import
from app.azure_insights import reconciliation
from app.azure_insights.metrics import GRAIN_MINUTE, SERIES_KIND, SUMMARY_KIND
from app.azure_insights.scheduler import STATUS_KIND
from app.azure_insights.schemas import (
    AzureMetricSeriesRead,
    AzureMetricsSummaryRead,
    AzureStatusRead,
    MetricName,
    MetricsGrain,
    MetricsRange,
)
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


_SNAPSHOT_NOTE = (
    "Precomputed from Postgres (ETag / 304, `X-Data-As-Of`), recomputed after "
    "every metrics poll and announced on `/admin/live`; never calls Azure."
)


@router.get(
    "/metrics/summary",
    response_model=AzureMetricsSummaryRead,
    summary="Azure vs AskDIQ reconciliation",
    description=(
        "Azure Monitor platform metrics of the Foundry / Azure OpenAI account "
        "against what AskDIQ recorded, per deployment for the last 24 h and per "
        "UTC day of the last `days` days: requests, tokens in / out, HTTP 429s "
        "and latency, with drift % (`|Azure - AskDIQ| / Azure`). Rows at or "
        "above AZURE_RECONCILIATION_DRIFT_PCT are flagged `drifted`; "
        "deployments AskDIQ does not use have role `other`. `tokens` lists "
        "Azure-reported tokens per deployment with the share not seen by "
        "AskDIQ (other callers, playground, retries). `connected=false` when "
        "AZURE_INSIGHTS_ENABLED is off. " + _SNAPSHOT_NOTE
    ),
    responses={**READ_ERRORS, 422: {"description": "Invalid `days`."}},
)
async def get_azure_metrics_summary(
    request: Request,
    days: int = Query(
        reconciliation.DEFAULT_DAYS,
        ge=1,
        le=reconciliation.MAX_DAYS,
        description="UTC days of per-day reconciliation (PT1H history: 93 days).",
    ),
    user: AuthUser = Depends(read_guard),
    runner: ScopedRunner = Depends(get_scoped_runner),
) -> Response:
    del user
    return await snapshot_response(
        request, SUMMARY_KIND, reconciliation.summary_params(days), runner.gather
    )


@router.get(
    "/metrics/series",
    response_model=AzureMetricSeriesRead,
    summary="Azure Monitor metric series",
    description=(
        "Stored points of one platform metric per deployment (and per HTTP "
        "status for `AzureOpenAIRequests`). `grain=PT1H` merges complete "
        "hourly points with the minute points of recent hours; `grain=PT1M` "
        "is served for `range` 1h or 6h (minute points are kept 7 days). "
        "`deployment=*` returns every deployment. " + _SNAPSHOT_NOTE
    ),
    responses={**READ_ERRORS, 422: {"description": "Invalid metric, range or grain."}},
)
async def get_azure_metrics_series(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    request: Request,
    metric: MetricName = Query(
        "TokenTransaction", description="Azure Monitor metric name."
    ),
    deployment: str = Query(
        reconciliation.ALL,
        min_length=1,
        max_length=128,
        pattern=r"^(\*|[A-Za-z0-9][A-Za-z0-9._-]*)$",
        description="Azure deployment name, or `*` for all.",
    ),
    range_: MetricsRange = Query("24h", alias="range"),
    grain: MetricsGrain = Query("PT1H"),
    user: AuthUser = Depends(read_guard),
    runner: ScopedRunner = Depends(get_scoped_runner),
) -> Response:
    del user
    if grain == GRAIN_MINUTE and range_ not in reconciliation.MINUTE_RANGES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`grain=PT1M` is served for `range` 1h or 6h; use PT1H.",
        )
    params = reconciliation.series_params(metric, deployment, range_, grain)
    return await snapshot_response(request, SERIES_KIND, params, runner.gather)
