"""Per-worker resource sampler (runs in every API worker from the lifespan).

Every ``METRICS_SAMPLE_INTERVAL_SECONDS``: one ``ResourceProbe`` sample (in
a thread: it reads /proc and /sys), plus event-loop lag, DB pool use and
active /query requests of this worker, written to ``resource_samples``.
Old rows are pruned by whichever worker gets there; a failed write only
logs (the next sample tries again).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from app.core import database
from app.core.config import Settings, get_settings
from app.core.logging_config import get_logger
from app.governance.resource_store import prune_samples, record_sample
from app.governance.resources import LoopLagMonitor, ResourceProbe

logger = get_logger(__name__)

_PRUNE_EVERY = 12


def pool_sample() -> dict[str, Any]:
    stats = database.pool_stats()
    capacity = stats["size"] + stats["max_overflow"]
    return {
        **stats,
        "in_use_percent": round(stats["checked_out"] * 100 / capacity, 1)
        if capacity
        else None,
    }


def active_queries() -> int:
    from app.core.middleware import METRICS  # noqa: PLC0415 - avoid import cycle

    return int(METRICS.get("active_queries", 0))


class MetricsSampler:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        probe: ResourceProbe | None = None,
        lag: LoopLagMonitor | None = None,
    ) -> None:
        self.cfg = settings or get_settings()
        self.probe = probe or ResourceProbe()
        self.lag = lag or LoopLagMonitor()
        self.latest: dict[str, Any] | None = None
        self.samples = 0

    async def collect(self) -> dict[str, Any]:
        sample = await asyncio.to_thread(self.probe.sample)
        sample["loop"] = self.lag.drain()
        sample["db_pool"] = pool_sample()
        sample["active_queries"] = active_queries()
        self.latest = sample
        return sample

    async def sample_once(self) -> dict[str, Any]:
        sample = await self.collect()
        async with database.AsyncSessionLocal() as db:
            await record_sample(db, sample)
            self.samples += 1
            if self.samples % _PRUNE_EVERY == 1:
                await prune_samples(db, settings=self.cfg)
            await db.commit()
        return sample

    async def run(self, stop: asyncio.Event) -> None:
        self.lag.start()
        interval = float(self.cfg.metrics_sample_interval_seconds)
        try:
            # Prime the counters: CPU percentages need two readings.
            await asyncio.to_thread(self.probe.sample)
            while not stop.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                if stop.is_set():
                    break
                try:
                    await self.sample_once()
                except Exception as exc:
                    logger.warning(
                        "metrics_sample_failed", error_type=type(exc).__name__
                    )
        finally:
            await self.lag.stop()
