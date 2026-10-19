"""Settings > Models: runtime LLM / embedding deployments and token pricing.

Superadmin only (``models:write``), audited:
- ``GET /admin/models``: effective values, the .env defaults and what is overridden.
- ``PUT /admin/models``: partial update with optimistic concurrency on ``version``.
  A changed model is called once first (422 if it cannot be used); new prices
  apply to exchanges recorded from then on.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.database import get_db
from app.core.exceptions import get_request_id
from app.core.request_meta import get_client_ip
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
