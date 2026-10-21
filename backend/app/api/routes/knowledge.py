"""Settings > Knowledge links: the source-link policy (OR-7).

- ``GET /knowledge/settings`` (``dashboard:read``): the host allow-list and
  whether uploads need a source URL (the upload dialog mirrors the checks).
- ``PUT /knowledge/settings`` (``knowledge:links``, superadmin): replace
  them, with optimistic concurrency on ``version``. Audited as
  ``knowledge_settings_updated``.

Tightening the list never deletes stored URLs: documents whose host left
the list show "Link not allowed" and are cited without a link until fixed.
The server never fetches these URLs.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.database import get_db
from app.core.exceptions import get_request_id
from app.core.request_meta import get_client_ip
from app.knowledge import settings_service
from app.schemas.knowledge import KnowledgeSettingsRead, KnowledgeSettingsUpdate

router = APIRouter(prefix="/knowledge", tags=["Knowledge links"])

_PROBLEM = {"content": {"application/problem+json": {}}}
_UNAUTHENTICATED: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in or missing service key", **_PROBLEM},
}

Db = Annotated[AsyncSession, Depends(get_db)]
Reader = Annotated[AuthUser, Depends(require_permission(Permission.DASHBOARD_READ))]
LinksWriter = Annotated[
    AuthUser, Depends(require_permission(Permission.KNOWLEDGE_LINKS))
]


@router.get(
    "/settings",
    response_model=KnowledgeSettingsRead,
    summary="Knowledge link policy",
    description=(
        "The source-URL host allow-list (empty = any https host) and whether a "
        "new upload must carry a source URL. Defaults before the first save: "
        "empty list, URL not required."
    ),
    responses={
        **_UNAUTHENTICATED,
        403: {"description": "Requires dashboard:read", **_PROBLEM},
    },
)
async def get_knowledge_settings(db: Db, user: Reader) -> KnowledgeSettingsRead:
    return await settings_service.get_knowledge_settings(db)


@router.put(
    "/settings",
    response_model=KnowledgeSettingsRead,
    summary="Update the knowledge link policy",
    description=(
        "Replaces the allow-list and the require-URL switch. Hosts are "
        "lower-cased and IDNA-encoded; `*.example.com` (one leading wildcard) "
        "allows every sub-domain. At most 50 hosts of at most 253 characters. "
        "Send the `version` that was read (0 before the first save); a stale "
        "version gives 409. Audited (hosts only)."
    ),
    responses={
        **_UNAUTHENTICATED,
        403: {"description": "Requires knowledge:links (superadmin)", **_PROBLEM},
        409: {"description": "Version conflict (changed by someone else)", **_PROBLEM},
        422: {"description": "Invalid host or too many hosts", **_PROBLEM},
    },
)
async def update_knowledge_settings(
    payload: KnowledgeSettingsUpdate,
    request: Request,
    db: Db,
    user: LinksWriter,
) -> KnowledgeSettingsRead:
    return await settings_service.update_knowledge_settings(
        db,
        payload,
        actor=user.email,
        request_id=get_request_id(request),
        client_ip=get_client_ip(request),
    )
