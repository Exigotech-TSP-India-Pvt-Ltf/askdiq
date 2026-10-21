"""Shared resource telemetry: per-worker samples in Postgres, aggregated.

Workers write ``resource_samples`` rows (``app.governance.metrics_sampler``);
readers aggregate every *live* worker of every replica (a worker without a
sample for ``METRICS_WORKER_STALE_INTERVALS`` intervals is dropped):

- host / container values: the worst (max) reported,
- process values: summed (RSS, CPU, active queries, DB pool connections),
- event-loop lag: the worst worker,

plus a bucketed history (one point per sample interval) for sparklines.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
HOSTNAME = socket.gethostname()

_INSERT_SQL = text(
    "INSERT INTO resource_samples (worker_id, hostname, sampled_at, sample) "
    "VALUES (:worker_id, :hostname, :sampled_at, CAST(:sample AS jsonb))"
)
_PRUNE_SQL = text("DELETE FROM resource_samples WHERE sampled_at < :cutoff")
_WINDOW_SQL = text(
    "SELECT worker_id, hostname, sampled_at, sample FROM resource_samples "
    "WHERE sampled_at >= :since ORDER BY sampled_at"
)

Row = tuple[str, str, datetime, dict[str, Any]]


def utcnow() -> datetime:
    return datetime.now(UTC)


async def record_sample(
    db: AsyncSession,
    sample: dict[str, Any],
    *,
    now: datetime | None = None,
    worker_id: str = WORKER_ID,
    hostname: str = HOSTNAME,
) -> None:
    await db.execute(
        _INSERT_SQL,
        {
            "worker_id": worker_id,
            "hostname": hostname,
            "sampled_at": now or utcnow(),
            "sample": json.dumps(sample, separators=(",", ":")),
        },
    )


async def prune_samples(
    db: AsyncSession, *, now: datetime | None = None, settings: Settings | None = None
) -> int:
    cfg = settings or get_settings()
    cutoff = (now or utcnow()) - timedelta(minutes=cfg.metrics_history_minutes + 1)
    result = await db.execute(_PRUNE_SQL, {"cutoff": cutoff})
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def load_rows(db: AsyncSession, since: datetime) -> list[Row]:
    rows = (await db.execute(_WINDOW_SQL, {"since": since})).all()
    out: list[Row] = []
    for worker_id, hostname, sampled_at, sample in rows:
        data = sample if isinstance(sample, dict) else json.loads(sample)
        out.append((worker_id, hostname, sampled_at, data))
    return out


def _get(sample: dict[str, Any], *path: str) -> Any:
    value: Any = sample
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _max(values: list[Any]) -> float | None:
    nums = [v for v in (_num(x) for x in values) if v is not None]
    return round(max(nums), 2) if nums else None


def _sum(values: list[Any]) -> float | None:
    nums = [v for v in (_num(x) for x in values) if v is not None]
    return round(sum(nums), 2) if nums else None


def _pool_pct(samples: list[dict[str, Any]]) -> tuple[float | None, int, int]:
    used = sum(int(_get(s, "db_pool", "checked_out") or 0) for s in samples)
    cap = sum(
        int(_get(s, "db_pool", "size") or 0)
        + int(_get(s, "db_pool", "max_overflow") or 0)
        for s in samples
    )
    return (round(used * 100 / cap, 1) if cap else None), used, cap


def _queries_pct(samples: list[dict[str, Any]]) -> tuple[float | None, int, int]:
    active = sum(int(s.get("active_queries") or 0) for s in samples)
    cap = sum(int(s.get("query_capacity") or 0) for s in samples)
    return (round(min(100.0, active * 100 / cap), 1) if cap else None), active, cap


def _scope(samples: list[dict[str, Any]], key: str) -> str:
    scopes = {_get(s, "effective", key) for s in samples}
    return "container" if "container" in scopes else "host"


def aggregate(
    rows: list[Row], *, now: datetime, settings: Settings | None = None
) -> dict[str, Any]:
    """Current snapshot over live workers + bucketed history (pure function)."""
    cfg = settings or get_settings()
    interval = float(cfg.metrics_sample_interval_seconds)
    stale_before = now - timedelta(
        seconds=interval * cfg.metrics_worker_stale_intervals
    )
    latest: dict[str, Row] = {}
    for row in rows:
        if row[2] >= stale_before and (
            row[0] not in latest or row[2] > latest[row[0]][2]
        ):
            latest[row[0]] = row
    live = [r[3] for r in latest.values()]
    by_host: dict[str, dict[str, Any]] = {}
    for _worker, hostname, _ts, sample in latest.values():
        by_host.setdefault(hostname, sample)
    pool_pct, pool_used, pool_cap = _pool_pct(live)
    queries_pct, queries_active, queries_cap = _queries_pct(live)
    last_ts = max((r[2] for r in latest.values()), default=None)
    current = {
        "sampled_at": last_ts.isoformat() if last_ts else None,
        "age_seconds": round((now - last_ts).total_seconds(), 1) if last_ts else None,
        "interval_seconds": interval,
        "workers_live": len(live),
        "effective": {
            "cpu_percent": _max([_get(s, "effective", "cpu_percent") for s in live]),
            "cpu_scope": _scope(live, "cpu_scope"),
            "memory_percent": _max(
                [_get(s, "effective", "memory_percent") for s in live]
            ),
            "memory_scope": _scope(live, "memory_scope"),
        },
        "host": {
            "scope": "host",
            "cpu_percent": _max([_get(s, "host", "cpu_percent") for s in live]),
            "memory_percent": _max([_get(s, "host", "memory_percent") for s in live]),
            "memory_total_mb": _sum(
                [_get(s, "host", "memory_total_mb") for s in by_host.values()]
            ),
            "hosts": len(by_host),
        },
        "container": {
            "scope": "container",
            "cpu_percent": _max([_get(s, "container", "cpu_percent") for s in live]),
            "memory_percent": _max(
                [_get(s, "container", "memory_percent") for s in live]
            ),
            # Each container counted once (workers in one container share it).
            "memory_mb": _sum(
                [_get(s, "container", "memory_mb") for s in by_host.values()]
            ),
            "memory_limit_mb": _sum(
                [_get(s, "container", "memory_limit_mb") for s in by_host.values()]
            ),
        }
        if any(_get(s, "container") for s in live)
        else None,
        "process": {
            "scope": "process",
            "rss_mb": _sum([_get(s, "process", "rss_mb") for s in live]),
            "cpu_percent": _sum([_get(s, "process", "cpu_percent") for s in live]),
        },
        "event_loop": {
            "scope": "process",
            "lag_p95_ms": _max([_get(s, "loop", "p95_ms") for s in live]),
            "lag_max_ms": _max([_get(s, "loop", "max_ms") for s in live]),
        },
        "db_pool": {
            "scope": "process",
            "in_use_percent": pool_pct,
            "checked_out": pool_used,
            "capacity": pool_cap,
        },
        "active_queries": int(_sum([s.get("active_queries") for s in live]) or 0),
        "queries": {
            "scope": "process",
            "in_use_percent": queries_pct,
            "active": queries_active,
            "capacity": queries_cap,
        },
        "workers": [
            {
                "worker_id": worker_id,
                "hostname": hostname,
                "pid": _get(sample, "process", "pid"),
                "last_seen": ts.isoformat(),
                "rss_mb": _get(sample, "process", "rss_mb"),
                "cpu_percent": _get(sample, "process", "cpu_percent"),
                "loop_lag_p95_ms": _get(sample, "loop", "p95_ms"),
                "db_pool_checked_out": _get(sample, "db_pool", "checked_out"),
                "active_queries": sample.get("active_queries"),
            }
            for worker_id, hostname, ts, sample in sorted(latest.values())
        ],
    }
    current["history"] = history(rows, interval=interval)
    return current


def history(rows: list[Row], *, interval: float) -> list[dict[str, Any]]:
    """One point per ``interval`` bucket: worst CPU/memory/lag, summed RSS."""
    buckets: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for worker_id, _host, ts, sample in rows:
        bucket = int(ts.timestamp() // interval)
        buckets[bucket][worker_id] = sample  # latest sample of the worker wins
    points = []
    for bucket in sorted(buckets):
        samples = list(buckets[bucket].values())
        pool_pct, _u, _c = _pool_pct(samples)
        queries_pct, _a, _q = _queries_pct(samples)
        points.append(
            {
                "ts": datetime.fromtimestamp(bucket * interval, tz=UTC).isoformat(),
                "cpu_percent": _max(
                    [_get(s, "effective", "cpu_percent") for s in samples]
                ),
                "memory_percent": _max(
                    [_get(s, "effective", "memory_percent") for s in samples]
                ),
                "rss_mb": _sum([_get(s, "process", "rss_mb") for s in samples]),
                "loop_lag_p95_ms": _max([_get(s, "loop", "p95_ms") for s in samples]),
                "db_pool_percent": pool_pct,
                "queries_percent": queries_pct,
            }
        )
    return points


async def resources_snapshot(
    db: AsyncSession, *, now: datetime | None = None, settings: Settings | None = None
) -> dict[str, Any]:
    cfg = settings or get_settings()
    now = now or utcnow()
    rows = await load_rows(db, now - timedelta(minutes=cfg.metrics_history_minutes))
    return aggregate(rows, now=now, settings=cfg)
