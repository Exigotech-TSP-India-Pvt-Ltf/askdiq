"""Real-time User Management: auth changes reach open consoles at once.

Every sign-in, access request, decision and user change writes an auth audit
event in its transaction (``app.auth.audit.record_auth_event``), which marks
the session here. When that transaction commits:

1. ``console.changed`` is published with scope ``users`` (LISTEN/NOTIFY, so
   every replica's ``GET /admin/live`` stream sees it): consoles refetch the
   user lists, the access request queue, the auth audit trail and the
   pending-requests badge, with no polling;
2. the precomputed User Management stats (``users.stats`` snapshots) are
   recomputed, which announces ``users.stats`` the same way once stored.

A rolled-back transaction announces nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.cache import dashboard_cache
from app.core.concurrency import ScopedRunner
from app.core.config import get_settings
from app.core.events import TOPIC_CONSOLE_CHANGED, publish_event
from app.core.logging_config import get_logger
from app.dashboard.snapshots import CACHE_PREFIX, compute_and_store, snapshot_key

logger = get_logger(__name__)

SCOPE_USERS = "users"
USERS_STATS_KIND = "users.stats"
# Stats windows kept precomputed (the page asks for 30 days).
DEFAULT_STATS_DAYS = 30
STATS_DEFAULTS: tuple[dict[str, Any], ...] = ({"days": DEFAULT_STATS_DAYS},)

_DIRTY = "auth_live_dirty"
_HOOKED = "auth_live_hooked"
# Stats windows this process has served (recomputed after a change too).
_served_days: set[int] = {DEFAULT_STATS_DAYS}
_tasks: set[asyncio.Task[Any]] = set()


def remember_stats_days(days: int) -> None:
    _served_days.add(days)


def mark_auth_changed(db: AsyncSession) -> None:
    """Announce after ``db``'s current transaction commits (once per commit)."""
    sync = db.sync_session
    sync.info[_DIRTY] = True
    if not sync.info.get(_HOOKED):
        sync.info[_HOOKED] = True
        event.listen(sync, "after_commit", _after_commit)
        event.listen(sync, "after_soft_rollback", _after_rollback)


def _after_commit(session: Session) -> None:
    if not session.info.pop(_DIRTY, False):
        return
    if not get_settings().dashboard_snapshots_persist:
        _drop_memory_stats()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(announce_auth_change())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _after_rollback(session: Session, _previous: Any) -> None:
    session.info.pop(_DIRTY, None)


def _stats_params() -> list[dict[str, Any]]:
    return [{"days": d} for d in sorted(_served_days)]


def _drop_memory_stats() -> None:
    for params in _stats_params():
        dashboard_cache.invalidate_prefix(
            CACHE_PREFIX + snapshot_key(USERS_STATS_KIND, params)
        )


async def refresh_user_stats() -> None:
    """Recompute the stored stats snapshots (in-process cache only: the
    copies were already dropped at commit)."""
    if not get_settings().dashboard_snapshots_persist:
        return
    gather = ScopedRunner().gather
    for params in _stats_params():
        await compute_and_store(USERS_STATS_KIND, params, gather)


async def announce_auth_change() -> None:
    try:
        await publish_event(TOPIC_CONSOLE_CHANGED, SCOPE_USERS)
        await refresh_user_stats()
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("auth_live_announce_failed", exc_info=True)
