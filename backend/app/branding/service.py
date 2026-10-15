"""Branding service: resolve, cache, update and store brand assets.

``GET /branding`` is served from a short per-process cache (single-flight on
a miss). An update invalidates the local cache and broadcasts
``cache.invalidate/branding`` so every other worker drops its copy too.
If the database is unreachable the config defaults are returned (and not
cached), so the login page always renders.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.branding.assets import AssetError, validate_asset
from app.branding.defaults import SINGLETON_ID, default_brand_values
from app.branding.palette import PaletteError, derive_ramp, palette_for
from app.core.cache import TTLCache
from app.core.concurrency import single_flight
from app.core.config import Settings, get_settings
from app.core.events import TOPIC_CACHE_INVALIDATE, publish_event
from app.core.logging_config import get_logger
from app.governance.audit import record_audit_event
from app.models.db_models import BrandAsset, BrandSettings
from app.schemas.branding import (
    ASSET_FIELDS,
    BrandAssetRead,
    BrandAssetUrls,
    BrandingRead,
    BrandingUpdate,
)

logger = get_logger(__name__)

BRANDING_CACHE_ID = "branding"
ASSET_URL_PREFIX = "/branding/assets/"
_CACHE_KEY = "branding:resolved"
_cache = TTLCache(max_size=4)

AUDIT_UPDATED = "branding_updated"
AUDIT_ASSET_UPLOADED = "branding_asset_uploaded"
DETAIL_CONFLICT = "Branding was changed by someone else. Reload and try again."


def invalidate_branding_cache() -> None:
    _cache.clear()


def asset_url(sha: str | None) -> str | None:
    return f"{ASSET_URL_PREFIX}{sha}" if sha else None


def resolve(values: dict[str, Any], settings: Settings) -> BrandingRead:
    """Merge a row's values over the defaults and add the derived palette."""
    defaults = default_brand_values(settings)
    merged = {
        key: values.get(key) if values.get(key) not in (None, "") else default
        for key, default in defaults.items()
    }
    # Optional fields: an explicit null in the row wins (accent and tint are
    # then derived from the primary ramp).
    for key in (
        *ASSET_FIELDS,
        "accent_hex",
        "tint_hex",
        "login_eyebrow",
        "login_headline",
        "login_subtext",
        "login_description",
        "privacy_url",
        "terms_url",
        "help_url",
    ):
        if key in values:
            merged[key] = values[key]
    ramp = derive_ramp(merged["primary_hex"])
    merged["accent_hex"] = merged["accent_hex"] or ramp["800"]
    merged["tint_hex"] = merged["tint_hex"] or ramp["50"]
    merged["access_contact_email"] = (
        merged["access_contact_email"] or settings.access_contact_email or None
    )
    return BrandingRead(
        brand_name=merged["product_name"],
        brand_assistant_name=merged["assistant_name"],
        brand_support_email=merged["support_email"],
        palette=ramp,
        assets=BrandAssetUrls(
            logo_light=asset_url(merged["logo_light_asset_id"]),
            logo_dark=asset_url(merged["logo_dark_asset_id"]),
            mark_light=asset_url(merged["mark_light_asset_id"]),
            mark_dark=asset_url(merged["mark_dark_asset_id"]),
            favicon=asset_url(merged["favicon_asset_id"]),
        ),
        version=values.get("version") or 1,
        updated_at=values.get("updated_at"),
        **merged,
    )


def _row_values(row: BrandSettings | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        column.key: getattr(row, column.key)
        for column in BrandSettings.__table__.columns
    }


async def _fetch_row(db: AsyncSession) -> BrandSettings | None:
    result = await db.execute(
        select(BrandSettings)
        .where(BrandSettings.id == SINGLETON_ID)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def get_branding(db: AsyncSession) -> BrandingRead:
    """Resolved branding (cached per process; config defaults on DB failure)."""
    settings = get_settings()
    cached = _cache.get(_CACHE_KEY)
    if cached is not None:
        return cached

    async def _load() -> BrandingRead:
        branding = resolve(_row_values(await _fetch_row(db)), settings)
        _cache.set(_CACHE_KEY, branding, ttl=settings.branding_cache_ttl_seconds)
        return branding

    try:
        return await single_flight.run(_CACHE_KEY, _load)
    except Exception as exc:
        logger.warning("branding_load_failed_using_defaults", error=type(exc).__name__)
        return resolve({}, settings)


async def _ensure_row(db: AsyncSession, settings: Settings) -> None:
    """Create the singleton from defaults if the seed row is missing."""
    await db.execute(
        insert(BrandSettings)
        .values(id=SINGLETON_ID, version=1, **default_brand_values(settings))
        .on_conflict_do_nothing(index_elements=[BrandSettings.id])
    )


async def _require_assets_exist(db: AsyncSession, changes: dict[str, Any]) -> None:
    wanted = {changes[f] for f in ASSET_FIELDS if changes.get(f)}
    if not wanted:
        return
    found = set(
        (
            await db.execute(
                select(BrandAsset.sha256).where(BrandAsset.sha256.in_(wanted))
            )
        ).scalars()
    )
    missing = sorted(wanted - found)
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown brand asset id(s): {', '.join(missing)}. Upload them first.",
        )


async def update_branding(
    db: AsyncSession,
    payload: BrandingUpdate,
    *,
    actor: str,
    request_id: str,
    client_ip: str | None,
) -> BrandingRead:
    """Apply ``payload`` if ``payload.version`` is current (409 otherwise)."""
    settings = get_settings()
    changes = payload.changes()
    await _ensure_row(db, settings)
    row = await _fetch_row(db)
    if row is None:  # pragma: no cover - _ensure_row just created it
        raise HTTPException(status.HTTP_409_CONFLICT, detail=DETAIL_CONFLICT)

    primary = changes.get("primary_hex") or row.primary_hex
    try:
        palette_for(primary)
    except PaletteError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Primary colour {primary} fails WCAG AA contrast: {exc}",
        ) from exc
    await _require_assets_exist(db, changes)

    new_version = (
        await db.execute(
            update(BrandSettings)
            .where(
                BrandSettings.id == SINGLETON_ID,
                BrandSettings.version == payload.version,
            )
            .values(
                **changes,
                updated_by=actor,
                updated_at=datetime.now(UTC),
                version=BrandSettings.version + 1,
            )
            .returning(BrandSettings.version)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if new_version is None:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=DETAIL_CONFLICT)

    await record_audit_event(
        db,
        request_id=request_id,
        actor=actor,
        action=AUDIT_UPDATED,
        # Field names and versions only: values may contain contact emails.
        detail={
            "changed_fields": sorted(changes),
            "from_version": payload.version,
            "to_version": new_version,
        },
        ip_address=client_ip,
        commit=False,
    )
    await db.commit()
    invalidate_branding_cache()
    await publish_event(TOPIC_CACHE_INVALIDATE, BRANDING_CACHE_ID)
    return resolve(_row_values(await _fetch_row(db)), settings)


def _asset_read(asset: BrandAsset) -> BrandAssetRead:
    return BrandAssetRead(
        id=asset.sha256,
        mime=asset.mime,
        size_bytes=asset.size_bytes,
        width=asset.width,
        height=asset.height,
        url=f"{ASSET_URL_PREFIX}{asset.sha256}",
        created_at=asset.created_at,
    )


async def store_asset(
    db: AsyncSession,
    data: bytes,
    declared_type: str | None,
    *,
    actor: str,
    request_id: str,
    client_ip: str | None,
) -> BrandAssetRead:
    """Validate, sanitize and store an upload (idempotent by content hash)."""
    settings = get_settings()
    try:
        asset = validate_asset(
            data,
            declared_type,
            max_bytes=settings.branding_asset_max_bytes,
            max_dimension=settings.branding_asset_max_dimension,
        )
    except AssetError as exc:
        raise HTTPException(exc.status_code, detail=str(exc)) from exc

    await db.execute(
        insert(BrandAsset)
        .values(
            sha256=asset.sha256,
            mime=asset.mime.value,
            data=asset.data,
            size_bytes=len(asset.data),
            width=asset.width,
            height=asset.height,
            created_by=actor,
        )
        .on_conflict_do_nothing(index_elements=[BrandAsset.sha256])
    )
    await record_audit_event(
        db,
        request_id=request_id,
        actor=actor,
        action=AUDIT_ASSET_UPLOADED,
        detail={
            "sha256": asset.sha256,
            "mime": asset.mime.value,
            "size_bytes": len(asset.data),
        },
        ip_address=client_ip,
        commit=False,
    )
    await db.commit()
    stored = await get_asset(db, asset.sha256)
    if stored is None:  # pragma: no cover - inserted above
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR)
    return _asset_read(stored)


async def get_asset(db: AsyncSession, sha: str) -> BrandAsset | None:
    return (
        await db.execute(select(BrandAsset).where(BrandAsset.sha256 == sha))
    ).scalar_one_or_none()
