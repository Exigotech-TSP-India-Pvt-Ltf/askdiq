"""Resource telemetry: host, container (cgroup) and per-worker process metrics.

Every API worker runs one ``ResourceProbe`` (driven by
``app.governance.metrics_sampler``) every ``METRICS_SAMPLE_INTERVAL_SECONDS``.
Each value is labelled with its scope:

- ``host``      the whole machine (psutil); on Docker this is the server.
- ``container`` the cgroup the process runs in (Docker / Kubernetes limits),
                read from ``/sys/fs/cgroup`` (v2, with v1 fallbacks). Absent
                outside a container (Windows / bare dev).
- ``process``   this worker only (RSS, CPU, event-loop lag, DB pool).

CPU percentages are computed from cumulative counters (``cpu_times`` and the
cgroup ``usage_usec``) between two samples of this probe, so they do not
depend on how often the API is called (``psutil.cpu_percent(None)`` measured
"since the previous call by anyone").
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CGROUP_ROOT = Path("/sys/fs/cgroup")
# cgroup v1 reports "no limit" as a huge number (PAGE_COUNTER_MAX * page size).
_V1_UNLIMITED = 1 << 60
_BYTES_PER_MB = 1024 * 1024
_LAG_PROBE_SECONDS = 0.25
_LAG_WINDOW = 400


@dataclass(frozen=True, slots=True)
class CgroupStats:
    """One read of the process's cgroup (``None`` = not available)."""

    version: int | None
    cpu_limit_cores: float | None
    cpu_usage_usec: int | None
    memory_bytes: int | None
    memory_limit_bytes: int | None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None


def _int(text: str | None) -> int | None:
    try:
        return int(text) if text not in (None, "", "max") else None
    except ValueError:
        return None


def _v2_cpu_limit(text: str | None) -> float | None:
    """``cpu.max``: "<quota> <period>" or "max <period>" (no limit)."""
    if not text:
        return None
    parts = text.split()
    quota, period = _int(parts[0]), _int(parts[1]) if len(parts) > 1 else 100_000
    if quota is None or not period:
        return None
    return quota / period


def _v2_usage_usec(text: str | None) -> int | None:
    for line in (text or "").splitlines():
        key, _, value = line.partition(" ")
        if key == "usage_usec":
            return _int(value)
    return None


def _first_existing(root: Path, *names: str) -> Path | None:
    for name in names:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def read_cgroup(root: Path = CGROUP_ROOT) -> CgroupStats:
    """cgroup v2 (unified) or v1 counters for this container; empty if none."""
    if (root / "cgroup.controllers").exists():
        limit = _int(_read(root / "memory.max"))
        return CgroupStats(
            version=2,
            cpu_limit_cores=_v2_cpu_limit(_read(root / "cpu.max")),
            cpu_usage_usec=_v2_usage_usec(_read(root / "cpu.stat")),
            memory_bytes=_int(_read(root / "memory.current")),
            memory_limit_bytes=limit,
        )
    cpu_dir = _first_existing(root, "cpu,cpuacct", "cpu")
    acct_dir = _first_existing(root, "cpu,cpuacct", "cpuacct")
    mem_dir = _first_existing(root, "memory")
    if cpu_dir is None and acct_dir is None and mem_dir is None:
        return CgroupStats(None, None, None, None, None)
    limit_cores = None
    if cpu_dir is not None:
        quota = _int(_read(cpu_dir / "cpu.cfs_quota_us"))
        period = _int(_read(cpu_dir / "cpu.cfs_period_us"))
        if quota is not None and quota > 0 and period:
            limit_cores = quota / period
    usage_ns = _int(_read(acct_dir / "cpuacct.usage")) if acct_dir else None
    mem_limit = _int(_read(mem_dir / "memory.limit_in_bytes")) if mem_dir else None
    if mem_limit is not None and mem_limit >= _V1_UNLIMITED:
        mem_limit = None
    return CgroupStats(
        version=1,
        cpu_limit_cores=limit_cores,
        cpu_usage_usec=usage_ns // 1000 if usage_ns is not None else None,
        memory_bytes=_int(_read(mem_dir / "memory.usage_in_bytes"))
        if mem_dir
        else None,
        memory_limit_bytes=mem_limit,
    )


def _pct(value: float, total: float) -> float | None:
    return round(min(100.0, max(0.0, value * 100.0 / total)), 2) if total > 0 else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]


class LoopLagMonitor:
    """Event-loop lag: how late a short ``sleep`` wakes up (ms)."""

    def __init__(self, probe_seconds: float = _LAG_PROBE_SECONDS) -> None:
        self.probe_seconds = probe_seconds
        self._lags: deque[float] = deque(maxlen=_LAG_WINDOW)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="loop-lag-monitor")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def record(self, lag_ms: float) -> None:
        self._lags.append(max(0.0, lag_ms))

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            await asyncio.sleep(self.probe_seconds)
            self.record((loop.time() - started - self.probe_seconds) * 1000)

    def drain(self) -> dict[str, float | None]:
        """p95 / max lag since the previous drain (then forgets them)."""
        values = list(self._lags)
        self._lags.clear()
        return {
            "p95_ms": round(_p95(values), 1) if values else None,
            "max_ms": round(max(values), 1) if values else None,
        }


class ResourceProbe:
    """Computes one sample from counter deltas since the previous sample."""

    def __init__(
        self,
        *,
        cgroup_root: Path = CGROUP_ROOT,
        clock: Any = time.monotonic,
        psutil_module: Any = None,
    ) -> None:
        self._root = cgroup_root
        self._clock = clock
        self._psutil = psutil_module if psutil_module is not None else _psutil()
        self._prev: dict[str, Any] | None = None

    def _host(self) -> dict[str, Any]:
        ps = self._psutil
        if ps is None:
            return {}
        times = ps.cpu_times()
        mem = ps.virtual_memory()
        proc = ps.Process(os.getpid())
        ptimes = proc.cpu_times()
        return {
            "cpu_busy": sum(times) - times.idle - getattr(times, "iowait", 0.0),
            "cpu_total": sum(times),
            "cpu_count": ps.cpu_count() or 1,
            "mem_total": int(mem.total),
            "mem_used_pct": float(mem.percent),
            "proc_cpu": float(ptimes.user + ptimes.system),
            "proc_rss": int(proc.memory_info().rss),
        }

    def sample(self) -> dict[str, Any]:
        """Blocking (reads /proc and /sys); run it in a thread."""
        now = self._clock()
        host = self._host()
        cg = read_cgroup(self._root)
        prev, self._prev = self._prev, {"t": now, "host": host, "cg": cg}
        wall = (now - prev["t"]) if prev else 0.0

        host_cpu = proc_cpu = container_cpu = None
        if prev and host and prev["host"]:
            total = host["cpu_total"] - prev["host"]["cpu_total"]
            host_cpu = _pct(host["cpu_busy"] - prev["host"]["cpu_busy"], total)
            if wall > 0:
                # Percent of ONE core (like `top`): 250% = 2.5 cores busy.
                proc_cpu = round(
                    max(0.0, host["proc_cpu"] - prev["host"]["proc_cpu"]) * 100 / wall,
                    2,
                )
        cores = cg.cpu_limit_cores or float(
            host.get("cpu_count") or os.cpu_count() or 1
        )
        if (
            prev
            and wall > 0
            and cg.cpu_usage_usec is not None
            and prev["cg"].cpu_usage_usec is not None
        ):
            used = cg.cpu_usage_usec - prev["cg"].cpu_usage_usec
            container_cpu = _pct(used / 1e6, wall * cores)

        host_total = host.get("mem_total") or 0
        mem_limit = cg.memory_limit_bytes
        limited = bool(mem_limit) and (not host_total or mem_limit < host_total)
        container = None
        if cg.version is not None:
            container = {
                "cgroup_version": cg.version,
                "cpu_percent": container_cpu,
                "cpu_limit_cores": cg.cpu_limit_cores,
                "memory_mb": round(cg.memory_bytes / _BYTES_PER_MB, 1)
                if cg.memory_bytes is not None
                else None,
                "memory_limit_mb": round(mem_limit / _BYTES_PER_MB, 1)
                if limited
                else None,
                "memory_percent": _pct(cg.memory_bytes, mem_limit)
                if limited and cg.memory_bytes is not None
                else None,
            }
        rss = host.get("proc_rss")
        cpu_scope = (
            "container"
            if container and container_cpu is not None and cg.cpu_limit_cores
            else "host"
        )
        mem_scope = (
            "container"
            if container and container.get("memory_percent") is not None
            else "host"
        )
        return {
            "host": {
                "cpu_percent": host_cpu,
                "cpu_count": host.get("cpu_count"),
                "memory_percent": host.get("mem_used_pct"),
                "memory_total_mb": round(host_total / _BYTES_PER_MB)
                if host_total
                else None,
            },
            "container": container,
            "process": {
                "pid": os.getpid(),
                "rss_mb": round(rss / _BYTES_PER_MB, 1) if rss is not None else None,
                "cpu_percent": proc_cpu,
            },
            # The values gauges and alerts use: the container's when it is
            # limited, otherwise the host's.
            "effective": {
                "cpu_percent": container_cpu if cpu_scope == "container" else host_cpu,
                "cpu_scope": cpu_scope,
                "memory_percent": (
                    container["memory_percent"]
                    if mem_scope == "container" and container
                    else host.get("mem_used_pct")
                ),
                "memory_scope": mem_scope,
            },
        }


def _psutil() -> Any:
    try:
        import psutil  # noqa: PLC0415 - optional at runtime

        return psutil
    except ImportError:  # pragma: no cover - psutil is a pinned dependency
        return None
