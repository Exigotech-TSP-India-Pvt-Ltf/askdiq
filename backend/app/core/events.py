"""Cross-worker event bus over Postgres LISTEN/NOTIFY (newplan section 13.6).

Each process keeps one dedicated asyncpg connection that LISTENs on
``EVENTS_CHANNEL``; it reconnects with exponential backoff when the
connection drops. ``publish(topic, id)`` sends a ``NOTIFY`` carrying only
ids (payloads are capped at 8 KB by Postgres), and every process fans the
event out to its in-process subscribers:

- queues from ``subscribe(topic)`` (SSE fan-out), and
- callbacks from ``on(topic, fn)`` (cache invalidation).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core import database
from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

MAX_PAYLOAD_BYTES = 7900
_QUEUE_MAXSIZE = 1000
_HEALTH_CHECK_SECONDS = 5.0

TOPIC_CACHE_INVALIDATE = "cache.invalidate"


@dataclass(frozen=True, slots=True)
class Event:
    topic: str
    id: str
    origin: str


def asyncpg_dsn(database_url: str) -> str:
    """Convert the SQLAlchemy URL to a plain asyncpg DSN."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


class EventBus:
    """One LISTEN connection per process plus in-process fan-out."""

    def __init__(
        self,
        dsn: str | None = None,
        channel: str | None = None,
        engine: AsyncEngine | None = None,
        reconnect_max_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        self.dsn = dsn or asyncpg_dsn(settings.database_url)
        self.channel = channel or settings.events_channel
        self._engine = engine
        self._reconnect_max = (
            reconnect_max_seconds
            if reconnect_max_seconds is not None
            else settings.events_reconnect_max_seconds
        )
        self.instance_id = uuid.uuid4().hex
        self._queues: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)
        self._callbacks: dict[str, list[Callable[[Event], Any]]] = defaultdict(list)
        self._conn: asyncpg.Connection | None = None
        self._lost = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.connected = asyncio.Event()
        self.connect_count = 0
        self.started = False

    # -- subscription ------------------------------------------------------

    def subscribe(self, topic: str) -> asyncio.Queue[Event]:
        """Return a queue receiving every event on ``topic`` ("*" for all)."""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._queues[topic].add(queue)
        return queue

    def unsubscribe(self, topic: str, queue: asyncio.Queue[Event]) -> None:
        self._queues[topic].discard(queue)

    def on(self, topic: str, callback: Callable[[Event], Any]) -> None:
        """Run a cheap synchronous callback for every event on ``topic``."""
        self._callbacks[topic].append(callback)

    # -- publishing --------------------------------------------------------

    async def publish(self, topic: str, item_id: str | int | uuid.UUID) -> None:
        """NOTIFY every process (including this one) about ``topic``/``id``."""
        payload = json.dumps(
            {"topic": topic, "id": str(item_id), "origin": self.instance_id},
            separators=(",", ":"),
        )
        if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("Event payload too large: publish ids, not data")
        engine = self._engine if self._engine is not None else database.engine
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {"channel": self.channel, "payload": payload},
            )

    def dispatch(self, event: Event) -> None:
        """Deliver an event to local subscribers (also used by tests)."""
        for topic in (event.topic, "*"):
            for queue in list(self._queues.get(topic, ())):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning("event_queue_full", topic=event.topic)
            for callback in list(self._callbacks.get(topic, ())):
                try:
                    callback(event)
                except Exception:
                    logger.exception("event_callback_failed", topic=event.topic)

    # -- listener lifecycle ------------------------------------------------

    async def start(self) -> None:
        self.started = True
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="event-bus-listener")

    async def stop(self) -> None:
        self.started = False
        self._stop.set()
        self._lost.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._close_conn()

    @property
    def backend_pid(self) -> int | None:
        conn = self._conn
        if conn is None or conn.is_closed():
            return None
        return conn.get_server_pid()

    def _on_notify(self, _conn: Any, _pid: int, _channel: str, payload: str) -> None:
        try:
            data = json.loads(payload)
            event = Event(
                topic=str(data["topic"]),
                id=str(data["id"]),
                origin=str(data.get("origin", "")),
            )
        except (ValueError, KeyError, TypeError):
            logger.warning("event_payload_invalid")
            return
        self.dispatch(event)

    def _on_terminate(self, _conn: Any) -> None:
        self.connected.clear()
        self._lost.set()

    async def _close_conn(self) -> None:
        conn, self._conn = self._conn, None
        self.connected.clear()
        if conn is not None and not conn.is_closed():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(conn.close(), timeout=2)

    async def _connect(self) -> None:
        conn = await asyncpg.connect(self.dsn, timeout=10)
        conn.add_termination_listener(self._on_terminate)
        await conn.add_listener(self.channel, self._on_notify)
        self._conn = conn
        self._lost.clear()
        self.connect_count += 1
        self.connected.set()
        logger.info("event_bus_listening", channel=self.channel)

    async def _wait_until_lost(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._lost.wait(), _HEALTH_CHECK_SECONDS)
            conn = self._conn
            if self._lost.is_set() or conn is None or conn.is_closed():
                return

    async def _run(self) -> None:
        delay = 0.5
        while not self._stop.is_set():
            try:
                await self._connect()
                delay = 0.5
                await self._wait_until_lost()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("event_bus_connect_failed", error=str(exc))
            await self._close_conn()
            if self._stop.is_set():
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._reconnect_max)


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Process-wide event bus (listener started from the lifespan)."""
    global _bus  # noqa: PLW0603 - one listener per process
    if _bus is None:
        _bus = EventBus()
    return _bus


async def publish_event(topic: str, item_id: str | int | uuid.UUID) -> None:
    """Best-effort NOTIFY; a failure is logged, never raised to the caller.

    A no-op in processes that never started the bus (tests, CLI scripts).
    """
    bus = get_event_bus()
    if not bus.started:
        return
    try:
        await bus.publish(topic, item_id)
    except Exception as exc:
        logger.warning("event_publish_failed", topic=topic, error=str(exc))
