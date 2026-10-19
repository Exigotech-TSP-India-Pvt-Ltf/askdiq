"""Per-process alert sampling (circuit breaker, Azure quota/429s, event bus, DB).

These signals live in each API process's memory, so every process samples
its own every ``ALERTS_SAMPLE_INTERVAL_SECONDS`` and raises through
``app.alerts.service`` (Postgres dedupe makes N processes one alert). The
``alerts.evaluate`` job resolves an alert no process re-reported for
``ALERTS_LOCAL_RESOLVE_MINUTES``.

Database outages cannot be stored, so they are emailed directly (once per
``ALERT_COOLDOWN_MINUTES`` per process) and recorded as a resolved alert
when the database is reachable again.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.alerts import catalogue as c
from app.alerts.catalogue import Severity
from app.alerts.email import (
    AlertView,
    Brand,
    build_message,
    render_alert_email,
    send_email,
)
from app.alerts.service import Finding, raise_alert, record_past_incident, utcnow
from app.core import database
from app.core.circuit import CircuitState, get_llm_breaker
from app.core.config import Settings, get_settings
from app.core.events import EventBus, get_event_bus
from app.core.logging_config import get_logger
from app.core.quota import quota_monitor
from app.governance.quota_series import utilization_pct

logger = get_logger(__name__)

_DB_PROBE_TIMEOUT_SECONDS = 5.0
_THROTTLE_CRITICAL_FACTOR = 5


def circuit_finding() -> Finding | None:
    breaker = get_llm_breaker()
    state = breaker.state
    if state is CircuitState.CLOSED:
        return None
    return Finding(
        rule=c.LLM_CIRCUIT_OPEN,
        dedupe_key=c.LLM_CIRCUIT_OPEN,
        severity=Severity.CRITICAL,
        title=f"LLM circuit breaker {state.value.replace('_', '-')}",
        summary=(
            "Azure OpenAI chat completions failed repeatedly, so the circuit "
            "breaker is failing calls fast; users get a fallback answer."
        ),
        component="Azure OpenAI",
        details={
            "value": state.value,
            "threshold": (
                f"{breaker.failure_threshold} failures in {breaker.window_seconds:.0f} s"
            ),
            "counts": {"reset_after_seconds": breaker.reset_timeout_seconds},
        },
    )


def quota_findings(summary: dict[str, Any], cfg: Settings) -> list[Finding]:
    findings: list[Finding] = []
    utilization = utilization_pct(summary)
    if utilization >= cfg.governance_health_quota_degraded_pct:
        critical = utilization >= cfg.governance_health_quota_incident_pct
        threshold = (
            cfg.governance_health_quota_incident_pct
            if critical
            else cfg.governance_health_quota_degraded_pct
        )
        findings.append(
            Finding(
                rule=c.AZURE_QUOTA,
                dedupe_key=c.AZURE_QUOTA,
                severity=Severity.CRITICAL if critical else Severity.WARNING,
                title=f"Azure OpenAI quota {utilization:.0f}% used",
                summary=(
                    f"The busiest Azure OpenAI deployment used {utilization:.0f}% of "
                    f"its tokens or requests per minute (alert at {threshold:.0f}%)."
                ),
                component="Azure OpenAI",
                details={
                    "value": round(utilization, 1),
                    "threshold": threshold,
                    "unit": "%",
                    "window": "last 60 seconds",
                    "counts": _quota_counts(summary),
                },
            )
        )
    throttled = sum(
        int((summary.get(k) or {}).get("azure_429_count_60s") or 0)
        for k in ("chat", "embedding")
    )
    if throttled >= cfg.alert_azure_429_threshold:
        critical = (
            throttled >= cfg.alert_azure_429_threshold * _THROTTLE_CRITICAL_FACTOR
        )
        findings.append(
            Finding(
                rule=c.AZURE_THROTTLING,
                dedupe_key=c.AZURE_THROTTLING,
                severity=Severity.CRITICAL if critical else Severity.WARNING,
                title=f"Azure OpenAI throttled {throttled} requests (429) in 60 s",
                summary=(
                    f"Azure OpenAI answered {throttled} requests with 429 Too Many "
                    "Requests in the last minute; users see slower or failed answers."
                ),
                component="Azure OpenAI",
                details={
                    "value": throttled,
                    "threshold": cfg.alert_azure_429_threshold,
                    "unit": "429s",
                    "window": "last 60 seconds",
                    "counts": _quota_counts(summary),
                },
            )
        )
    return findings


def _quota_counts(summary: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kind in ("chat", "embedding"):
        metrics = summary.get(kind) or {}
        out[f"{kind}_tpm"] = metrics.get("tpm_current")
        out[f"{kind}_rpm"] = metrics.get("rpm_current")
        out[f"{kind}_429s"] = metrics.get("azure_429_count_60s")
    return out


class AlertSampler:
    """Samples this process's signals until ``stop`` is set."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        bus: EventBus | None = None,
        clock: Callable[[], float] = time.monotonic,
        send: Callable[..., Any] | None = None,
    ) -> None:
        self.cfg = settings or get_settings()
        self.bus = bus or get_event_bus()
        self._clock = clock
        self._send = send or send_email
        self._bus_down_since: float | None = None
        self._db_down_since: datetime | None = None
        self._db_outage_emailed_at: float | None = None
        self._db_outage_emailed = False

    async def run(self, stop: asyncio.Event) -> None:
        interval = float(self.cfg.alerts_sample_interval_seconds)
        while not stop.is_set():
            try:
                await self.sample_once()
            except Exception as exc:
                logger.warning("alert_sample_failed", error_type=type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)

    def event_bus_finding(self) -> Finding | None:
        if not self.bus.started or self.bus.connected.is_set():
            self._bus_down_since = None
            return None
        now = self._clock()
        if self._bus_down_since is None:
            self._bus_down_since = now
        down_for = now - self._bus_down_since
        if down_for < self.cfg.alert_event_bus_grace_seconds:
            return None
        return Finding(
            rule=c.EVENT_BUS_DOWN,
            dedupe_key=c.EVENT_BUS_DOWN,
            severity=Severity.WARNING,
            title=f"Event bus disconnected for {down_for:.0f} s",
            summary=(
                "The Postgres LISTEN/NOTIFY connection is down: live dashboard "
                "updates and cross-worker cache invalidation are paused."
            ),
            component="Event bus",
            details={
                "value": round(down_for),
                "threshold": self.cfg.alert_event_bus_grace_seconds,
                "unit": "s",
                "counts": {"reconnects": self.bus.connect_count},
            },
        )

    def local_findings(self) -> list[Finding]:
        findings = quota_findings(quota_monitor.get_summary(), self.cfg)
        for finding in (circuit_finding(), self.event_bus_finding()):
            if finding is not None:
                findings.append(finding)
        return findings

    async def sample_once(self) -> list[Finding]:
        findings = self.local_findings()
        try:
            await asyncio.wait_for(probe_database(), _DB_PROBE_TIMEOUT_SECONDS)
        except Exception as exc:
            # Any failure of a bare ``SELECT 1`` means this process cannot
            # use the database (refused, DNS, auth, pool exhausted, timeout).
            await self._on_database_down(exc)
            return findings
        await self._on_database_up()
        if findings:
            async with database.AsyncSessionLocal() as db:
                for finding in findings:
                    await raise_alert(db, finding, settings=self.cfg)
                await db.commit()
        return findings

    def _outage_finding(
        self, error: str, extra: dict[str, Any] | None = None
    ) -> Finding:
        return Finding(
            rule=c.DATABASE_UNAVAILABLE,
            dedupe_key=c.DATABASE_UNAVAILABLE,
            severity=Severity.CRITICAL,
            title="Database unreachable",
            summary=(
                "The API cannot reach Postgres; requests that need the database "
                "are failing."
            ),
            component="Database",
            details={"last_error": error, **(extra or {})},
        )

    async def _on_database_down(self, exc: BaseException) -> None:
        now = utcnow()
        if self._db_down_since is None:
            self._db_down_since = now
            logger.error("alert_database_unreachable", error_type=type(exc).__name__)
        cooldown = self.cfg.alert_cooldown_minutes * 60
        mono = self._clock()
        due = self._db_outage_emailed_at is None or (
            mono - self._db_outage_emailed_at >= cooldown
        )
        if not (due and self.cfg.alert_email_configured):
            return
        finding = self._outage_finding(type(exc).__name__)
        view = AlertView(
            id="not stored (database unreachable)",
            rule=finding.rule,
            severity=finding.severity.value,
            status="open",
            title=finding.title,
            summary=finding.summary,
            component=finding.component,
            details=finding.details,
            occurrences=1,
            first_seen_at=self._db_down_since,
            last_seen_at=now,
        )
        brand = Brand(
            product_name=self.cfg.brand_name,
            assistant_name=self.cfg.brand_assistant_name,
            company_name=self.cfg.brand_company_name,
        )
        rendered = render_alert_email(view, brand, self.cfg, "opened")
        try:
            await self._send(build_message(rendered, self.cfg), self.cfg)
            self._db_outage_emailed_at = mono
            self._db_outage_emailed = True
        except Exception as send_exc:
            logger.warning(
                "alert_outage_email_failed", error_type=type(send_exc).__name__
            )

    async def _on_database_up(self) -> None:
        started = self._db_down_since
        if started is None:
            return
        now = utcnow()
        outage_s = round((now - started).total_seconds())
        finding = self._outage_finding(
            "recovered",
            {"value": outage_s, "unit": "s", "counts": {"outage_seconds": outage_s}},
        )
        async with database.AsyncSessionLocal() as db:
            await record_past_incident(
                db,
                finding,
                started_at=started,
                resolved_at=now,
                emailed=self._db_outage_emailed,
            )
            await db.commit()
        logger.info("alert_database_recovered", outage_s=outage_s)
        self._db_down_since = None
        self._db_outage_emailed = False
        self._db_outage_emailed_at = None


async def probe_database() -> None:
    async with database.AsyncSessionLocal() as db:
        await db.execute(text("SELECT 1"))
