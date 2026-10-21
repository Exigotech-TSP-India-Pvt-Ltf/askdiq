"""Settings > Knowledge links: the singleton link policy.

The effective policy is held per process so the hot path (citations on every
``/query``, the documents list) reads it synchronously with
:func:`current_knowledge_settings`. It is loaded at startup, reloaded after a
save, on the cross-worker ``cache.invalidate/knowledge_settings`` event and
in the background once ``MODEL_SETTINGS_REFRESH_SECONDS`` have passed (the
same cadence as Settings > Models). If the database is unreachable the last
good values (or the defaults) stay in use.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import status
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.concurrency import SessionFactory
from app.core.config import get_settings
from app.core.errors import ApiHTTPException, ErrorCode
from app.core.events import TOPIC_CACHE_INVALIDATE, publish_event
from app.core.logging_config import get_logger
from app.core.tasks import spawn
from app.governance.audit import record_audit_event
from app.models.db_models import KnowledgeSettings
from app.schemas.knowledge import KnowledgeSettingsRead, KnowledgeSettingsUpdate

logger = get_logger(__name__)

KNOWLEDGE_SETTINGS_CACHE_ID = "knowledge_settings"
SINGLETON_ID = 1
AUDIT_UPDATED = "knowledge_settings_updated"
DETAIL_CONFLICT = (
    "Knowledge link settings were changed by someone else. Reload and try again."
)


@dataclass
class _State:
    value: KnowledgeSettingsRead | None = None
    loaded_at: float = 0.0
    refreshing: bool = False


_state = _State()


def resolve(row: KnowledgeSettings | None) -> KnowledgeSettingsRead:
    """The row, or the shipped defaults (any https host, URL optional)."""
    if row is None:
        return KnowledgeSettingsRead()
    hosts = row.allowed_hosts if isinstance(row.allowed_hosts, list) else []
    return KnowledgeSettingsRead(
        allowed_hosts=[str(h) for h in hosts],
        require_source_url=bool(row.require_source_url),
        version=row.version,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


def _store(value: KnowledgeSettingsRead) -> KnowledgeSettingsRead:
    _state.value = value
    _state.loaded_at = time.monotonic()
    return value


def current_knowledge_settings() -> KnowledgeSettingsRead:
    """Effective policy for this process (never blocks, never raises)."""
    value = _state.value
    if value is None:
        value = _state.value = resolve(None)
    refresh_after = get_settings().model_settings_refresh_seconds
    if time.monotonic() - _state.loaded_at >= refresh_after:
        _schedule_refresh()
    return value


def invalidate_knowledge_settings_cache() -> None:
    """Reload on the next read (another worker saved a change)."""
    _state.loaded_at = 0.0


def _schedule_refresh() -> None:
    if _state.refreshing:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    # pylint: disable-next=import-outside-toplevel
    from app.core.database import get_session_factory  # noqa: PLC0415 - import cycle

    _state.refreshing = True
    spawn(
        refresh_knowledge_settings(get_session_factory()),
        name="knowledge-settings-refresh",
    )


async def _fetch_row(db: AsyncSession) -> KnowledgeSettings | None:
    result = await db.execute(
        select(KnowledgeSettings)
        .where(KnowledgeSettings.id == SINGLETON_ID)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def refresh_knowledge_settings(
    session_factory: SessionFactory,
) -> KnowledgeSettingsRead:
    """Load the row; on failure keep (and return) the current values."""
    settings = get_settings()
    try:
        async with (
            asyncio.timeout(settings.model_settings_load_timeout_seconds),
            session_factory() as db,
        ):
            row = await _fetch_row(db)
        return _store(resolve(row))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("knowledge_settings_load_failed", error=type(exc).__name__)
        _state.loaded_at = time.monotonic()
        return _state.value or resolve(None)
    finally:
        _state.refreshing = False


async def get_knowledge_settings(db: AsyncSession) -> KnowledgeSettingsRead:
    """Fresh read (also updates this process's values)."""
    return _store(resolve(await _fetch_row(db)))


async def update_knowledge_settings(
    db: AsyncSession,
    payload: KnowledgeSettingsUpdate,
    *,
    actor: str,
    request_id: str,
    client_ip: str | None,
) -> KnowledgeSettingsRead:
    """Replace the policy if ``payload.version`` is current (409 otherwise)."""
    before = resolve(await _fetch_row(db))
    if payload.version != before.version:
        raise ApiHTTPException(
            status.HTTP_409_CONFLICT, detail=DETAIL_CONFLICT, code=ErrorCode.CONFLICT
        )
    await db.execute(
        insert(KnowledgeSettings)
        .values(id=SINGLETON_ID, version=0, allowed_hosts=[])
        .on_conflict_do_nothing(index_elements=[KnowledgeSettings.id])
    )
    new_version = (
        await db.execute(
            update(KnowledgeSettings)
            .where(
                KnowledgeSettings.id == SINGLETON_ID,
                KnowledgeSettings.version == payload.version,
            )
            .values(
                allowed_hosts=payload.allowed_hosts,
                require_source_url=payload.require_source_url,
                updated_by=actor,
                updated_at=datetime.now(UTC),
                version=KnowledgeSettings.version + 1,
            )
            .returning(KnowledgeSettings.version)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if new_version is None:
        await db.rollback()
        raise ApiHTTPException(
            status.HTTP_409_CONFLICT, detail=DETAIL_CONFLICT, code=ErrorCode.CONFLICT
        )
    added = [h for h in payload.allowed_hosts if h not in before.allowed_hosts]
    removed = [h for h in before.allowed_hosts if h not in payload.allowed_hosts]
    await record_audit_event(
        db,
        request_id=request_id,
        actor=actor,
        action=AUDIT_UPDATED,
        detail={
            "hosts_added": added,
            "hosts_removed": removed,
            "host_count": len(payload.allowed_hosts),
            "require_source_url": {
                "from": before.require_source_url,
                "to": payload.require_source_url,
            },
            "from_version": payload.version,
            "to_version": new_version,
        },
        ip_address=client_ip,
        commit=False,
    )
    await db.commit()
    after = _store(resolve(await _fetch_row(db)))
    await publish_event(TOPIC_CACHE_INVALIDATE, KNOWLEDGE_SETTINGS_CACHE_ID)
    return after
