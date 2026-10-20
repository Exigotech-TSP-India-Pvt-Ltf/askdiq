"""Precomputed dashboard aggregates (``dashboard_snapshots``).

A dashboard request must never wait on an aggregation over the history.
Each heavy aggregate is registered here as a *kind* with a builder; its
payload is stored per parameter set in ``dashboard_snapshots`` and kept fresh
in the background by :class:`SnapshotRefresher` (one process at a time,
Postgres advisory lock), which also computes the registered defaults on its
first run (backfill) so the first visit after a deploy or restart reads a
row too.

Read path (:func:`get_snapshot`):

1. the in-process stale-while-revalidate cache (``dashboard_cache``);
2. else the stored row, returned at once whatever its age (a newer one is
   computed in the background when it is due);
3. only a parameter set never seen before is computed while the caller
   waits, then stored, and from then on refreshed in the background.

Payloads are JSON (``jsonable_encoder``): what the route returns is exactly
what was stored. ``computed_at`` is the payload's as-of time.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import database
from app.core.cache import dashboard_cache
from app.core.concurrency import ScopedRunner, single_flight
from app.core.config import get_settings
from app.core.events import (
    TOPIC_CONSOLE_CHANGED,
    TOPIC_SNAPSHOT_UPDATED,
    Event,
    publish_event,
)
from app.core.http_cache import encoded_response, weak_etag
from app.core.logging_config import get_logger
from app.models.db_models import DashboardSnapshot

logger = get_logger(__name__)

# ``gather``-style runner: runs each task on its own session, results in order.
Gather = Callable[..., Awaitable[list[Any]]]
Builder = Callable[[Gather, dict[str, Any]], Awaitable[Any]]

# pg_try_advisory_lock key of the refresher ("dashsnap").
REFRESH_LOCK_KEY = 0x64617368736E6170
# Snapshots touched this recently are "being watched": refreshed every cycle
# the data changed; older ones only every DASHBOARD_SNAPSHOT_IDLE_REFRESH_SECONDS.
ACTIVE_WINDOW = timedelta(minutes=30)
# Snapshots used within this window (and the defaults) also catch up with the
# clock every idle interval, even when no data changed.
IDLE_WINDOW = timedelta(hours=6)
# A read moves ``requested_at`` forward at most this often per key.
TOUCH_INTERVAL = timedelta(minutes=5)
# Upper bound on rows recomputed per refresher cycle (bounded DB load).
MAX_REFRESH_PER_CYCLE = 50
# Prefix of snapshot payloads in the in-process ``dashboard_cache``.
CACHE_PREFIX = "snapshot:"

_DATA_STAMP_SQL = text(
    """
    SELECT concat_ws('|',
        (SELECT max(created_at) FROM chat_messages),
        (SELECT max(classified_at) FROM turn_insights),
        (SELECT max(updated_at) FROM message_feedback),
        (SELECT max(created_at) FROM latency_metrics),
        (SELECT max(created_at) FROM evaluation_records),
        (SELECT max(updated_at) FROM insight_taxonomy),
        (SELECT count(*) FROM test_ip_configs),
        (SELECT concat(count(*), '@', max(updated_at)) FROM documents))
    """
)
# Alerts raised, re-seen, acknowledged or resolved (the notification bell).
_ALERTS_STAMP_SQL = text(
    """
    SELECT concat_ws('|', count(*), max(last_seen_at), max(acknowledged_at),
                     max(resolved_at))
    FROM alerts
    """
)
# Scopes of TOPIC_CONSOLE_CHANGED besides snapshot kinds.
SCOPE_DATA = "data"
SCOPE_ALERTS = "alerts"
# Encoded bodies kept per snapshot key (``snapshot_response``).
_ENCODED_MAX = 512


@dataclass(frozen=True, slots=True)
class Kind:
    builder: Builder
    # Parameter sets kept precomputed; a callable is evaluated each cycle (for
    # defaults that move with the date, e.g. "the last 7 days").
    defaults_spec: tuple[dict[str, Any], ...] | Callable[[], Iterable[dict[str, Any]]]

    @property
    def defaults(self) -> tuple[dict[str, Any], ...]:
        spec = self.defaults_spec
        return tuple(spec()) if callable(spec) else spec


_KINDS: dict[str, Kind] = {}


def register(
    kind: str,
    builder: Builder,
    defaults: Iterable[dict[str, Any]] | Callable[[], Iterable[dict[str, Any]]] = (),
) -> None:
    """Register ``kind``; ``defaults`` are precomputed on the refresher's first run."""
    _KINDS[kind] = Kind(builder, defaults if callable(defaults) else tuple(defaults))


# Incremental upkeep run at the start of every refresher cycle, under its
# lock (e.g. expanding new citation sources): ``fn(session)``.
_MAINTENANCE: dict[str, Callable[[AsyncSession], Awaitable[Any]]] = {}


def register_maintenance(
    name: str, fn: Callable[[AsyncSession], Awaitable[Any]]
) -> None:
    _MAINTENANCE[name] = fn


def registered_kinds() -> dict[str, Kind]:
    return dict(_KINDS)


def canonical(params: dict[str, Any]) -> str:
    return json.dumps(jsonable_encoder(params), sort_keys=True, separators=(",", ":"))


def snapshot_key(kind: str, params: dict[str, Any]) -> str:
    return hashlib.sha256(f"{kind}|{canonical(params)}".encode()).hexdigest()


def _encode(value: Any) -> Any:
    """JSON form of a payload; response models keep their field names (the
    route's response_model re-validates and applies the aliases)."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return jsonable_encoder(value)


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


async def _read_row(session: AsyncSession, key: str) -> DashboardSnapshot | None:
    return await session.get(DashboardSnapshot, key)


async def _write_row(session: AsyncSession, values: dict[str, Any]) -> None:
    """Upsert one snapshot (``values``: key, kind, params, payload, compute_ms)."""
    now = _now()
    stmt = insert(DashboardSnapshot).values(**values, computed_at=now, requested_at=now)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DashboardSnapshot.key],
        set_={
            "payload": stmt.excluded.payload,
            "computed_at": stmt.excluded.computed_at,
            "compute_ms": stmt.excluded.compute_ms,
        },
    )
    await session.execute(stmt)
    await session.commit()


async def _touch(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("UPDATE dashboard_snapshots SET requested_at = now() WHERE key = :key"),
        {"key": key},
    )
    await session.commit()


# Bound computation of one snapshot: ``build(gather)`` returns the payload.
Build = Callable[[Gather], Awaitable[Any]]


def _registered_build(kind: str, params: dict[str, Any]) -> Build:
    builder = _KINDS[kind].builder
    return lambda gather: builder(gather, params)


async def compute_and_store(
    kind: str,
    params: dict[str, Any],
    gather: Gather,
    key: str | None = None,
    build: Build | None = None,
) -> Any:
    """Build the payload, store it, and update this process's memory cache."""
    key = key or snapshot_key(kind, params)
    build = build or _registered_build(kind, params)
    started = time.perf_counter()
    payload = _encode(await build(gather))
    elapsed = int((time.perf_counter() - started) * 1000)
    values = {
        "key": key,
        "kind": kind,
        "params": jsonable_encoder(params),
        "payload": payload,
        "compute_ms": elapsed,
    }
    await gather(lambda s: _write_row(s, values))
    dashboard_cache.put(CACHE_PREFIX + key, payload)
    _as_of[key] = _now()
    # Every process reloads its memory copy; the console refetches the kind.
    _spawn(_announce(kind, key), "announce")
    return payload


async def _announce(kind: str, key: str) -> None:
    await publish_event(TOPIC_SNAPSHOT_UPDATED, f"{kind}|{key}")
    await publish_event(TOPIC_CONSOLE_CHANGED, kind)


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------

_background: set[asyncio.Task[Any]] = set()
# As-of time of the payload in memory, per key (the ``X-Data-As-Of`` header).
_as_of: dict[str, datetime] = {}
# key -> (payload object, encoded body, ETag): encoded once per payload.
_encoded: dict[str, tuple[Any, bytes, str]] = {}


def _spawn(coro: Awaitable[Any], what: str) -> None:
    async def _run() -> None:
        try:
            await coro
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("snapshot_background_failed", what=what, exc_info=True)

    task = asyncio.create_task(_run())
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _load(
    kind: str, params: dict[str, Any], key: str, gather: Gather, build: Build
) -> Any:
    (row,) = await gather(lambda s: _read_row(s, key))
    if row is None:
        # Never seen: the only case where a caller waits on the aggregation.
        return await single_flight.run(
            f"snapshot-build:{key}",
            lambda: compute_and_store(kind, params, gather, key, build),
        )
    payload = row.payload
    _as_of[key] = row.computed_at
    now = _now()
    cfg = get_settings()
    # Registered kinds are kept fresh by the refresher; ad-hoc ones (and any
    # the refresher fell behind on) refresh here, after answering.
    max_age = (
        cfg.dashboard_snapshot_max_age_seconds
        if kind in _KINDS
        else cfg.dashboard_snapshot_refresh_seconds or cfg.dashboard_cache_fresh_seconds
    )
    if now - row.computed_at > timedelta(seconds=max_age):
        _spawn(
            single_flight.run(
                f"snapshot-build:{key}",
                lambda: compute_and_store(
                    kind, params, ScopedRunner().gather, key, build
                ),
            ),
            kind,
        )
    if now - row.requested_at > TOUCH_INTERVAL:
        _spawn(gather(lambda s: _touch(s, key)), "touch")
    return payload


async def get_snapshot(
    kind: str,
    params: dict[str, Any],
    gather: Gather,
    build: Build | None = None,
) -> Any:
    """The stored payload of ``kind`` for ``params`` (see the module docstring).

    ``build(gather)`` computes it; by default the builder registered for
    ``kind`` (registered kinds are also refreshed and backfilled by the
    refresher, ad-hoc ones only on read).
    """
    # Builders always see the JSON form (as the refresher reads it back).
    params = jsonable_encoder(params)
    build = build or _registered_build(kind, params)
    cfg = get_settings()
    if cfg.dashboard_cache_fresh_seconds <= 0:
        return _encode(await build(gather))
    key = snapshot_key(kind, params)
    if not cfg.dashboard_snapshots_persist:
        # In-process cache only (no table reads / writes).
        async def compute() -> Any:
            payload = _encode(await build(gather))
            _as_of[key] = _now()
            return payload

        return await dashboard_cache.get_or_compute(
            CACHE_PREFIX + key,
            compute,
            fresh_seconds=cfg.dashboard_cache_fresh_seconds,
            stale_seconds=cfg.dashboard_cache_stale_seconds,
        )
    return await dashboard_cache.get_or_compute(
        CACHE_PREFIX + key,
        lambda: _load(kind, params, key, gather, build),
        fresh_seconds=cfg.dashboard_cache_fresh_seconds,
        stale_seconds=cfg.dashboard_cache_stale_seconds,
    )


def _encoded_body(key: str, payload: Any) -> tuple[bytes, str]:
    hit = _encoded.get(key)
    if hit is not None and hit[0] is payload:
        return hit[1], hit[2]
    body = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    etag = weak_etag(body)
    if key not in _encoded and len(_encoded) >= _ENCODED_MAX:
        _encoded.pop(next(iter(_encoded)))
    _encoded[key] = (payload, body, etag)
    return body, etag


async def snapshot_response(
    request: Request,
    kind: str,
    params: dict[str, Any],
    gather: Gather,
    build: Build | None = None,
) -> Response:
    """``get_snapshot`` as a JSON response with an ETag, encoded once per
    payload (a matching revalidation is a 304 without any encoding) and
    ``X-Data-As-Of`` = when the payload was computed."""
    params = jsonable_encoder(params)
    payload = await get_snapshot(kind, params, gather, build)
    key = snapshot_key(kind, params)
    body, etag = _encoded_body(key, payload)
    as_of = _as_of.get(key)
    extra = {"X-Data-As-Of": as_of.isoformat()} if as_of is not None else None
    return encoded_response(request, body, etag, extra)


async def _reload(key: str) -> None:
    async with database.AsyncSessionLocal() as session:
        row = await _read_row(session, key)
    if row is not None:
        dashboard_cache.put(CACHE_PREFIX + key, row.payload)
        _as_of[key] = row.computed_at


def on_snapshot_updated(event: Event, own_instance: str | None = None) -> None:
    """Event-bus callback: another process stored a newer payload; reload it
    into this process's memory (only keys this process has served)."""
    if own_instance is not None and event.origin == own_instance:
        return
    _, _, key = event.id.partition("|")
    if key and dashboard_cache.has(CACHE_PREFIX + key):
        _spawn(_reload(key), "snapshot_reload")


async def delete_snapshots(session: AsyncSession, kinds_prefix: str) -> None:
    """Delete stored snapshots of ``kinds_prefix`` inside the caller's transaction."""
    await session.execute(
        delete(DashboardSnapshot).where(DashboardSnapshot.kind.startswith(kinds_prefix))
    )
    dashboard_cache.invalidate_prefix(CACHE_PREFIX)


async def drop_snapshots(kinds_prefix: str = "") -> None:
    """Delete stored snapshots whose inputs changed (test IPs, taxonomy).

    They are rebuilt on their next read and by the refresher's backfill.
    """
    dashboard_cache.invalidate_prefix(CACHE_PREFIX)
    if not get_settings().dashboard_snapshots_persist:
        return
    async with database.AsyncSessionLocal() as session:
        await session.execute(
            delete(DashboardSnapshot).where(
                DashboardSnapshot.kind.startswith(kinds_prefix)
            )
        )
        await session.commit()


def schedule_drop_snapshots(kinds_prefix: str = "") -> None:
    """Fire-and-forget :func:`drop_snapshots` (no-op outside an event loop)."""
    dashboard_cache.invalidate_prefix(CACHE_PREFIX)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    _spawn(drop_snapshots(kinds_prefix), "drop_snapshots")


# ---------------------------------------------------------------------------
# Background refresher
# ---------------------------------------------------------------------------


class SnapshotRefresher:
    """Keeps requested snapshots fresh; one instance at a time across workers."""

    def __init__(self, gather: Gather | None = None) -> None:
        self.gather = gather or ScopedRunner().gather
        self._last_stamp: str | None = None
        self._watched: dict[str, str] = {}
        self.cycles = 0

    async def run(self, stop: asyncio.Event) -> None:
        """Refresh every REFRESH s; between cycles check the data stamp every
        WATCH s and, on a change, announce it and refresh the watched
        snapshots (at most once per MIN_CHANGE_REFRESH s)."""
        cfg = get_settings()
        interval = cfg.dashboard_snapshot_refresh_seconds
        watch = cfg.dashboard_watch_seconds
        min_gap = cfg.dashboard_min_change_refresh_seconds
        loop = asyncio.get_running_loop()
        next_cycle = loop.time()
        last_change_cycle = -min_gap
        pending_change = False
        while not stop.is_set():
            if watch > 0:
                try:
                    pending_change = await self.watch_once() or pending_change
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.warning("snapshot_watch_failed", exc_info=True)
            now = loop.time()
            if pending_change and now - last_change_cycle >= min_gap:
                next_cycle = now
            if now >= next_cycle:
                if pending_change:
                    last_change_cycle = now
                    pending_change = False
                try:
                    await self.refresh_once()
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.warning("snapshot_refresh_failed", exc_info=True)
                next_cycle = loop.time() + interval
            tick = min(watch, interval) if watch > 0 else interval
            timeout = max(0.05, min(tick, next_cycle - loop.time()))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=timeout)

    async def watch_once(self) -> bool:
        """Compare the data / alerts stamps with the last check and announce a
        change on the console live stream. True when the data changed (the
        first check only records the stamps)."""

        async def stamps(session: AsyncSession) -> dict[str, str]:
            return {
                SCOPE_DATA: str(await session.scalar(_DATA_STAMP_SQL)),
                SCOPE_ALERTS: str(await session.scalar(_ALERTS_STAMP_SQL)),
            }

        (current,) = await self.gather(stamps)
        previous, self._watched = self._watched, current
        if not previous:
            return False
        for scope, stamp in current.items():
            if previous.get(scope) != stamp:
                await publish_event(TOPIC_CONSOLE_CHANGED, scope)
        return previous.get(SCOPE_DATA) != current[SCOPE_DATA]

    async def refresh_once(self) -> int:
        """One cycle under the advisory lock; returns the snapshots recomputed."""
        async with database.engine.connect() as raw:
            conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
            got = bool(
                await conn.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": REFRESH_LOCK_KEY}
                )
            )
            if not got:
                return 0
            try:
                return await self._cycle()
            finally:
                await conn.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": REFRESH_LOCK_KEY}
                )

    async def _maintain(self) -> None:
        for name, fn in _MAINTENANCE.items():
            try:
                await self.gather(fn)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("snapshot_maintenance_failed", task=name, exc_info=True)

    async def _cycle(self) -> int:
        cfg = get_settings()
        await self._maintain()
        built = await self._backfill_defaults()
        (stamp,) = await self.gather(lambda s: s.scalar(_DATA_STAMP_SQL))
        changed = stamp != self._last_stamp
        now = _now()
        # After a data change, watched views computed before it follow at once
        # (real time); otherwise at most every REFRESH s.
        active_cut = now - timedelta(
            seconds=cfg.dashboard_min_change_refresh_seconds
            if changed
            else cfg.dashboard_snapshot_refresh_seconds
        )
        idle_cut = now - timedelta(seconds=cfg.dashboard_snapshot_idle_refresh_seconds)

        defaults = self._default_keys()
        snap = DashboardSnapshot

        async def due(session: AsyncSession) -> list[tuple[str, str, dict]]:
            # Watched views follow the data every cycle it changed; recently
            # used ones and the defaults catch up with the clock (rolling
            # windows) every idle interval. Older ones refresh on their next
            # read (stale-while-revalidate).
            watched_and_changed = (snap.requested_at > now - ACTIVE_WINDOW) & (
                snap.computed_at < active_cut
            )
            idle = (snap.computed_at < idle_cut) & (
                (snap.requested_at > now - IDLE_WINDOW) | snap.key.in_(defaults)
            )
            condition = (watched_and_changed | idle) if changed else idle
            # Most recently viewed first: what is on screen updates first.
            order = snap.requested_at.desc() if changed else snap.computed_at
            rows = await session.execute(
                select(snap.key, snap.kind, snap.params)
                .where(condition)
                .order_by(order)
                .limit(MAX_REFRESH_PER_CYCLE)
            )
            return [(r.key, r.kind, r.params) for r in rows]

        (rows,) = await self.gather(due)
        for key, kind, params in rows:
            if kind not in _KINDS:
                continue
            try:
                await single_flight.run(
                    f"snapshot-build:{key}",
                    lambda k=kind, p=params, key_=key: compute_and_store(
                        k, p, self.gather, key_
                    ),
                )
                built += 1
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("snapshot_build_failed", kind=kind, exc_info=True)
        self._last_stamp = stamp
        await self._prune(now - timedelta(hours=cfg.dashboard_snapshot_keep_hours))
        self.cycles += 1
        if built:
            logger.info("snapshots_refreshed", count=built, data_changed=changed)
        return built

    @staticmethod
    def _defaults() -> dict[str, tuple[str, dict[str, Any]]]:
        return {
            snapshot_key(kind, params): (kind, params)
            for kind, spec in _KINDS.items()
            for params in spec.defaults
        }

    def _default_keys(self) -> list[str]:
        return list(self._defaults())

    async def _backfill_defaults(self) -> int:
        """Compute registered defaults that have no row yet (first run)."""
        wanted = self._defaults()
        if not wanted:
            return 0

        async def existing(session: AsyncSession) -> set[str]:
            rows = await session.execute(
                select(DashboardSnapshot.key).where(DashboardSnapshot.key.in_(wanted))
            )
            return set(rows.scalars())

        (have,) = await self.gather(existing)
        built = 0
        for key, (kind, params) in wanted.items():
            if key in have:
                continue
            try:
                await compute_and_store(kind, params, self.gather, key)
                built += 1
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("snapshot_backfill_failed", kind=kind, exc_info=True)
        return built

    async def _prune(self, before: datetime) -> None:
        defaults = self._default_keys()

        async def prune(session: AsyncSession) -> None:
            stmt = delete(DashboardSnapshot).where(
                DashboardSnapshot.requested_at < before
            )
            if defaults:
                stmt = stmt.where(DashboardSnapshot.key.not_in(defaults))
            await session.execute(stmt)
            await session.commit()

        await self.gather(prune)


async def snapshot_stats(session: AsyncSession) -> dict[str, Any]:
    """Row count and oldest snapshot (``/health``-style diagnostics, tests)."""
    row = (
        await session.execute(
            select(func.count(), func.min(DashboardSnapshot.computed_at))
        )
    ).one()
    return {"rows": int(row[0]), "oldest": row[1]}


class GatherRunner:
    """``ScopedRunner``-shaped wrapper of a ``gather`` (for code taking a runner)."""

    def __init__(self, gather: Gather) -> None:
        self.gather = gather


async def snapshot_query(
    kind: str,
    params: dict[str, Any],
    runner: ScopedRunner,
    query: Callable[[AsyncSession], Awaitable[Any]],
) -> Any:
    """``get_snapshot`` for one session-bound query (``query(session)``)."""

    async def build(gather: Gather) -> Any:
        (result,) = await gather(query)
        return result

    return await get_snapshot(kind, params, runner.gather, build)
