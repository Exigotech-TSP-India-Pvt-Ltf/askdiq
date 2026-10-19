"""Alert job handlers (registered on import by ``app.jobs.handlers``).

- ``alerts.evaluate``: runs every database rule, raises / refreshes their
  alerts, resolves the ones whose condition cleared and process-sampled ones
  nobody re-reported, and prunes old resolved alerts. Each rule commits on
  its own, so one failing rule never hides the others.
- ``alerts.notify``: renders and sends one alert email. A failure is
  recorded on the alert row and re-raised, so the job queue retries it with
  exponential backoff up to ``ALERT_EMAIL_MAX_ATTEMPTS``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import service
from app.alerts.catalogue import DATABASE_RULES, PROCESS_RULES, NotifyStatus
from app.alerts.email import (
    AlertView,
    Brand,
    build_message,
    describe_error,
    render_alert_email,
    send_email,
    utc_key,
)
from app.alerts.rules import DATABASE_RULE_FUNCS
from app.branding.service import get_branding
from app.core import database
from app.core.config import Settings, get_settings
from app.core.logging_config import get_logger
from app.jobs.registry import job_handler
from app.models.db_models import Alert

logger = get_logger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class _Factory:
    """Session factory indirection (tests route it to their database)."""

    session: SessionFactory | None = None


def _session() -> AbstractAsyncContextManager[AsyncSession]:
    factory = _Factory.session or database.AsyncSessionLocal
    return factory()


def set_session_factory(factory: SessionFactory | None) -> None:
    _Factory.session = factory


async def evaluate_alerts(
    *, now: Any = None, settings: Settings | None = None
) -> dict[str, int]:
    """One evaluation pass; returns counters for the log line and tests."""
    cfg = settings or get_settings()
    now = now or service.utcnow()
    present: set[str] = set()
    evaluated: set[str] = set()
    stats = {"raised": 0, "failed_rules": 0, "resolved": 0, "pruned": 0}
    for rule_id, rule in DATABASE_RULE_FUNCS.items():
        try:
            async with _session() as db:
                findings = await rule(db, now, cfg)
                for finding in findings:
                    await service.raise_alert(db, finding, now=now, settings=cfg)
                    present.add(finding.dedupe_key)
                await db.commit()
            evaluated.add(rule_id)
            stats["raised"] += len(findings)
        except Exception as exc:
            stats["failed_rules"] += 1
            logger.warning(
                "alert_rule_failed", rule=rule_id, error_type=type(exc).__name__
            )
    async with _session() as db:
        # Only rules that ran cleanly may resolve their alerts.
        stats["resolved"] += await service.resolve_missing(
            db, evaluated & DATABASE_RULES, present, now=now, settings=cfg
        )
        stale_before = now - timedelta(minutes=cfg.alerts_local_resolve_minutes)
        stats["resolved"] += await service.resolve_stale(
            db, PROCESS_RULES, stale_before, now=now, settings=cfg
        )
        stats["pruned"] = await service.prune_resolved(db, now=now, settings=cfg)
        await db.commit()
    logger.info("alerts_evaluated", **stats)
    return stats


@job_handler(service.EVALUATE_JOB)
async def evaluate_alerts_task(_payload: dict[str, Any]) -> None:
    if not get_settings().alerts_enabled:
        return
    await evaluate_alerts()


async def load_brand() -> Brand:
    """Branding for emails (config defaults if the branding row cannot be read)."""
    branding = await get_branding(database.AsyncSessionLocal)
    return Brand(
        product_name=branding.product_name,
        assistant_name=branding.assistant_name,
        company_name=branding.company_name,
        primary_hex=branding.primary_hex,
        support_email=branding.support_email,
    )


_LOCAL_TIME_SQL = text(
    "SELECT to_char(CAST(:ts AS timestamptz), 'YYYY-MM-DD HH24:MI TZ')"
)


async def local_times(
    db: AsyncSession, moments: list[datetime], tz_name: str
) -> dict[str, str]:
    """Local wall times computed by Postgres (its IANA tz database is always
    present, unlike the OS one on Windows). Empty on any failure."""
    try:
        await db.execute(
            text("SELECT set_config('TimeZone', :tz, true)"), {"tz": tz_name}
        )
        out = {}
        for moment in moments:
            value = await db.scalar(_LOCAL_TIME_SQL, {"ts": moment})
            if value:
                out[utc_key(moment)] = str(value)
        return out
    except Exception as exc:
        logger.info("alert_local_time_unavailable", error_type=type(exc).__name__)
        return {}


async def _update(alert_id: uuid.UUID, **values: Any) -> None:
    async with _session() as db:
        alert = await db.get(Alert, alert_id, with_for_update=True)
        if alert is None:
            return
        for key, value in values.items():
            setattr(alert, key, value)
        await db.commit()


async def deliver_alert_email(
    alert_id: uuid.UUID,
    event: str,
    *,
    settings: Settings | None = None,
    brand: Brand | None = None,
) -> str:
    """Send one alert email; returns the resulting ``notify_status``."""
    cfg = settings or get_settings()
    async with _session() as db:
        alert = await db.get(Alert, alert_id)
        if alert is None:
            return "missing"
        view = AlertView.from_row(alert)
        attempts = int(alert.notify_attempts or 0) + 1
        view = replace(
            view, local_times=await local_times(db, view.moments(), cfg.alert_timezone)
        )
    if not cfg.alert_email_configured:
        await _update(alert_id, notify_status=NotifyStatus.DISABLED.value)
        return NotifyStatus.DISABLED.value
    rendered = render_alert_email(view, brand or await load_brand(), cfg, event)
    try:
        await send_email(build_message(rendered, cfg), cfg)
    except Exception as exc:
        final = attempts >= cfg.alert_email_max_attempts
        status = NotifyStatus.FAILED if final else NotifyStatus.RETRYING
        await _update(
            alert_id,
            notify_attempts=attempts,
            notify_error=describe_error(exc),
            **(
                {}
                if event == service.EVENT_RESOLVED
                else {"notify_status": status.value}
            ),
        )
        logger.warning(
            "alert_email_failed",
            alert_id=str(alert_id),
            attempt=attempts,
            final=final,
            error=describe_error(exc),
        )
        raise
    now = service.utcnow()
    if event == service.EVENT_RESOLVED:
        await _update(alert_id, resolved_notified_at=now, notify_error=None)
    else:
        await _update(
            alert_id,
            notify_status=NotifyStatus.SENT.value,
            notify_attempts=attempts,
            notified_at=now,
            notify_error=None,
        )
    logger.info("alert_email_sent", alert_id=str(alert_id), alert_event=event)
    return NotifyStatus.SENT.value


@job_handler(service.NOTIFY_JOB)
async def notify_alert_task(payload: dict[str, Any]) -> None:
    try:
        alert_id = uuid.UUID(str(payload.get("alert_id")))
    except ValueError:
        logger.warning("alert_notify_bad_payload")
        return
    event = str(payload.get("event") or service.EVENT_OPENED)
    if event not in service.NOTIFY_EVENTS:
        logger.warning("alert_notify_bad_event", alert_event=event)
        return
    await deliver_alert_email(alert_id, event)
