"""Rolling 60-second in-memory Azure OpenAI quota telemetry (TPM/RPM monitor).

Tracks:
- Request velocity (RPM) and token velocity (TPM) per deployment (chat vs. embedding)
- Headroom percentages against configured Azure limits
- Azure rate limit response headers (x-ratelimit-remaining-tokens / requests)
- Throttling metrics: 429 counts and retry rates in rolling 60s window
- Single-worker per process model (Decision D4)
"""

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings
from app.model_settings.service import current_model_settings

_HEADER_FRESHNESS_SECONDS = 60.0
_HIGH_UTILIZATION_RATIO = 0.8


@dataclass
class DeploymentQuotaTracker:
    name: str
    tpm_limit: int
    rpm_limit: int
    # Rolling 60s history: (monotonic_timestamp, token_count, is_error, is_429, retry_count)
    history: deque = field(default_factory=deque)
    rolling_tokens: int = 0
    rolling_requests: int = 0
    rolling_errors: int = 0
    rolling_429s: int = 0
    rolling_retries: int = 0
    # Azure header snapshots
    last_header_remaining_tokens: int | None = None
    last_header_remaining_requests: int | None = None
    last_header_timestamp: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _prune(self, now: float) -> None:
        """Evict events older than 60 seconds."""
        cutoff = now - 60.0
        while self.history and self.history[0][0] < cutoff:
            _, tokens, is_err, is_429, retries = self.history.popleft()
            self.rolling_tokens = max(0, self.rolling_tokens - tokens)
            self.rolling_requests = max(0, self.rolling_requests - 1)
            if is_err:
                self.rolling_errors = max(0, self.rolling_errors - 1)
            if is_429:
                self.rolling_429s = max(0, self.rolling_429s - 1)
            self.rolling_retries = max(0, self.rolling_retries - retries)

    def record(
        self,
        tokens: int,
        is_error: bool = False,
        is_429: bool = False,
        retries: int = 0,
        headers: dict[str, str] | None = None,
        now: float | None = None,
    ) -> None:
        if now is None:
            now = time.monotonic()

        with self._lock:
            self._prune(now)
            self.history.append((now, tokens, is_error, is_429, retries))
            self.rolling_tokens += tokens
            self.rolling_requests += 1
            if is_error:
                self.rolling_errors += 1
            if is_429:
                self.rolling_429s += 1
            self.rolling_retries += retries

            if headers:
                for k, v in headers.items():
                    k_lower = k.lower()
                    if k_lower == "x-ratelimit-remaining-tokens":
                        try:
                            self.last_header_remaining_tokens = int(v)
                            self.last_header_timestamp = now
                        except (ValueError, TypeError):
                            pass
                    elif k_lower == "x-ratelimit-remaining-requests":
                        try:
                            self.last_header_remaining_requests = int(v)
                            self.last_header_timestamp = now
                        except (ValueError, TypeError):
                            pass

    def get_metrics(self, now: float | None = None) -> dict[str, Any]:
        if now is None:
            now = time.monotonic()

        with self._lock:
            self._prune(now)
            tpm = self.rolling_tokens
            rpm = self.rolling_requests
            errors = self.rolling_errors
            throttle_429s = self.rolling_429s
            retries = self.rolling_retries

            # Local calculated headroom
            tpm_util = min(1.0, tpm / self.tpm_limit) if self.tpm_limit > 0 else 0.0
            rpm_util = min(1.0, rpm / self.rpm_limit) if self.rpm_limit > 0 else 0.0

            local_tpm_headroom = round(max(0.0, 100.0 * (1.0 - tpm_util)), 2)
            local_rpm_headroom = round(max(0.0, 100.0 * (1.0 - rpm_util)), 2)

            # Prefer fresh Azure header if present (within 60s)
            tpm_headroom = local_tpm_headroom
            rpm_headroom = local_rpm_headroom
            header_active = False

            if (
                self.last_header_timestamp is not None
                and (now - self.last_header_timestamp) < _HEADER_FRESHNESS_SECONDS
            ):
                header_active = True
                if self.last_header_remaining_tokens is not None and self.tpm_limit > 0:
                    header_tpm_headroom = round(
                        max(
                            0.0,
                            100.0
                            * (self.last_header_remaining_tokens / self.tpm_limit),
                        ),
                        2,
                    )
                    tpm_headroom = min(local_tpm_headroom, header_tpm_headroom)
                if (
                    self.last_header_remaining_requests is not None
                    and self.rpm_limit > 0
                ):
                    header_rpm_headroom = round(
                        max(
                            0.0,
                            100.0
                            * (self.last_header_remaining_requests / self.rpm_limit),
                        ),
                        2,
                    )
                    rpm_headroom = min(local_rpm_headroom, header_rpm_headroom)

            error_rate = round(errors / rpm, 4) if rpm > 0 else 0.0
            high_utilization = (tpm_util > _HIGH_UTILIZATION_RATIO) or (
                rpm_util > _HIGH_UTILIZATION_RATIO
            )

            return {
                "deployment": self.name,
                "tpm_current": tpm,
                "tpm_limit": self.tpm_limit,
                "tpm_headroom_pct": tpm_headroom,
                "rpm_current": rpm,
                "rpm_limit": self.rpm_limit,
                "rpm_headroom_pct": rpm_headroom,
                "azure_429_count_60s": throttle_429s,
                "azure_retries_60s": retries,
                "azure_error_rate": error_rate,
                "high_utilization_warning": high_utilization,
                "header_telemetry_active": header_active,
            }


class QuotaMonitor:
    def __init__(self) -> None:
        self._trackers: dict[str, DeploymentQuotaTracker] = {}
        self._lock = threading.Lock()

    def get_tracker(self, deployment: str) -> DeploymentQuotaTracker:
        with self._lock:
            if deployment not in self._trackers:
                settings = get_settings()
                if "embed" in deployment.lower():
                    tpm_lim = settings.azure_openai_embedding_tpm_limit
                    rpm_lim = settings.azure_openai_embedding_rpm_limit
                else:
                    tpm_lim = settings.azure_openai_tpm_limit
                    rpm_lim = settings.azure_openai_rpm_limit
                self._trackers[deployment] = DeploymentQuotaTracker(
                    name=deployment,
                    tpm_limit=tpm_lim,
                    rpm_limit=rpm_lim,
                )
            return self._trackers[deployment]

    def record_chat(
        self,
        tokens: int,
        is_error: bool = False,
        is_429: bool = False,
        retries: int = 0,
        headers: dict[str, str] | None = None,
        now: float | None = None,
    ) -> None:
        tracker = self.get_tracker(current_model_settings().llm_deployment)
        tracker.record(tokens, is_error, is_429, retries, headers, now)

    def record_embedding(
        self,
        tokens: int,
        is_error: bool = False,
        is_429: bool = False,
        retries: int = 0,
        headers: dict[str, str] | None = None,
        now: float | None = None,
    ) -> None:
        tracker = self.get_tracker(current_model_settings().embedding_model)
        tracker.record(tokens, is_error, is_429, retries, headers, now)

    def get_summary(self, now: float | None = None) -> dict[str, Any]:
        models = current_model_settings()
        chat_tracker = self.get_tracker(models.llm_deployment)
        embed_tracker = self.get_tracker(models.embedding_model)
        chat_m = chat_tracker.get_metrics(now)
        embed_m = embed_tracker.get_metrics(now)

        return {
            "chat": chat_m,
            "embedding": embed_m,
            "any_high_utilization": (
                chat_m["high_utilization_warning"]
                or embed_m["high_utilization_warning"]
            ),
        }


# Global process singleton
quota_monitor = QuotaMonitor()
