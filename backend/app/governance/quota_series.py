"""Per-process Azure OpenAI quota time series for Monitoring (newplan §4.2).

``quota_monitor`` only keeps a rolling 60 s window. This keeps a bounded ring
of throttled snapshots (at most one per ``GOVERNANCE_QUOTA_SAMPLE_SECONDS``),
fed by ``/governance/monitoring`` reads and the SSE producer tick, so the UI
can chart headroom over time. It is process-local, like the source telemetry.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.core.quota import quota_monitor


def utilization_pct(summary: dict[str, Any]) -> float:
    """Worst TPM/RPM utilisation (100 - headroom) across chat and embedding."""
    worst = 0.0
    for key in ("chat", "embedding"):
        metrics = summary.get(key) or {}
        for headroom in ("tpm_headroom_pct", "rpm_headroom_pct"):
            value = metrics.get(headroom)
            if isinstance(value, (int, float)):
                worst = max(worst, 100.0 - float(value))
    return round(worst, 2)


class QuotaSeries:
    def __init__(self, points: int | None = None, every: float | None = None) -> None:
        settings = get_settings()
        self._every = every or settings.governance_quota_sample_seconds
        self._points: deque[dict[str, Any]] = deque(
            maxlen=points or settings.governance_quota_series_points
        )
        self._last = float("-inf")
        self._lock = threading.Lock()

    def sample(self, now: float | None = None) -> dict[str, Any]:
        """Record a snapshot if the interval elapsed; return the current summary."""
        summary = quota_monitor.get_summary()
        mono = time.monotonic() if now is None else now
        with self._lock:
            if mono - self._last >= self._every:
                self._last = mono
                chat = summary.get("chat") or {}
                embed = summary.get("embedding") or {}
                self._points.append(
                    {
                        "ts": datetime.now(UTC).isoformat(),
                        "utilization_pct": utilization_pct(summary),
                        "chat_tpm_headroom_pct": chat.get("tpm_headroom_pct"),
                        "chat_rpm_headroom_pct": chat.get("rpm_headroom_pct"),
                        "embedding_tpm_headroom_pct": embed.get("tpm_headroom_pct"),
                        "embedding_rpm_headroom_pct": embed.get("rpm_headroom_pct"),
                        "azure_429_count_60s": (chat.get("azure_429_count_60s") or 0)
                        + (embed.get("azure_429_count_60s") or 0),
                    }
                )
        return summary

    def points(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._points)


quota_series = QuotaSeries()
