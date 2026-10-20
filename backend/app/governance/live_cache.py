"""In-memory copies of the live Governance aggregates (Overall, Monitoring).

A read never waits on the aggregation once a copy exists: each audience's
payload is kept in memory, JSON-encoded once with its weak ETag, so a request
(or a ``304`` revalidation) is a dictionary lookup.

- The governance stream producer (``app.governance.stream``) refreshes the
  overview every ``GOVERNANCE_STREAM_INTERVAL_SECONDS`` (or at once on an
  incident NOTIFY) while anyone watches, and stores what it computed here.
- A copy older than ``fresh_seconds`` is still served; a newer one is
  computed in the background (one at a time per audience).
- Only the very first read of an audience in a process computes while the
  caller waits; :func:`warm` precomputes every audience at startup so even
  that is rare.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.concurrency import ScopedRunner, single_flight
from app.core.http_cache import weak_etag
from app.core.logging_config import get_logger

logger = get_logger(__name__)

Compute = Callable[[str, ScopedRunner], Awaitable[dict[str, Any]]]
AUDIENCES: tuple[str, ...] = ("overview", "users", "testing")
_REGISTRY: list[LiveCache] = []


@dataclass(frozen=True, slots=True)
class Entry:
    payload: dict[str, Any]
    body: bytes
    etag: str
    as_of: datetime
    stored_at: float  # time.monotonic()


def encode(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


class LiveCache:
    """Per-process, per-audience memory copy of one aggregate."""

    def __init__(
        self,
        name: str,
        compute: Compute,
        fresh_seconds: Callable[[], float],
        encoder: Callable[[dict[str, Any]], dict[str, Any]] = lambda p: p,
    ) -> None:
        self.name = name
        self._compute = compute
        self._fresh_seconds = fresh_seconds
        self._encoder = encoder
        self._entries: dict[str, Entry] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self.computes = 0
        self._read_at: dict[str, float] = {}
        _REGISTRY.append(self)

    # -- writes -----------------------------------------------------------

    def store(self, audience: str, payload: dict[str, Any]) -> Entry:
        """Keep ``payload`` (JSON-encoded once); an unchanged body keeps its ETag."""
        body = encode(self._encoder(payload))
        previous = self._entries.get(audience)
        now = time.monotonic()
        if previous is not None and previous.body == body:
            entry = Entry(previous.payload, body, previous.etag, _utcnow(), now)
        else:
            entry = Entry(payload, body, weak_etag(body), _utcnow(), now)
        self._entries[audience] = entry
        return entry

    async def refresh(self, audience: str, runner: ScopedRunner | None = None) -> Entry:
        """Compute now (one computation per audience at a time) and store it."""

        async def run() -> Entry:
            payload = await self._compute(audience, runner or ScopedRunner())
            self.computes += 1
            return self.store(audience, payload)

        return await single_flight.run(f"governance-live:{self.name}:{audience}", run)

    def clear(self) -> None:
        self._entries.clear()
        self._read_at.clear()

    def mark_stale(self, audience: str | None = None) -> None:
        """Next read serves the copy and recomputes it in the background."""
        for aud in [audience] if audience else list(self._entries):
            entry = self._entries.get(aud)
            if entry is not None:
                self._entries[aud] = Entry(
                    entry.payload, entry.body, entry.etag, entry.as_of, -1e18
                )

    # -- reads ------------------------------------------------------------

    def recently_read(self, audience: str, seconds: float) -> bool:
        """True when a client read ``audience`` within ``seconds`` (on screen)."""
        read_at = self._read_at.get(audience)
        return read_at is not None and time.monotonic() - read_at <= seconds

    def peek(self, audience: str) -> Entry | None:
        return self._entries.get(audience)

    async def get(self, audience: str, runner: ScopedRunner | None = None) -> Entry:
        """The copy (computed with ``runner`` when there is none yet)."""
        self._read_at[audience] = time.monotonic()
        entry = self._entries.get(audience)
        if entry is None:
            return await self.refresh(audience, runner)
        if time.monotonic() - entry.stored_at > self._fresh_seconds():
            self.refresh_soon(audience)
        return entry

    def refresh_soon(self, audience: str) -> None:
        task = asyncio.create_task(self._refresh_quietly(audience))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _refresh_quietly(self, audience: str) -> None:
        try:
            await self.refresh(audience)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning(
                "governance_live_refresh_failed", name=self.name, exc_info=True
            )

    async def warm(self, audiences: Iterable[str] = AUDIENCES) -> None:
        for audience in audiences:
            with contextlib.suppress(Exception):
                await self.refresh(audience)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def clear_live_caches() -> None:
    """Drop every copy (the audience classification changed; tests)."""
    for cache in _REGISTRY:
        cache.clear()
