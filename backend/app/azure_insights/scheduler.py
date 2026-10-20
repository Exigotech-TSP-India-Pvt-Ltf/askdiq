"""Azure Insights sources, their job handlers and their schedule.

Every source follows one contract, enforced here so a new source gets it by
registering (see ``README.md``):

    poll on schedule -> persist with history -> refresh snapshots -> announce live

- :func:`register_source` registers the job handler ``azure.<name>``.
- :func:`ensure_azure_scheduled` (startup + every few minutes from the jobs
  worker) queues each active source for its next slot; a source that has not
  run within its interval is queued at once. The queue's dedupe key
  (``azure.<name>:<slot>``) plus ``FOR UPDATE SKIP LOCKED`` mean exactly one
  replica runs each slot.
- The handler (:func:`run_source`) records the run in ``azure_sync_status``,
  calls the source's ``run(ctx)`` (which fetches from Azure and upserts its
  rows), then re-arms the next slot and recomputes the source's snapshot
  kinds (plus ``azure.status``), which announces them on the console live
  stream (``/admin/live``) so open consoles update at once.
- Throttling (429 / Retry-After / QPU) defers the same job to ``now +
  retry_after`` without spending an attempt; auth / permission / 4xx errors
  are recorded and wait for the next slot; transient errors (5xx after the
  in-process retries, unexpected exceptions) use the queue's retry backoff.
- Disabled (``AZURE_INSIGHTS_ENABLED=false`` or the source's own flag): no
  job is queued, and an already queued one is a no-op.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.azure_insights import status as sync_status
from app.azure_insights.auth import get_token_provider
from app.azure_insights.client import ARM_BASE_URL, AzureClient
from app.azure_insights.errors import (
    AzureApiError,
    AzureInsightsError,
    AzureThrottledError,
)
from app.core import database
from app.core.cache import dashboard_cache
from app.core.concurrency import ScopedRunner, SessionFactory
from app.core.config import Settings, get_settings
from app.core.events import TOPIC_CONSOLE_CHANGED, publish_event
from app.core.logging_config import get_logger
from app.dashboard.snapshots import (
    CACHE_PREFIX,
    Gather,
    compute_and_store,
    registered_kinds,
    snapshot_key,
)
from app.jobs.queue import JobDeferredError, enqueue, utcnow
from app.jobs.registry import job_handler, unregister

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = get_logger(__name__)

JOB_PREFIX = "azure."
# Snapshot kind of the "Azure connection" card (registered in service.py).
STATUS_KIND = "azure.status"
# How often the jobs worker re-checks the schedule (cheap; dedupe keys).
SCHEDULE_CHECK_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class SourceResult:
    """What a run reports: rows written and a small JSON ``detail`` stored on
    the status row (e.g. the effective date of the prices)."""

    rows: int = 0
    detail: dict[str, Any] | None = None


@dataclass(slots=True)
class SourceContext:
    """Handed to ``AzureSource.run``: settings, the run's clock, sessions and
    HTTP clients (the caller closes clients with ``async with``)."""

    source: AzureSource
    settings: Settings
    now: datetime
    session_factory: SessionFactory
    transport: Any = None  # httpx transport override (tests)

    def arm_client(self, *, budgeted: bool = False) -> AzureClient:
        """Authenticated ARM client; ``budgeted`` counts every call against
        the source's monthly cap (raises AzureBudgetExhaustedError at the cap)."""
        budget = None
        if budgeted:
            cap = self.source.call_cap(self.settings) if self.source.call_cap else 0
            budget = sync_status.call_budget(
                self.source.name, cap, self.session_factory
            )
        return AzureClient(
            base_url=ARM_BASE_URL,
            token_source=get_token_provider(self.settings),
            timeout=self.settings.azure_http_timeout_seconds,
            budget=budget,
            transport=self.transport,
        )

    def public_client(self, base_url: str) -> AzureClient:
        """Unauthenticated client (public APIs such as Retail Prices)."""
        return AzureClient(
            base_url=base_url,
            token_source=None,
            timeout=self.settings.azure_http_timeout_seconds,
            transport=self.transport,
        )


SourceRunner = Callable[[SourceContext], Awaitable[SourceResult | None]]


def _always(_settings: Settings) -> bool:
    return True


@dataclass(frozen=True, slots=True)
class AzureSource:  # pylint: disable=too-many-instance-attributes
    """One Azure data source.

    ``interval(settings)``: seconds between polls. ``run(ctx)``: fetch and
    upsert (idempotent, history kept). ``snapshot_kinds``: kinds recomputed
    and announced after every run. ``live_scopes``: extra console scopes to
    announce. ``needs_credentials=False`` for public APIs (still scheduled
    only when Azure Insights is enabled; on-demand runs always work).
    ``call_cap(settings)``: monthly cap for ``ctx.arm_client(budgeted=True)``.
    ``anchor(settings)``: optional ``(IANA zone, local time)`` the slots are
    aligned to (e.g. daily at 03:00 Australia/Sydney); default epoch-aligned.
    """

    name: str
    label: str
    interval: Callable[[Settings], int]
    run: SourceRunner
    needs_credentials: bool = True
    enabled: Callable[[Settings], bool] = _always
    snapshot_kinds: tuple[str, ...] = ()
    live_scopes: tuple[str, ...] = ()
    call_cap: Callable[[Settings], int] | None = None
    order: int = 100
    anchor: Callable[[Settings], tuple[str, time]] | None = None

    @property
    def job_type(self) -> str:
        return f"{JOB_PREFIX}{self.name}"


_SOURCES: dict[str, AzureSource] = {}


def register_source(source: AzureSource) -> AzureSource:
    """Register ``source`` and its job handler ``azure.<name>`` (idempotent
    per name: re-registering replaces the source, the handler stays)."""
    first = source.name not in _SOURCES
    _SOURCES[source.name] = source
    if first:

        async def _handler(payload: dict[str, Any]) -> None:
            await run_source(source.name, payload=payload)

        _handler.__name__ = f"azure_{source.name}_task"
        job_handler(source.job_type)(_handler)
    return source


def registered_sources() -> list[AzureSource]:
    return sorted(_SOURCES.values(), key=lambda s: (s.order, s.name))


def get_source(name: str) -> AzureSource:
    return _SOURCES[name]


def unregister_source(name: str) -> None:
    """Tests only."""
    source = _SOURCES.pop(name, None)
    if source is not None:
        unregister(source.job_type)


def is_scheduled(source: AzureSource, settings: Settings) -> bool:
    """Polled in the background: Azure Insights enabled and the source's flag on."""
    return settings.azure_insights_configured and source.enabled(settings)


def can_run(source: AzureSource, settings: Settings) -> bool:
    """Runnable now (on demand): public sources always, others when configured."""
    if not source.needs_credentials:
        return True
    return is_scheduled(source, settings)


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def slot_start(now: datetime, interval: int) -> datetime:
    """Start of the epoch-aligned slot containing ``now``."""
    seconds = int((now - _EPOCH).total_seconds())
    return _EPOCH + timedelta(seconds=seconds - seconds % max(1, interval))


def next_slot(now: datetime, interval: int) -> datetime:
    return slot_start(now, interval) + timedelta(seconds=max(1, interval))


_DAY_SECONDS = 86_400


def _zone(name: str | tzinfo) -> tzinfo:
    if isinstance(name, tzinfo):
        return name
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # No IANA database on this host (e.g. Windows without tzdata).
        logger.warning("azure_schedule_zone_missing", zone=name)
        return UTC


def anchored_slot(
    now: datetime, interval: int, zone: str | tzinfo, at: time
) -> tuple[datetime, datetime]:
    """(start, next) of the slot containing ``now`` when slots are aligned to
    the local time ``at`` in ``zone`` (DST-aware): daily or longer intervals
    run at ``at`` every ``interval // 1 day`` days, shorter ones every
    ``interval`` from ``at`` (restarting at ``at`` each day). UTC results;
    an unknown zone falls back to UTC."""
    tz = _zone(zone)
    now = now.astimezone(UTC)

    def at_day(day: date) -> datetime:
        return datetime.combine(day, at, tzinfo=tz).astimezone(UTC)

    local_day = now.astimezone(tz).date()
    if interval >= _DAY_SECONDS:
        days = interval // _DAY_SECONDS
        first = date.fromordinal(local_day.toordinal() - local_day.toordinal() % days)
        if at_day(first) > now:
            first -= timedelta(days=days)
        return at_day(first), at_day(first + timedelta(days=days))
    if at_day(local_day) > now:
        local_day -= timedelta(days=1)
    base, next_base = at_day(local_day), at_day(local_day + timedelta(days=1))
    step = timedelta(seconds=max(1, interval))
    start = base + step * ((now - base) // step)
    return start, min(start + step, next_base)


def source_slot(
    source: AzureSource, settings: Settings, now: datetime
) -> tuple[datetime, datetime]:
    """(start, next) of the source's slot containing ``now``."""
    interval = source.interval(settings)
    if source.anchor is not None:
        zone, at = source.anchor(settings)
        return anchored_slot(now, interval, zone, at)
    return slot_start(now, interval), next_slot(now, interval)


def dedupe_key(source: AzureSource, slot: datetime) -> str:
    return f"{source.job_type}:{slot:%Y%m%dT%H%M%S}"


async def _enqueue_slot(
    source: AzureSource,
    slot: datetime,
    run_after: datetime,
    engine: AsyncEngine | None,
) -> Any:
    return await enqueue(
        source.job_type,
        {"slot": slot.isoformat()},
        dedupe_key=dedupe_key(source, slot),
        run_after=run_after,
        engine=engine,
    )


async def ensure_azure_scheduled(  # pylint: disable=too-many-locals
    *,
    now: datetime | None = None,
    engine: AsyncEngine | None = None,
    session_factory: SessionFactory | None = None,
) -> dict[str, datetime]:
    """Queue every active source for its next run; returns {source: run_after}.

    A no-op (empty dict) when Azure Insights is disabled. Safe to call often:
    the dedupe key makes a repeat a no-op."""
    settings = get_settings()
    if not settings.azure_insights_configured:
        return {}
    now = now or utcnow()
    factory = session_factory or database.AsyncSessionLocal
    async with factory() as session:
        rows = await sync_status.read_all(session)
    planned: dict[str, datetime] = {}
    for source in registered_sources():
        if not is_scheduled(source, settings):
            continue
        interval = source.interval(settings)
        row = rows.get(source.name)
        last = row.last_run_at if row is not None else None
        current, upcoming = source_slot(source, settings, now)
        if last is None or last + timedelta(seconds=interval) <= now:
            # Never run, or missed its slot (downtime): catch up now.
            slot, run_after = current, now
        else:
            slot = upcoming
            run_after = slot
        await _enqueue_slot(source, slot, run_after, engine)
        planned[source.name] = run_after
    async with factory() as session:
        for name, when in planned.items():
            await sync_status.set_next_run(session, name, when)
        await session.commit()
    return planned


# ---------------------------------------------------------------------------
# Running a source
# ---------------------------------------------------------------------------


def _is_transient(error: BaseException) -> bool:
    if isinstance(error, AzureApiError):
        return error.status_code is None or error.status_code >= 500  # noqa: PLR2004
    return not isinstance(error, AzureInsightsError)


async def run_source(  # pylint: disable=too-many-arguments,too-many-locals
    name: str,
    *,
    payload: dict[str, Any] | None = None,
    now: datetime | None = None,
    session_factory: SessionFactory | None = None,
    gather: Gather | None = None,
    transport: Any = None,
    rearm: bool = True,
) -> SourceResult | None:
    """Run one poll of ``name`` with the full contract (status, re-arm,
    snapshot refresh, live announcement). Returns the result, or None when
    the source is not runnable (disabled) or the run failed non-transiently.

    Raises ``JobDeferredError`` when throttled (the worker re-queues the same
    job) and re-raises transient errors (the queue retries with backoff)."""
    del payload
    source = get_source(name)
    settings = get_settings()
    scheduled = is_scheduled(source, settings)
    if not can_run(source, settings):
        logger.info("azure_source_skipped", source=name, reason="not_configured")
        return None
    now = now or utcnow()
    factory = session_factory or database.AsyncSessionLocal
    ctx = SourceContext(source, settings, now, factory, transport)
    async with factory() as session:
        await sync_status.mark_started(session, name, now)
        await session.commit()
    deferred_until: datetime | None = None
    try:
        result = await source.run(ctx) or SourceResult()
        async with factory() as session:
            await sync_status.record_success(
                session, name, now=utcnow(), detail=result.detail
            )
            await session.commit()
    except AzureThrottledError as exc:
        deferred_until = utcnow() + timedelta(seconds=exc.retry_after)
        await _record_failure(factory, name, exc)
        raise JobDeferredError(deferred_until, f"azure throttled: {exc.code}") from None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        await _record_failure(factory, name, exc)
        if _is_transient(exc):
            raise
        return None
    finally:
        if scheduled and rearm and deferred_until is None:
            await _rearm(source, settings, factory)
        # Success or not: the new rows / the status card reach open consoles.
        await refresh_views(source, gather=gather)
    logger.info("azure_source_done", source=name, rows=result.rows)
    return result


async def _record_failure(
    factory: SessionFactory, name: str, exc: BaseException
) -> None:
    logger.warning(
        "azure_source_failed",
        source=name,
        error_type=type(exc).__name__,
        code=getattr(exc, "code", None),
    )
    try:
        async with factory() as session:
            await sync_status.record_failure(session, name, exc, now=utcnow())
            await session.commit()
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("azure_status_write_failed", source=name, exc_info=True)


async def _rearm(
    source: AzureSource, settings: Settings, factory: SessionFactory
) -> None:
    try:
        _, slot = source_slot(source, settings, utcnow())
        await _enqueue_slot(source, slot, slot, None)
        async with factory() as session:
            await sync_status.set_next_run(session, source.name, slot)
            await session.commit()
    except Exception:  # pylint: disable=broad-exception-caught
        # ensure_azure_scheduled re-arms within SCHEDULE_CHECK_SECONDS.
        logger.warning("azure_rearm_failed", source=source.name, exc_info=True)


# ---------------------------------------------------------------------------
# Snapshots + live stream
# ---------------------------------------------------------------------------


async def refresh_views(
    source: AzureSource | None = None,
    *,
    gather: Gather | None = None,
    kinds: tuple[str, ...] | None = None,
) -> list[str]:
    """Recompute the stored snapshots of ``kinds`` (default: ``azure.status``
    plus the source's kinds) for every registered default parameter set; each
    store announces ``TOPIC_SNAPSHOT_UPDATED`` (other replicas reload their
    memory copy) and ``console.changed`` (open consoles refetch). Without
    stored snapshots, the memory copies are dropped and the scopes announced.
    Returns the announced scopes. Never raises."""
    if kinds is None:
        kinds = (STATUS_KIND, *(source.snapshot_kinds if source else ()))
    scopes = list(dict.fromkeys((*kinds, *(source.live_scopes if source else ()))))
    try:
        await _refresh(kinds, scopes, gather)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("azure_views_refresh_failed", kinds=list(kinds), exc_info=True)
    return scopes


async def _refresh(
    kinds: tuple[str, ...], scopes: list[str], gather: Gather | None
) -> None:
    cfg = get_settings()
    registry = registered_kinds()
    announced: set[str] = set()
    for kind in kinds:
        spec = registry.get(kind)
        defaults = spec.defaults if spec is not None else ()
        if cfg.dashboard_snapshots_persist and spec is not None:
            run = gather or ScopedRunner().gather
            for params in defaults or ({},):
                await compute_and_store(kind, params, run)
            announced.add(kind)  # compute_and_store announced it
            continue
        for params in defaults or ({},):
            dashboard_cache.invalidate_prefix(CACHE_PREFIX + snapshot_key(kind, params))
    for scope in scopes:
        if scope not in announced:
            await publish_event(TOPIC_CONSOLE_CHANGED, scope)
    # Let the announce tasks spawned by compute_and_store get going.
    await asyncio.sleep(0)


async def bootstrap_azure_insights() -> None:
    """Startup: queue the active sources (only when enabled + configured) and
    refresh the status card (it reflects the current flags). Never raises."""
    try:
        planned = await ensure_azure_scheduled()
        if planned:
            logger.info("azure_insights_scheduled", sources=sorted(planned))
        else:
            logger.info("azure_insights_not_scheduled", reason="not_configured")
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("azure_insights_schedule_failed", exc_info=True)
    await refresh_views(kinds=(STATUS_KIND,))
