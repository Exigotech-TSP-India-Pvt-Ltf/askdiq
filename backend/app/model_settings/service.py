"""Runtime model and pricing overrides (Settings > Models).

The effective values (DB row over the .env defaults) are held per process so
the hot path (every LLM / embedding call and every cost calculation) reads
them synchronously with :func:`current_model_settings`. They are loaded at startup, reloaded
after a save, on the cross-worker ``cache.invalidate/model_settings`` event,
and in the background once ``MODEL_SETTINGS_REFRESH_SECONDS`` have passed.
If the database is unreachable the last good values (or the .env defaults)
stay in use.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.azure_insights.prices import official_prices
from app.core.concurrency import SessionFactory
from app.core.config import Settings, get_settings
from app.core.events import TOPIC_CACHE_INVALIDATE, publish_event
from app.core.logging_config import get_logger
from app.core.tasks import spawn
from app.governance.audit import record_audit_event
from app.models.db_models import ModelSettings
from app.schemas.model_settings import (
    OVERRIDE_FIELDS,
    PRICE_FIELDS,
    ModelConfigValues,
    ModelSettingsRead,
    ModelSettingsUpdate,
)

logger = get_logger(__name__)

MODEL_SETTINGS_CACHE_ID = "model_settings"
SINGLETON_ID = 1
AUDIT_UPDATED = "model_settings_updated"
DETAIL_CONFLICT = "Model settings were changed by someone else. Reload and try again."

# (llm_deployment | None, embedding_model | None) -> raises HTTPException if unusable.
Verifier = Callable[[str | None, str | None], Awaitable[None]]


class _State:
    """The effective values for this process and when they were loaded."""

    value: ModelSettingsRead | None = None
    loaded_at: float = 0.0
    refreshing: bool = False


_state = _State()


def default_values(settings: Settings) -> ModelConfigValues:
    return ModelConfigValues(
        llm_deployment=settings.azure_deployment_name,
        embedding_model=settings.embedding_model,
        price_prompt_per_million=settings.price_prompt_per_million,
        price_cached_per_million=settings.price_cached_per_million,
        price_output_per_million=settings.price_output_per_million,
        price_embed_per_million=settings.price_embed_per_million,
    )


def resolve(row: ModelSettings | None, settings: Settings) -> ModelSettingsRead:
    """Merge a row's non-null overrides over the .env defaults."""
    defaults = default_values(settings)
    overrides = {
        field: getattr(row, field)
        for field in OVERRIDE_FIELDS
        if row is not None and getattr(row, field) is not None
    }
    return ModelSettingsRead(
        **{**defaults.model_dump(), **overrides},
        embedding_dim=settings.embedding_dim,
        defaults=defaults,
        overridden=[field for field in OVERRIDE_FIELDS if field in overrides],
        version=row.version if row is not None else 0,
        updated_at=row.updated_at if row is not None else None,
        updated_by=row.updated_by if row is not None else None,
        price_source=row.price_source if row is not None else None,
        prices_synced_at=row.prices_synced_at if row is not None else None,
        prices_effective_date=row.prices_effective_date if row is not None else None,
    )


def _store(value: ModelSettingsRead) -> ModelSettingsRead:
    _state.value = value
    _state.loaded_at = time.monotonic()
    return value


def current_model_settings() -> ModelSettingsRead:
    """Effective values for this process (never blocks, never raises)."""
    settings = get_settings()
    value = _state.value
    if value is None:
        # .env defaults until the row is loaded (loaded_at stays 0: load now).
        value = _state.value = resolve(None, settings)
    if time.monotonic() - _state.loaded_at >= settings.model_settings_refresh_seconds:
        _schedule_refresh()
    return value


def invalidate_model_settings_cache() -> None:
    """Reload on the next :func:`current_model_settings` (another worker saved a change)."""
    _state.loaded_at = 0.0


def _schedule_refresh() -> None:
    if _state.refreshing:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # sync caller outside the event loop: keep the cached values
    from app.core.database import get_session_factory  # noqa: PLC0415 - avoids an import cycle

    _state.refreshing = True
    spawn(refresh_model_settings(get_session_factory()), name="model-settings-refresh")


async def _fetch_row(db: AsyncSession) -> ModelSettings | None:
    result = await db.execute(
        select(ModelSettings)
        .where(ModelSettings.id == SINGLETON_ID)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def refresh_model_settings(session_factory: SessionFactory) -> ModelSettingsRead:
    """Load the row; on failure keep (and return) the current values."""
    settings = get_settings()
    try:
        async with (
            asyncio.timeout(settings.model_settings_load_timeout_seconds),
            session_factory() as db,
        ):
            row = await _fetch_row(db)
        return _store(resolve(row, settings))
    except Exception as exc:
        logger.warning("model_settings_load_failed", error=type(exc).__name__)
        _state.loaded_at = time.monotonic()  # back off until the next interval
        return _state.value or resolve(None, settings)
    finally:
        _state.refreshing = False


async def get_model_settings(db: AsyncSession) -> ModelSettingsRead:
    """Fresh read for the Settings page (also updates this process's values)."""
    return _store(resolve(await _fetch_row(db), get_settings()))


def _changed_models(
    changes: dict[str, Any], before: ModelSettingsRead
) -> tuple[str | None, str | None]:
    """The model names that would change (None = unchanged), after defaults."""

    def effective(field: str) -> str | None:
        if field not in changes:
            return None
        value = changes[field] or getattr(before.defaults, field)
        return value if value != getattr(before, field) else None

    return effective("llm_deployment"), effective("embedding_model")


DETAIL_NOT_OFFICIAL = (
    "{field} does not match the official Azure retail price. Sync the official "
    "prices again or save it as a manual price."
)


async def _price_provenance(
    db: AsyncSession,
    payload: ModelSettingsUpdate,
    changes: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    """Where changed prices come from. ``azure_retail`` is accepted only when
    every changed price equals the stored official USD price (422 otherwise);
    any other price change is ``manual``. No price change: unchanged."""
    changed = [field for field in PRICE_FIELDS if field in changes]
    if not changed:
        return {}
    if payload.price_source != "azure_retail":
        return {
            "price_source": "manual",
            "prices_synced_at": None,
            "prices_effective_date": None,
        }
    official = {
        price.field: price
        for price in await official_prices(db, settings, datetime.now(UTC).date())
    }
    for field in changed:
        price = official.get(field)
        value = changes[field]
        if (
            price is None
            or price.usd_per_million is None
            or value is None
            or not math.isclose(
                value, price.usd_per_million, rel_tol=1e-9, abs_tol=1e-9
            )
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=DETAIL_NOT_OFFICIAL.format(field=field),
            )
    used = [official[field] for field in changed]
    return {
        "price_source": "azure_retail",
        "prices_synced_at": max(p.fetched_at for p in used if p.fetched_at),
        "prices_effective_date": max(
            p.effective_date for p in used if p.effective_date
        ),
    }


async def update_model_settings(
    db: AsyncSession,
    payload: ModelSettingsUpdate,
    *,
    verify: Verifier,
    actor: str,
    request_id: str,
    client_ip: str | None,
) -> ModelSettingsRead:
    """Apply ``payload`` if ``payload.version`` is current (409 otherwise).

    New model names are verified with a live call before anything is written.
    """
    settings = get_settings()
    changes = payload.changes()
    before = resolve(await _fetch_row(db), settings)
    if payload.version != before.version:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=DETAIL_CONFLICT)

    llm, embedding = _changed_models(changes, before)
    if llm or embedding:
        await verify(llm, embedding)
    provenance = await _price_provenance(db, payload, changes, settings)

    await db.execute(
        insert(ModelSettings)
        .values(id=SINGLETON_ID, version=0)
        .on_conflict_do_nothing(index_elements=[ModelSettings.id])
    )
    new_version = (
        await db.execute(
            update(ModelSettings)
            .where(
                ModelSettings.id == SINGLETON_ID,
                ModelSettings.version == payload.version,
            )
            .values(
                **changes,
                **provenance,
                updated_by=actor,
                updated_at=datetime.now(UTC),
                version=ModelSettings.version + 1,
            )
            .returning(ModelSettings.version)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if new_version is None:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=DETAIL_CONFLICT)

    after = resolve(await _fetch_row(db), settings)
    await record_audit_event(
        db,
        request_id=request_id,
        actor=actor,
        action=AUDIT_UPDATED,
        detail={
            "changes": {
                field: {"from": getattr(before, field), "to": getattr(after, field)}
                for field in changes
            },
            "price_source": provenance.get("price_source"),
            "from_version": payload.version,
            "to_version": new_version,
        },
        ip_address=client_ip,
        commit=False,
    )
    await db.commit()
    _store(after)
    await publish_event(TOPIC_CACHE_INVALIDATE, MODEL_SETTINGS_CACHE_ID)
    return after
