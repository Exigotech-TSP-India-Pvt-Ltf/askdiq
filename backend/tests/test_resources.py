"""Resource telemetry: cgroup parsing, probe deltas, cross-worker aggregation,
sustain logic, resource alert rules, live stream and the DB round trip."""

from __future__ import annotations

import asyncio
import contextlib
from collections import namedtuple
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import catalogue as c
from app.alerts import jobs as alert_jobs
from app.alerts.resource_rules import evaluate_resource_rows, sustained_min
from app.core.config import get_settings
from app.core.events import EventBus
from app.governance import resource_store
from app.governance.resources import LoopLagMonitor, ResourceProbe, read_cgroup
from app.governance.stream import GovernanceStreamHub
from app.models.db_models import Alert

NOW = datetime(2099, 6, 1, 12, 0, tzinfo=UTC)


def cfg(**overrides: Any):
    values = {
        "metrics_sample_interval_seconds": 5.0,
        "alert_resource_sustain_seconds": 60,
        "smtp_host": "",
    }
    values.update(overrides)
    return get_settings().model_copy(update=values)


# --- cgroup parsing ------------------------------------------------------------


def _write(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="ascii")
    return root


def test_cgroup_v2_with_limits(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "cgroup.controllers": "cpu memory io",
            "cpu.max": "200000 100000",
            "cpu.stat": "usage_usec 5000000\nuser_usec 4000000\nsystem_usec 1000000",
            "memory.current": str(512 * 1024 * 1024),
            "memory.max": str(2 * 1024 * 1024 * 1024),
        },
    )
    stats = read_cgroup(root)
    assert stats.version == 2
    assert stats.cpu_limit_cores == 2.0
    assert stats.cpu_usage_usec == 5_000_000
    assert stats.memory_bytes == 512 * 1024 * 1024
    assert stats.memory_limit_bytes == 2 * 1024 * 1024 * 1024


def test_cgroup_v2_unlimited(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "cgroup.controllers": "cpu memory",
            "cpu.max": "max 100000",
            "cpu.stat": "usage_usec 10",
            "memory.current": "1000",
            "memory.max": "max",
        },
    )
    stats = read_cgroup(root)
    assert stats.cpu_limit_cores is None
    assert stats.memory_limit_bytes is None


def test_cgroup_v1_fallbacks(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "cpu,cpuacct/cpu.cfs_quota_us": "150000",
            "cpu,cpuacct/cpu.cfs_period_us": "100000",
            "cpu,cpuacct/cpuacct.usage": "3000000000",
            "memory/memory.usage_in_bytes": "1048576",
            "memory/memory.limit_in_bytes": str(1 << 62),  # "no limit"
        },
    )
    stats = read_cgroup(root)
    assert stats.version == 1
    assert stats.cpu_limit_cores == 1.5
    assert stats.cpu_usage_usec == 3_000_000
    assert stats.memory_bytes == 1048576
    assert stats.memory_limit_bytes is None


def test_no_cgroup_outside_a_container(tmp_path: Path) -> None:
    stats = read_cgroup(tmp_path / "missing")
    assert stats.version is None


# --- probe ---------------------------------------------------------------------

CpuTimes = namedtuple("CpuTimes", "user system idle")
ProcTimes = namedtuple("ProcTimes", "user system")
Mem = namedtuple("Mem", "total percent")
MemInfo = namedtuple("MemInfo", "rss")


class FakePsutil:
    def __init__(self) -> None:
        self.busy = 0.0
        self.idle = 0.0
        self.proc = 0.0
        self.rss = 100 * 1024 * 1024

    def cpu_times(self) -> CpuTimes:
        return CpuTimes(self.busy, 0.0, self.idle)

    def virtual_memory(self) -> Mem:
        return Mem(16 * 1024**3, 42.0)

    def cpu_count(self) -> int:
        return 8

    def Process(self, _pid: int) -> Any:  # noqa: N802 - mirrors psutil
        outer = self

        class _P:
            def cpu_times(self) -> ProcTimes:
                return ProcTimes(outer.proc, 0.0)

            def memory_info(self) -> MemInfo:
                return MemInfo(outer.rss)

        return _P()


def test_probe_uses_deltas_and_prefers_container_limits(tmp_path: Path) -> None:
    files = {
        "cgroup.controllers": "cpu memory",
        "cpu.max": "200000 100000",
        "cpu.stat": "usage_usec 0",
        "memory.current": str(900 * 1024 * 1024),
        "memory.max": str(1024 * 1024 * 1024),
    }
    root = _write(tmp_path, files)
    clock = [0.0]
    ps = FakePsutil()
    probe = ResourceProbe(cgroup_root=root, clock=lambda: clock[0], psutil_module=ps)
    first = probe.sample()
    assert first["host"]["cpu_percent"] is None  # needs two readings
    # 10 s later: host 6 of 8+? busy share 75%, container used 10 cores-seconds
    # of 2 cores x 10 s = 50%, process 5 CPU-seconds = 50% of one core.
    clock[0] = 10.0
    ps.busy, ps.idle, ps.proc = 60.0, 20.0, 5.0
    _write(root, {"cpu.stat": "usage_usec 10000000"})
    second = probe.sample()
    assert second["host"]["cpu_percent"] == 75.0
    assert second["container"]["cpu_percent"] == 50.0
    assert second["container"]["memory_percent"] == pytest.approx(87.89, abs=0.01)
    assert second["process"]["cpu_percent"] == 50.0
    assert second["process"]["rss_mb"] == 100.0
    assert second["effective"] == {
        "cpu_percent": 50.0,
        "cpu_scope": "container",
        "memory_percent": pytest.approx(87.89, abs=0.01),
        "memory_scope": "container",
    }


def test_probe_without_container_uses_host(tmp_path: Path) -> None:
    clock = [0.0]
    ps = FakePsutil()
    probe = ResourceProbe(
        cgroup_root=tmp_path / "none", clock=lambda: clock[0], psutil_module=ps
    )
    probe.sample()
    clock[0] = 5.0
    ps.busy, ps.idle = 1.0, 3.0
    sample = probe.sample()
    assert sample["container"] is None
    assert sample["effective"]["cpu_scope"] == "host"
    assert sample["effective"]["cpu_percent"] == 25.0
    assert sample["effective"]["memory_percent"] == 42.0


async def test_loop_lag_monitor_measures_blocking() -> None:
    lag = LoopLagMonitor(probe_seconds=0.01)
    lag.start()
    await asyncio.sleep(0.05)
    import time  # noqa: PLC0415

    time.sleep(0.2)  # block the loop  # noqa: ASYNC251
    await asyncio.sleep(0.05)
    await lag.stop()
    drained = lag.drain()
    assert drained["max_ms"] is not None and drained["max_ms"] >= 150
    assert lag.drain() == {"p95_ms": None, "max_ms": None}


# --- aggregation -------------------------------------------------------------------


def sample(
    *,
    cpu: float = 10.0,
    mem: float = 40.0,
    rss: float = 100.0,
    lag: float = 1.0,
    pool_used: int = 1,
    scope: str = "host",
) -> dict[str, Any]:
    return {
        "host": {"cpu_percent": cpu, "memory_percent": mem},
        "container": None,
        "process": {"pid": 1, "rss_mb": rss, "cpu_percent": cpu / 2},
        "effective": {
            "cpu_percent": cpu,
            "cpu_scope": scope,
            "memory_percent": mem,
            "memory_scope": scope,
        },
        "loop": {"p95_ms": lag, "max_ms": lag * 2},
        "db_pool": {"size": 10, "max_overflow": 0, "checked_out": pool_used},
        "active_queries": 2,
        "query_capacity": 64,
    }


def test_aggregate_sums_workers_and_drops_dead_ones() -> None:
    rows = [
        ("w1", "api-1", NOW - timedelta(seconds=4), sample(cpu=30, rss=200, lag=5)),
        (
            "w2",
            "api-1",
            NOW - timedelta(seconds=2),
            sample(cpu=50, rss=300, lag=40, pool_used=5),
        ),
        # Stale: last sample long before the 3-interval window.
        ("w3", "api-2", NOW - timedelta(seconds=60), sample(cpu=99, rss=900)),
    ]
    snap = resource_store.aggregate(rows, now=NOW, settings=cfg())
    assert snap["workers_live"] == 2
    assert {w["worker_id"] for w in snap["workers"]} == {"w1", "w2"}
    assert snap["effective"]["cpu_percent"] == 50
    assert snap["process"]["rss_mb"] == 500
    assert snap["event_loop"]["lag_p95_ms"] == 40
    assert snap["db_pool"] == {
        "scope": "process",
        "in_use_percent": 30.0,
        "checked_out": 6,
        "capacity": 20,
    }
    assert snap["active_queries"] == 4
    assert snap["queries"] == {
        "scope": "process",
        "in_use_percent": 3.1,
        "active": 4,
        "capacity": 128,
    }
    assert snap["host"]["scope"] == "host"
    assert len(snap["history"]) >= 2
    assert snap["history"][-1]["queries_percent"] is not None


def test_queries_gauge_is_empty_for_samples_without_capacity() -> None:
    old = sample()
    del old["query_capacity"]
    snap = resource_store.aggregate(
        [("w1", "api-1", NOW - timedelta(seconds=1), old)], now=NOW, settings=cfg()
    )
    assert snap["queries"]["in_use_percent"] is None
    assert snap["queries"]["capacity"] == 0


# --- sustain logic and rules ---------------------------------------------------------


def series(values: list[float], *, worker: str = "w1", host: str = "api-1", **kw: Any):
    """One sample per 5 s ending at NOW."""
    n = len(values)
    return [
        (worker, host, NOW - timedelta(seconds=5 * (n - 1 - i)), sample(cpu=v, **kw))
        for i, v in enumerate(values)
    ]


def test_single_spike_never_alerts() -> None:
    rows = series([20.0] * 12 + [99.0])
    assert evaluate_resource_rows(rows, now=NOW, cfg=cfg()) == []


def test_sustained_cpu_alerts_with_severity() -> None:
    findings = evaluate_resource_rows(series([97.0] * 13), now=NOW, cfg=cfg())
    [cpu] = [f for f in findings if f.rule == c.CPU_SUSTAINED]
    assert cpu.severity == "critical"
    assert cpu.dedupe_key == "cpu_sustained:api-1"
    warn = evaluate_resource_rows(series([85.0] * 13), now=NOW, cfg=cfg())
    assert [f.severity for f in warn if f.rule == c.CPU_SUSTAINED] == ["warning"]


def test_window_must_be_covered() -> None:
    # Only the last 20 s of a 60 s window: not enough evidence.
    assert evaluate_resource_rows(series([99.0] * 4), now=NOW, cfg=cfg()) == []
    assert (
        sustained_min([(NOW, 99.0)], now=NOW, sustain_seconds=60, interval_seconds=5)
        is None
    )


def test_loop_lag_pool_and_memory_rules() -> None:
    rows = series([10.0] * 13, lag=1500.0, pool_used=10, mem=96.0)
    rules = {f.rule: f for f in evaluate_resource_rows(rows, now=NOW, cfg=cfg())}
    assert rules[c.EVENT_LOOP_LAG].severity == "critical"
    assert rules[c.DB_POOL_SATURATION].severity == "critical"
    assert rules[c.MEMORY_HIGH].severity == "critical"


def test_rss_growth_heuristic() -> None:
    settings = cfg(alert_rss_growth_mb=100.0, alert_rss_growth_min_samples=10)
    rising = [
        ("w1", "api-1", NOW - timedelta(seconds=5 * (19 - i)), sample(rss=100 + 10 * i))
        for i in range(20)
    ]
    rules = {f.rule for f in evaluate_resource_rows(rising, now=NOW, cfg=settings)}
    assert c.RSS_GROWTH in rules
    sawtooth = [
        (
            "w1",
            "api-1",
            NOW - timedelta(seconds=5 * (19 - i)),
            sample(rss=100 + (i % 2) * 300),
        )
        for i in range(20)
    ]
    rules = {f.rule for f in evaluate_resource_rows(sawtooth, now=NOW, cfg=settings)}
    assert c.RSS_GROWTH not in rules


# --- live stream ---------------------------------------------------------------------


async def test_stream_pushes_resources_to_subscribers() -> None:
    calls = []

    async def resources() -> dict[str, Any]:
        calls.append(1)
        return {"effective": {"cpu_percent": 12.5}, "n": len(calls)}

    async def overview(_aud: str) -> dict[str, Any]:
        return {"health": {"status": "Healthy"}, "incidents": [], "kpis": {}}

    hub = GovernanceStreamHub(
        snapshot_fn=overview,
        interval=60,
        bus=EventBus(dsn="postgresql://unused"),
        resources_fn=resources,
        resources_interval=0.05,
    )
    gen = hub.subscribe("overview")
    try:
        seen = []
        async with asyncio.timeout(5):
            while len([e for e in seen if e.event == "resources"]) < 2:
                seen.append(await anext(gen))
        res = [e for e in seen if e.event == "resources"]
        assert res[0].data["effective"]["cpu_percent"] == 12.5
        # A late subscriber immediately gets the latest resources snapshot.
        late = hub.subscribe("users")
        first = await asyncio.wait_for(anext(late), 5)
        assert first.event == "resources"
        await late.aclose()
    finally:
        await gen.aclose()
        await hub.aclose()


# --- database round trip + alert end to end ------------------------------------------


@contextlib.asynccontextmanager
async def _borrow(db: AsyncSession) -> AsyncIterator[AsyncSession]:
    yield db


async def test_samples_round_trip_and_raise_alert(db_session: AsyncSession) -> None:
    settings = cfg(alert_cpu_warning_pct=80.0)
    for i in range(13):
        await resource_store.record_sample(
            db_session,
            sample(cpu=90.0),
            now=NOW - timedelta(seconds=5 * (12 - i)),
            worker_id="e2e-worker",
            hostname="e2e-host",
        )
    snap = await resource_store.resources_snapshot(
        db_session, now=NOW, settings=settings
    )
    assert any(w["worker_id"] == "e2e-worker" for w in snap["workers"])
    alert_jobs.set_session_factory(lambda: _borrow(db_session))
    try:
        stats = await alert_jobs.evaluate_alerts(now=NOW, settings=settings)
        assert stats["failed_rules"] == 0
        alert = await db_session.scalar(
            select(Alert).where(Alert.dedupe_key == "cpu_sustained:e2e-host")
        )
        assert alert is not None and alert.status == "open"
        # The samples age out (worker gone): the alert resolves.
        await alert_jobs.evaluate_alerts(
            now=NOW + timedelta(hours=1), settings=settings
        )
        await db_session.refresh(alert)
        assert alert.status == "resolved"
    finally:
        alert_jobs.set_session_factory(None)
