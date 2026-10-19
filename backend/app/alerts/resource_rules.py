"""Resource alert rules over ``resource_samples`` (all workers, all replicas).

A condition must hold for the whole ``ALERT_RESOURCE_SUSTAIN_SECONDS``
window, with enough samples to cover it, so a single spike never alerts:

- CPU (container when limited, else host) per host,
- memory (container when limited, else host) per host,
- event-loop lag p95 per worker,
- DB pool saturation per worker,
- RSS growth (leak heuristic) per worker over the history window.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import catalogue as c
from app.alerts.catalogue import Severity
from app.alerts.service import Finding
from app.core.config import Settings
from app.governance.resource_store import Row, load_rows

# At least this share of the expected samples must exist in the window.
_MIN_COVERAGE = 0.6
# Leak heuristic: share of sample-to-sample RSS deltas that must be >= 0.
_MONOTONIC_SHARE = 0.9


def _value(sample: dict[str, Any], *path: str) -> float | None:
    value: Any = sample
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return float(value) if isinstance(value, int | float) else None


def pool_percent(sample: dict[str, Any]) -> float | None:
    """Checked-out share of the worker's pool capacity (size + overflow)."""
    used = _value(sample, "db_pool", "checked_out")
    size = _value(sample, "db_pool", "size") or 0.0
    overflow = _value(sample, "db_pool", "max_overflow") or 0.0
    if used is None or size + overflow <= 0:
        return None
    return used * 100 / (size + overflow)


def sustained_min(
    points: list[tuple[datetime, float | None]],
    *,
    now: datetime,
    sustain_seconds: float,
    interval_seconds: float,
) -> float | None:
    """Lowest value over the sustain window, or None if the window is not
    fully covered (too few samples, a gap at the start, or a missing value).
    ``>= threshold`` on the result means "above the threshold the whole time"."""
    start = now - timedelta(seconds=sustain_seconds)
    window = [(ts, v) for ts, v in points if ts >= start]
    expected = max(2, int(sustain_seconds / interval_seconds))
    if len(window) < max(2, int(expected * _MIN_COVERAGE)):
        return None
    earliest = min(ts for ts, _ in window)
    if (earliest - start).total_seconds() > max(
        2 * interval_seconds, 0.25 * sustain_seconds
    ):
        return None
    values = [v for _, v in window]
    if any(v is None for v in values):
        return None
    return min(v for v in values if v is not None)


def _group(rows: list[Row], key: Callable[[Row], str]) -> dict[str, list[Row]]:
    groups: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return groups


def _level(value: float | None, warning: float, critical: float) -> Severity | None:
    if value is None:
        return None
    if value >= critical:
        return Severity.CRITICAL
    if value >= warning:
        return Severity.WARNING
    return None


def _scope_of(rows: list[Row], key: str) -> str:
    return str(rows[-1][3].get("effective", {}).get(key) or "host")


def evaluate_resource_rows(
    rows: list[Row], *, now: datetime, cfg: Settings
) -> list[Finding]:
    """Pure evaluation (tests feed synthetic rows)."""
    sustain = float(cfg.alert_resource_sustain_seconds)
    interval = float(cfg.metrics_sample_interval_seconds)
    window_label = f"sustained {int(sustain)} s"
    findings: list[Finding] = []

    def sustain_of(group: list[Row], *path: str) -> float | None:
        return sustained_min(
            [(r[2], _value(r[3], *path)) for r in group],
            now=now,
            sustain_seconds=sustain,
            interval_seconds=interval,
        )

    for host, group in _group(rows, lambda r: r[1]).items():
        cpu = sustain_of(group, "effective", "cpu_percent")
        severity = _level(cpu, cfg.alert_cpu_warning_pct, cfg.alert_cpu_critical_pct)
        if severity is not None and cpu is not None:
            scope = _scope_of(group, "cpu_scope")
            threshold = (
                cfg.alert_cpu_critical_pct
                if severity is Severity.CRITICAL
                else cfg.alert_cpu_warning_pct
            )
            findings.append(
                Finding(
                    rule=c.CPU_SUSTAINED,
                    dedupe_key=f"{c.CPU_SUSTAINED}:{host}",
                    severity=severity,
                    title=f"{scope.capitalize()} CPU at least {cpu:.0f}% for {int(sustain)} s on {host}",
                    summary=(
                        f"{scope.capitalize()} CPU on {host} stayed at or above "
                        f"{threshold:.0f}% for {int(sustain)} seconds (lowest "
                        f"sample {cpu:.0f}%). Answers slow down and requests queue."
                    ),
                    component="Infrastructure",
                    details={
                        "value": round(cpu, 1),
                        "threshold": threshold,
                        "unit": "%",
                        "window": window_label,
                        "counts": {"samples": len(group), "scope": scope},
                    },
                )
            )
        mem = sustain_of(group, "effective", "memory_percent")
        severity = _level(
            mem, cfg.alert_memory_warning_pct, cfg.alert_memory_critical_pct
        )
        if severity is not None and mem is not None:
            scope = _scope_of(group, "memory_scope")
            threshold = (
                cfg.alert_memory_critical_pct
                if severity is Severity.CRITICAL
                else cfg.alert_memory_warning_pct
            )
            findings.append(
                Finding(
                    rule=c.MEMORY_HIGH,
                    dedupe_key=f"{c.MEMORY_HIGH}:{host}",
                    severity=severity,
                    title=f"{scope.capitalize()} memory at least {mem:.0f}% on {host}",
                    summary=(
                        f"{scope.capitalize()} memory on {host} stayed at or above "
                        f"{threshold:.0f}% for {int(sustain)} seconds; the "
                        "container can be OOM-killed if it keeps growing."
                    ),
                    component="Infrastructure",
                    details={
                        "value": round(mem, 1),
                        "threshold": threshold,
                        "unit": "%",
                        "window": window_label,
                        "counts": {"samples": len(group), "scope": scope},
                    },
                )
            )

    for worker, group in _group(rows, lambda r: r[0]).items():
        lag = sustain_of(group, "loop", "p95_ms")
        severity = _level(
            lag, cfg.alert_loop_lag_warning_ms, cfg.alert_loop_lag_critical_ms
        )
        if severity is not None and lag is not None:
            findings.append(
                Finding(
                    rule=c.EVENT_LOOP_LAG,
                    dedupe_key=f"{c.EVENT_LOOP_LAG}:{worker}",
                    severity=severity,
                    title=f"Event-loop lag at least {lag:.0f} ms on worker {worker}",
                    summary=(
                        f"The worker's event loop woke up at least {lag:.0f} ms late "
                        f"(p95) for {int(sustain)} seconds: CPU-bound or blocking "
                        "work is delaying every request on it."
                    ),
                    component="API worker",
                    details={
                        "value": round(lag),
                        "threshold": cfg.alert_loop_lag_critical_ms
                        if severity is Severity.CRITICAL
                        else cfg.alert_loop_lag_warning_ms,
                        "unit": "ms",
                        "window": window_label,
                        "counts": {"samples": len(group)},
                    },
                )
            )
        pool = sustained_min(
            [(r[2], pool_percent(r[3])) for r in group],
            now=now,
            sustain_seconds=sustain,
            interval_seconds=interval,
        )
        if pool is not None and pool >= cfg.alert_db_pool_saturation_pct:
            findings.append(
                Finding(
                    rule=c.DB_POOL_SATURATION,
                    dedupe_key=f"{c.DB_POOL_SATURATION}:{worker}",
                    severity=Severity.CRITICAL if pool >= 100 else Severity.WARNING,  # noqa: PLR2004
                    title=f"DB pool {pool:.0f}% in use on worker {worker}",
                    summary=(
                        f"At least {pool:.0f}% of the worker's database connections "
                        f"were checked out for {int(sustain)} seconds; new requests "
                        "wait for a connection (DB_POOL_SIZE / DB_MAX_OVERFLOW)."
                    ),
                    component="Database",
                    details={
                        "value": round(pool, 1),
                        "threshold": cfg.alert_db_pool_saturation_pct,
                        "unit": "%",
                        "window": window_label,
                        "counts": {
                            "checked_out": _value(
                                group[-1][3], "db_pool", "checked_out"
                            ),
                            "pool_size": _value(group[-1][3], "db_pool", "size"),
                        },
                    },
                )
            )
        growth = rss_growth(group, cfg)
        if growth is not None:
            findings.append(
                Finding(
                    rule=c.RSS_GROWTH,
                    dedupe_key=f"{c.RSS_GROWTH}:{worker}",
                    severity=Severity.WARNING,
                    title=f"Worker memory grew {growth[0]:.0f} MB without dropping",
                    summary=(
                        f"Worker {worker} RSS rose from {growth[1]:.0f} MB to "
                        f"{growth[2]:.0f} MB over {len(group)} samples, almost "
                        "never decreasing: a possible memory leak."
                    ),
                    component="API worker",
                    details={
                        "value": round(growth[0]),
                        "threshold": cfg.alert_rss_growth_mb,
                        "unit": "MB",
                        "window": f"last {cfg.metrics_history_minutes} minutes",
                        "counts": {
                            "samples": len(group),
                            "rss_start_mb": growth[1],
                            "rss_end_mb": growth[2],
                        },
                    },
                )
            )
    return findings


def rss_growth(group: list[Row], cfg: Settings) -> tuple[float, float, float] | None:
    """(growth, start, end) MB when RSS rose steadily by at least the threshold."""
    values = [
        v for v in (_value(r[3], "process", "rss_mb") for r in group) if v is not None
    ]
    if len(values) < cfg.alert_rss_growth_min_samples:
        return None
    growth = values[-1] - values[0]
    if growth < cfg.alert_rss_growth_mb:
        return None
    deltas = [b - a for a, b in zip(values, values[1:], strict=False)]
    rising = sum(1 for d in deltas if d >= 0) / len(deltas)
    if rising < _MONOTONIC_SHARE:
        return None
    return growth, values[0], values[-1]


async def resource_rule(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    rows = await load_rows(db, now - timedelta(minutes=cfg.metrics_history_minutes))
    return evaluate_resource_rows(rows, now=now, cfg=cfg)
