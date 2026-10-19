"""Alert persistence and lifecycle (raise, dedupe, resolve, acknowledge).

Multi-worker safety comes from Postgres alone: ``raise_alert`` locks the
active row for a ``dedupe_key`` (``SELECT ... FOR UPDATE``) or inserts it with
``ON CONFLICT DO NOTHING`` against the partial unique index, so concurrent
raisers coalesce into one alert. Email is queued through the jobs table in
the same transaction (``enqueue_in``), so it exists exactly when the alert
row does and inherits the queue's retries with backoff.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.catalogue import (
    AlertStatus,
    NotifyStatus,
    Severity,
    severity_rank,
)
from app.core.config import Settings, get_settings
from app.core.logging_config import get_logger
from app.governance.audit import mask_detail
from app.jobs.queue import enqueue_in
from app.jobs.registry import ALERTS_EVALUATE_JOB
from app.models.db_models import ALERT_ACTIVE_STATUSES, Alert

logger = get_logger(__name__)

EVALUATE_JOB = ALERTS_EVALUATE_JOB
NOTIFY_JOB = "alerts.notify"
SYSTEM_ACTOR = "system"

EVENT_OPENED = "opened"
EVENT_ESCALATED = "escalated"
EVENT_UPDATED = "updated"
EVENT_RESOLVED = "resolved"
NOTIFY_EVENTS = frozenset({EVENT_OPENED, EVENT_ESCALATED, EVENT_RESOLVED})

_MAX_TITLE = 300
_MAX_KEY = 200


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Finding:
    """A condition a rule found to hold right now."""

    rule: str
    dedupe_key: str
    severity: Severity
    title: str
    summary: str
    component: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RaiseResult:
    alert: Alert
    event: str


def _active(key: str) -> Any:
    return and_(Alert.dedupe_key == key, Alert.status.in_(ALERT_ACTIVE_STATUSES))


def _safe_details(details: dict[str, Any]) -> dict[str, Any]:
    """PII-masked, length-capped copy (``mask_detail``) safe to store and email."""
    masked = mask_detail(details)
    return masked if isinstance(masked, dict) else {}


async def raise_alert(
    db: AsyncSession,
    finding: Finding,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> RaiseResult:
    """Open (or refresh) the alert for ``finding``; queue its email if due.

    Does not commit: the caller owns the transaction.
    """
    cfg = settings or get_settings()
    now = now or utcnow()
    key = finding.dedupe_key[:_MAX_KEY]
    details = _safe_details(finding.details)
    existing = await _lock_active(db, key)
    if existing is None:
        stmt = (
            insert(Alert)
            .values(
                id=uuid.uuid4(),
                rule=finding.rule,
                dedupe_key=key,
                severity=str(finding.severity),
                status=AlertStatus.OPEN.value,
                title=finding.title[:_MAX_TITLE],
                summary=finding.summary,
                component=finding.component,
                details=details,
                occurrences=1,
                first_seen_at=now,
                last_seen_at=now,
                notify_status=NotifyStatus.PENDING.value,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[Alert.dedupe_key],
                index_where=Alert.status.in_(ALERT_ACTIVE_STATUSES),
            )
            .returning(Alert.id)
        )
        new_id = await db.scalar(stmt)
        if new_id is not None:
            alert = await db.get(Alert, new_id, populate_existing=True)
            if alert is None:  # pragma: no cover - inserted in this transaction
                raise RuntimeError(f"alert {key!r} missing after insert")
            await _schedule_notification(db, alert, EVENT_OPENED, now, cfg)
            logger.warning(
                "alert_opened",
                rule=alert.rule,
                severity=alert.severity,
                dedupe_key=key,
            )
            return RaiseResult(alert, EVENT_OPENED)
        # Another worker inserted it first: refresh that row instead.
        existing = await _lock_active(db, key)
        if existing is None:  # resolved in between; nothing to refresh
            raise RuntimeError(f"alert {key!r} vanished during raise")
    escalated = severity_rank(finding.severity) > severity_rank(existing.severity)
    existing.last_seen_at = now
    existing.updated_at = now
    existing.occurrences = (existing.occurrences or 0) + 1
    existing.details = details
    existing.summary = finding.summary
    if escalated:
        existing.severity = str(finding.severity)
        existing.title = finding.title[:_MAX_TITLE]
        await _schedule_notification(db, existing, EVENT_ESCALATED, now, cfg)
        logger.warning(
            "alert_escalated", rule=existing.rule, severity=existing.severity
        )
    await db.flush()
    return RaiseResult(existing, EVENT_ESCALATED if escalated else EVENT_UPDATED)


async def _lock_active(db: AsyncSession, key: str) -> Alert | None:
    result = await db.execute(
        select(Alert)
        .where(_active(key))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _recently_notified(
    db: AsyncSession, alert: Alert, now: datetime, cfg: Settings
) -> bool:
    if cfg.alert_cooldown_minutes <= 0:
        return False
    since = now - timedelta(minutes=cfg.alert_cooldown_minutes)
    found = await db.scalar(
        select(func.count(Alert.id)).where(
            Alert.dedupe_key == alert.dedupe_key,
            Alert.id != alert.id,
            Alert.notified_at >= since,
        )
    )
    return bool(found)


async def _schedule_notification(
    db: AsyncSession, alert: Alert, event: str, now: datetime, cfg: Settings
) -> None:
    """Queue an email for ``event`` or record why none is sent."""
    if event == EVENT_RESOLVED:
        if not (
            cfg.alert_notify_resolved
            and cfg.alert_email_configured
            and alert.notify_status == NotifyStatus.SENT.value
        ):
            return
    elif not cfg.alert_email_configured:
        alert.notify_status = NotifyStatus.DISABLED.value
        return
    elif severity_rank(alert.severity) < severity_rank(cfg.alert_email_min_severity):
        alert.notify_status = NotifyStatus.SKIPPED.value
        return
    elif event == EVENT_OPENED and await _recently_notified(db, alert, now, cfg):
        alert.notify_status = NotifyStatus.SUPPRESSED.value
        return
    else:
        alert.notify_status = NotifyStatus.QUEUED.value
    await db.flush()
    await enqueue_in(
        db,
        NOTIFY_JOB,
        {"alert_id": str(alert.id), "event": event},
        dedupe_key=f"{NOTIFY_JOB}:{alert.id}:{event}:{alert.severity}",
        max_attempts=cfg.alert_email_max_attempts,
    )


async def resolve_alert(
    db: AsyncSession,
    alert: Alert,
    *,
    actor: str = SYSTEM_ACTOR,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> Alert:
    """Mark ``alert`` resolved (idempotent) and queue the resolved email if due."""
    if alert.status == AlertStatus.RESOLVED.value:
        return alert
    cfg = settings or get_settings()
    now = now or utcnow()
    alert.status = AlertStatus.RESOLVED.value
    alert.resolved_at = now
    alert.resolved_by = actor[:320]
    alert.updated_at = now
    await _schedule_notification(db, alert, EVENT_RESOLVED, now, cfg)
    await db.flush()
    logger.info("alert_resolved", rule=alert.rule, dedupe_key=alert.dedupe_key)
    return alert


async def acknowledge_alert(
    db: AsyncSession, alert: Alert, *, actor: str, now: datetime | None = None
) -> Alert:
    """Open -> acknowledged (someone is on it); other states are unchanged."""
    if alert.status != AlertStatus.OPEN.value:
        return alert
    now = now or utcnow()
    alert.status = AlertStatus.ACKNOWLEDGED.value
    alert.acknowledged_at = now
    alert.acknowledged_by = actor[:320]
    alert.updated_at = now
    await db.flush()
    return alert


async def resolve_missing(
    db: AsyncSession,
    rules: frozenset[str] | set[str],
    present_keys: set[str],
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> int:
    """Resolve active alerts of ``rules`` whose condition no longer holds."""
    if not rules:
        return 0
    rows = (
        (
            await db.execute(
                select(Alert)
                .where(
                    Alert.rule.in_(sorted(rules)),
                    Alert.status.in_(ALERT_ACTIVE_STATUSES),
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    resolved = 0
    for alert in rows:
        if alert.dedupe_key not in present_keys:
            await resolve_alert(db, alert, now=now, settings=settings)
            resolved += 1
    return resolved


async def resolve_stale(
    db: AsyncSession,
    rules: frozenset[str] | set[str],
    older_than: datetime,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> int:
    """Resolve active process-sampled alerts nobody re-reported since ``older_than``."""
    rows = (
        (
            await db.execute(
                select(Alert)
                .where(
                    Alert.rule.in_(sorted(rules)),
                    Alert.status.in_(ALERT_ACTIVE_STATUSES),
                    Alert.last_seen_at < older_than,
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for alert in rows:
        await resolve_alert(db, alert, now=now, settings=settings)
    return len(rows)


async def record_past_incident(
    db: AsyncSession,
    finding: Finding,
    *,
    started_at: datetime,
    resolved_at: datetime,
    emailed: bool,
) -> Alert:
    """Store an alert that could not be stored while it was happening (DB outage)."""
    alert = Alert(
        id=uuid.uuid4(),
        rule=finding.rule,
        dedupe_key=f"{finding.dedupe_key}:{started_at.isoformat()}"[:_MAX_KEY],
        severity=str(finding.severity),
        status=AlertStatus.RESOLVED.value,
        title=finding.title[:_MAX_TITLE],
        summary=finding.summary,
        component=finding.component,
        details=_safe_details(finding.details),
        first_seen_at=started_at,
        last_seen_at=resolved_at,
        resolved_at=resolved_at,
        resolved_by=SYSTEM_ACTOR,
        notify_status=(NotifyStatus.SENT if emailed else NotifyStatus.FAILED).value,
        notified_at=started_at if emailed else None,
        created_at=resolved_at,
        updated_at=resolved_at,
    )
    db.add(alert)
    await db.flush()
    return alert


async def prune_resolved(
    db: AsyncSession, *, now: datetime | None = None, settings: Settings | None = None
) -> int:
    cfg = settings or get_settings()
    cutoff = (now or utcnow()) - timedelta(days=cfg.alert_retention_days)
    result = await db.execute(
        delete(Alert).where(
            Alert.status == AlertStatus.RESOLVED.value, Alert.resolved_at < cutoff
        )
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# --- Reads for the admin console ----------------------------------------------


@dataclass(frozen=True, slots=True)
class AlertPage:
    items: list[Alert]
    total: int
    active_by_severity: dict[str, int]


async def list_alerts(
    db: AsyncSession,
    *,
    statuses: list[str] | None,
    severity: str | None,
    rule: str | None,
    limit: int,
    offset: int,
) -> AlertPage:
    """Newest first; filters are optional. ``limit`` is bounded by the route."""
    conditions = []
    if statuses:
        conditions.append(Alert.status.in_(statuses))
    if severity:
        conditions.append(Alert.severity == severity)
    if rule:
        conditions.append(Alert.rule == rule)
    rows = (
        (
            await db.execute(
                select(Alert)
                .where(*conditions)
                .order_by(Alert.last_seen_at.desc(), Alert.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    total = int(await db.scalar(select(func.count(Alert.id)).where(*conditions)) or 0)
    counts = (
        await db.execute(
            select(Alert.severity, func.count(Alert.id))
            .where(Alert.status.in_(ALERT_ACTIVE_STATUSES))
            .group_by(Alert.severity)
        )
    ).all()
    by_severity = {s.value: 0 for s in Severity}
    by_severity.update({str(sev): int(n) for sev, n in counts})
    return AlertPage(list(rows), total, by_severity)


async def get_alert(db: AsyncSession, alert_id: uuid.UUID) -> Alert | None:
    return await db.get(Alert, alert_id, with_for_update=True)
