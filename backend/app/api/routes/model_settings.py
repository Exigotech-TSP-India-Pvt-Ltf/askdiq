"""Settings > Models: runtime LLM / embedding deployments and token pricing.

Superadmin only (``models:write``), audited:
- ``GET /admin/models``: effective values, the .env defaults and what is overridden.
- ``PUT /admin/models``: partial update with optimistic concurrency on ``version``.
  A changed model is called once first (422 if it cannot be used); new prices
  apply to exchanges recorded from then on.
- ``GET /admin/models/prices/official``: the stored official Azure retail
  prices (snapshot; never calls Azure).
- ``POST /admin/models/prices/sync``: fetch them now from the public Azure
  Retail Prices API (no credentials), store them and return them. Saving them
  is a separate ``PUT /admin/models`` with ``price_source=azure_retail``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.azure_insights import jobs as azure_jobs  # noqa: F401  # pylint: disable=unused-import
from app.azure_insights import status as sync_status
from app.azure_insights.retail import RETAIL_KIND, SOURCE, official_prices_view
from app.azure_insights.scheduler import run_source
from app.azure_insights.schemas import OfficialPricesRead
from app.core.concurrency import ScopedRunner, get_scoped_runner
from app.core.config import get_settings
from app.core.database import get_db, get_session_factory
from app.core.exceptions import get_request_id
from app.core.rate_limit import aenforce_retail_sync_rate_limit
from app.core.request_meta import get_client_ip
from app.dashboard.snapshots import snapshot_response
from app.jobs.queue import JobDeferredError, utcnow
from app.model_settings import service
from app.model_settings.verify import verify_models
from app.schemas.model_settings import ModelSettingsRead, ModelSettingsUpdate

router = APIRouter(prefix="/admin/models", tags=["Model Settings"])

_PROBLEM = {"content": {"application/problem+json": {}}}
_AUTH_ERRORS = {
    401: {"description": "Not signed in or missing service key", **_PROBLEM},
    403: {
        "description": "Requires the models:write permission (superadmin)",
        **_PROBLEM,
    },
}

ModelsWriter = Annotated[AuthUser, Depends(require_permission(Permission.MODELS_WRITE))]
Db = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "",
    response_model=ModelSettingsRead,
    summary="Model and pricing settings",
    description=(
        "Effective LLM deployment, embedding deployment and USD rates per 1M "
        "tokens (overrides over the .env values), plus the .env defaults."
    ),
    responses=_AUTH_ERRORS,
)
async def get_model_settings(db: Db, user: ModelsWriter) -> ModelSettingsRead:
    return await service.get_model_settings(db)


@router.put(
    "",
    response_model=ModelSettingsRead,
    summary="Update model and pricing settings",
    description=(
        "Partial update; `null` clears an override (back to the .env value). Send "
        "the current `version` (0 before the first save); a stale version gives "
        "409. A changed model is verified with one live Azure call and the "
        "embedding width must equal EMBEDDING_DIM, otherwise 422. Prices apply "
        "to future exchanges only. Audited."
    ),
    responses={
        **_AUTH_ERRORS,
        409: {"description": "Version conflict (changed by someone else)", **_PROBLEM},
        422: {"description": "Validation failure or unusable model", **_PROBLEM},
    },
)
async def update_model_settings(
    payload: ModelSettingsUpdate,
    request: Request,
    db: Db,
    user: ModelsWriter,
) -> ModelSettingsRead:
    return await service.update_model_settings(
        db,
        payload,
        verify=verify_models,
        actor=user.email,
        request_id=get_request_id(request),
        client_ip=get_client_ip(request),
    )


@router.get(
    "/prices/official",
    response_model=OfficialPricesRead,
    summary="Official Azure retail prices (stored)",
    description=(
        "The latest stored Azure Retail Prices (public list prices) for the "
        "configured LLM and embedding models: USD per 1M tokens (authoritative) "
        "and the pricing currency (for reference), with the effective date and "
        "when they were fetched. A role whose meter is not mapped or not found "
        "has `found=false` (no official price). Precomputed (ETag / 304, "
        "`X-Data-As-Of`); never calls Azure."
    ),
    responses={**_AUTH_ERRORS, 304: {"description": "Not modified."}},
)
async def get_official_prices(
    request: Request,
    user: ModelsWriter,
    runner: ScopedRunner = Depends(get_scoped_runner),
) -> Response:
    return await snapshot_response(request, RETAIL_KIND, {}, runner.gather)


@router.post(
    "/prices/sync",
    response_model=OfficialPricesRead,
    summary="Sync official prices from Azure now",
    description=(
        "Fetches the official list prices from the public Azure Retail Prices "
        "API (no Azure credentials needed, free), stores them with their "
        "history and returns them for preview. Nothing in the model settings "
        "changes: confirm with `PUT /admin/models` and "
        "`price_source=azure_retail`. Rate-limited per admin."
    ),
    responses={
        **_AUTH_ERRORS,
        429: {"description": "Too many syncs; retry later", **_PROBLEM},
        502: {"description": "Azure Retail Prices API failed", **_PROBLEM},
        503: {"description": "Azure throttled the request", **_PROBLEM},
    },
)
async def sync_official_prices(
    db: Db,
    user: ModelsWriter,
    runner: ScopedRunner = Depends(get_scoped_runner),
    session_factory=Depends(get_session_factory),  # noqa: ANN001
) -> OfficialPricesRead:
    settings = get_settings()
    await aenforce_retail_sync_rate_limit(
        user.email, settings.azure_retail_sync_rate_limit_per_minute
    )
    try:
        result = await run_source(
            SOURCE, session_factory=session_factory, gather=runner.gather
        )
    except JobDeferredError as deferred:
        wait = max(1, int((deferred.run_after - utcnow()).total_seconds()))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Azure is throttling price lookups; try again shortly.",
            headers={"Retry-After": str(wait)},
        ) from None
    except Exception:  # pylint: disable=broad-exception-caught
        result = None
    if result is None:
        row = (await sync_status.read_all(db)).get(SOURCE)
        reason = row.last_error_code if row and row.last_error_code else "error"
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Azure Retail Prices API failed ({reason}).",
        )
    return await official_prices_view(db, settings)
