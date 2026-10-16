"""White-label branding routes (newplan section 14, planchanges4 B3.3).

Public (no auth, cache-friendly):
- ``GET /branding``: resolved values, derived palette and asset URLs.
- ``GET /branding/assets/{sha}``: immutable, content-addressed brand images.

Superadmin only (``branding:write``), audited:
- ``GET /admin/branding/defaults``: the seeded values, for "Reset to defaults".
- ``PUT /admin/branding``: optimistic concurrency on ``version`` (409).
- ``POST /admin/branding/assets``: PNG, WebP or sanitized SVG, max 512 KB.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.branding import service
from app.branding.assets import AssetMime
from app.branding.defaults import default_brand_values
from app.core.concurrency import SessionFactory
from app.core.config import get_settings
from app.core.database import get_db, get_session_factory
from app.core.exceptions import get_request_id
from app.core.http_cache import cached_response
from app.core.request_meta import get_client_ip
from app.schemas.branding import (
    ASSET_ID_PATTERN,
    BrandAssetRead,
    BrandingDefaults,
    BrandingRead,
    BrandingUpdate,
)

router = APIRouter(prefix="/branding", tags=["Branding"])
admin_router = APIRouter(prefix="/admin/branding", tags=["Branding"])

ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'"

_PROBLEM = {"content": {"application/problem+json": {}}}
_AUTH_ERRORS = {
    401: {"description": "Not signed in or missing service key", **_PROBLEM},
    403: {
        "description": "Requires the branding:write permission (superadmin)",
        **_PROBLEM,
    },
}

BrandingWriter = Annotated[
    AuthUser, Depends(require_permission(Permission.BRANDING_WRITE))
]
Db = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "",
    response_model=BrandingRead,
    summary="Public branding",
    description=(
        "Resolved white-label settings (database row over config defaults), the "
        "derived 50-950 palette and asset URLs. Public; supports `If-None-Match` "
        "(304) and is cacheable for 5 minutes."
    ),
    responses={304: {"description": "Not modified (ETag matched)"}},
)
async def get_branding(
    request: Request,
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
) -> Response:
    branding = await service.get_branding(session_factory)
    return cached_response(
        request,
        branding.model_dump(mode="json"),
        max_age=get_settings().branding_http_max_age_seconds,
        public=True,
    )


@router.get(
    "/assets/{sha}",
    summary="Brand asset",
    description="Content-addressed brand image, cached immutably.",
    response_class=Response,
    responses={
        200: {
            "description": "The image bytes",
            "content": {mime.value: {} for mime in AssetMime},
        },
        304: {"description": "Not modified (ETag matched)"},
        404: {"description": "Unknown asset", **_PROBLEM},
        422: {"description": "Malformed asset id", **_PROBLEM},
    },
)
async def get_brand_asset(
    request: Request,
    db: Db,
    sha: Annotated[str, Path(pattern=ASSET_ID_PATTERN, description="sha256 (hex)")],
) -> Response:
    etag = f'"{sha}"'
    headers = {
        "ETag": etag,
        "Cache-Control": ASSET_CACHE_CONTROL,
        "X-Content-Type-Options": "nosniff",
    }
    asset = await service.get_asset(db, sha)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Brand asset not found.")
    if asset.mime == AssetMime.SVG:
        headers["Content-Security-Policy"] = SVG_CSP
    if request.headers.get("if-none-match", "").strip() in {etag, f"W/{etag}"}:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=asset.data, media_type=asset.mime, headers=headers)


@admin_router.get(
    "/defaults",
    response_model=BrandingDefaults,
    summary="Seeded branding defaults",
    description=(
        "The values of the seeded branding row (built-in colours and login copy, "
        "names and support email from the `BRAND_*` settings). The dashboard's "
        "Reset to defaults sends these back with PUT /admin/branding."
    ),
    responses=_AUTH_ERRORS,
)
async def get_branding_defaults(user: BrandingWriter) -> BrandingDefaults:
    return BrandingDefaults(**default_brand_values(get_settings()))


@admin_router.put(
    "",
    response_model=BrandingRead,
    summary="Update branding",
    description=(
        "Partial update of the branding row. Send the current `version`; a stale "
        "version gives 409. The primary colour must pass WCAG AA (brand-600 on "
        "white and brand-400 on the dark background at 4.5:1), otherwise 422 "
        "explains why. URLs must be https:// or relative. Audited."
    ),
    responses={
        **_AUTH_ERRORS,
        409: {"description": "Version conflict (changed by someone else)", **_PROBLEM},
        422: {"description": "Validation or contrast failure", **_PROBLEM},
    },
)
async def update_branding(
    payload: BrandingUpdate,
    request: Request,
    db: Db,
    user: BrandingWriter,
) -> BrandingRead:
    return await service.update_branding(
        db,
        payload,
        actor=user.email,
        request_id=get_request_id(request),
        client_ip=get_client_ip(request),
    )


@admin_router.post(
    "/assets",
    response_model=BrandAssetRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a brand asset",
    description=(
        "PNG, WebP or SVG up to 512 KB, identified by its bytes (not the file "
        "name). SVG is sanitized (no scripts, event handlers or external "
        "references). Returns the content-addressed id to use in PUT /admin/branding."
    ),
    responses={
        **_AUTH_ERRORS,
        413: {"description": "File larger than the limit", **_PROBLEM},
        415: {"description": "Not a PNG, WebP or SVG (or type mismatch)", **_PROBLEM},
        422: {
            "description": "Invalid image (dimensions, empty, malformed)",
            **_PROBLEM,
        },
    },
)
async def upload_brand_asset(
    request: Request,
    db: Db,
    user: BrandingWriter,
    file: Annotated[UploadFile, File(description="PNG, WebP or SVG image")],
) -> BrandAssetRead:
    # Read one byte past the cap so an oversized file is detected without
    # buffering more of it.
    data = await file.read(get_settings().branding_asset_max_bytes + 1)
    return await service.store_asset(
        db,
        data,
        file.content_type,
        actor=user.email,
        request_id=get_request_id(request),
        client_ip=get_client_ip(request),
    )
