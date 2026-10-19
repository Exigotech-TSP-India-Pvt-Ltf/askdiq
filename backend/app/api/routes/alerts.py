"""Alerts for the admin console (``/admin/alerts``).

Reading needs ``dashboard:read``; acknowledging or resolving needs
``alerts:manage`` (admin and superadmin) and is audited. Alerts are raised by
the rules in ``app.alerts.catalogue`` (see ``GET /admin/alerts/rules``).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import service
from app.alerts.catalogue import RULES, AlertStatus, Severity, rule_for
from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import NotFoundError, get_request_id
from app.core.request_meta import get_client_ip
from app.governance.audit import record_audit_event
from app.models.db_models import ALERT_ACTIVE_STATUSES, Alert
from app.schemas.alerts import (
    AlertCatalogueRead,
    AlertPage,
    AlertRead,
    AlertRuleRead,
)

router = APIRouter(prefix="/admin/alerts", tags=["Alerts"])

MAX_PAGE = 100
MAX_OFFSET = 10_000
DETAIL_NOT_FOUND = "Alert not found."

_PROBLEM = {"content": {"application/problem+json": {}}}
_READ_ERRORS = {
    401: {"description": "Not signed in or missing service key", **_PROBLEM},
    403: {"description": "Requires the dashboard:read permission", **_PROBLEM},
}
_WRITE_ERRORS = {
    401: _READ_ERRORS[401],
    403: {
        "description": "Requires alerts:manage (admin), or CSRF check failed",
        **_PROBLEM,
    },
    404: {"description": DETAIL_NOT_FOUND, **_PROBLEM},
}

Reader = Annotated[AuthUser, Depends(require_permission(Permission.DASHBOARD_READ))]
Manager = Annotated[AuthUser, Depends(require_permission(Permission.ALERTS_MANAGE))]
Db = Annotated[AsyncSession, Depends(get_db)]
StatusFilter = Literal["active", "open", "acknowledged", "resolved", "all"]


def alert_read(alert: Alert) -> AlertRead:
    rule = rule_for(alert.rule)
    return AlertRead.model_validate(
        {
            **{
                column.key: getattr(alert, column.key)
                for column in Alert.__table__.columns
                if column.key not in ("dedupe_key", "created_at", "updated_at")
            },
            "details": dict(alert.details or {}),
            "rule_name": rule.name if rule else alert.rule,
            "console_path": rule.console_path if rule else "/admin/governance",
            "runbook": rule.runbook if rule else "",
        }
    )


def _statuses(value: StatusFilter) -> list[str] | None:
    if value == "all":
        return None
    if value == "active":
        return list(ALERT_ACTIVE_STATUSES)
    return [value]


@router.get(
    "",
    response_model=AlertPage,
    summary="List alerts",
    description=(
        "Newest first (by last occurrence). `status=active` (default) returns "
        "open and acknowledged alerts. `active_by_severity` counts every active "
        "alert, for badges."
    ),
    responses=_READ_ERRORS,
)
async def list_alerts(
    db: Db,
    _user: Reader,
    status: StatusFilter = Query("active", description="Lifecycle filter."),
    severity: Severity | None = Query(None, description="Only this severity."),
    rule: str | None = Query(
        None, max_length=64, pattern=r"^[a-z0-9_]+$", description="Only this rule id."
    ),
    limit: int = Query(20, ge=1, le=MAX_PAGE),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
) -> AlertPage:
    page = await service.list_alerts(
        db,
        statuses=_statuses(status),
        severity=severity.value if severity else None,
        rule=rule,
        limit=limit,
        offset=offset,
    )
    return AlertPage(
        items=[alert_read(a) for a in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
        active_by_severity=page.active_by_severity,
    )


@router.get(
    "/rules",
    response_model=AlertCatalogueRead,
    summary="Alert catalogue",
    description=(
        "Every rule the platform alerts on (trigger, severity, console page, "
        "runbook) and whether email delivery is configured."
    ),
    responses=_READ_ERRORS,
)
async def list_alert_rules(_user: Reader) -> AlertCatalogueRead:
    cfg = get_settings()
    return AlertCatalogueRead(
        rules=[
            AlertRuleRead(
                id=r.id,
                name=r.name,
                component=r.component,
                source=r.source,
                trigger=r.trigger,
                severity=r.severity,
                console_path=r.console_path,
                runbook=r.runbook,
            )
            for r in RULES
        ],
        email_enabled=cfg.alert_email_configured,
        email_min_severity=cfg.alert_email_min_severity,
        cooldown_minutes=cfg.alert_cooldown_minutes,
        evaluate_interval_seconds=cfg.alerts_evaluate_interval_seconds,
    )


async def _load(db: AsyncSession, alert_id: uuid.UUID) -> Alert:
    alert = await service.get_alert(db, alert_id)
    if alert is None:
        raise NotFoundError(DETAIL_NOT_FOUND)
    return alert


@router.get(
    "/{alert_id}",
    response_model=AlertRead,
    summary="One alert",
    responses={**_READ_ERRORS, 404: _WRITE_ERRORS[404]},
)
async def get_alert(alert_id: uuid.UUID, db: Db, _user: Reader) -> AlertRead:
    alert = await db.get(Alert, alert_id)
    if alert is None:
        raise NotFoundError(DETAIL_NOT_FOUND)
    return alert_read(alert)


async def _audit(
    db: AsyncSession, request: Request, user: AuthUser, action: str, alert: Alert
) -> None:
    await record_audit_event(
        db,
        request_id=get_request_id(request),
        actor=user.email,
        action=action,
        detail={"alert_id": str(alert.id), "rule": alert.rule, "status": alert.status},
        ip_address=get_client_ip(request),
        commit=False,
    )


@router.post(
    "/{alert_id}/acknowledge",
    response_model=AlertRead,
    summary="Acknowledge an alert",
    description=(
        "Marks an open alert as acknowledged (someone is working on it); it "
        "stays active until its condition clears or it is resolved. Idempotent. "
        "Audited."
    ),
    responses=_WRITE_ERRORS,
)
async def acknowledge_alert(
    alert_id: uuid.UUID, request: Request, db: Db, user: Manager
) -> AlertRead:
    alert = await _load(db, alert_id)
    if alert.status == AlertStatus.OPEN.value:
        await service.acknowledge_alert(db, alert, actor=user.email)
        await _audit(db, request, user, "alert_acknowledged", alert)
    await db.commit()
    return alert_read(alert)


@router.post(
    "/{alert_id}/resolve",
    response_model=AlertRead,
    summary="Resolve an alert",
    description=(
        "Closes the alert. If the condition still holds, the next evaluation "
        "opens a new alert (emailed again only after ALERT_COOLDOWN_MINUTES). "
        "Idempotent. Audited."
    ),
    responses=_WRITE_ERRORS,
)
async def resolve_alert(
    alert_id: uuid.UUID, request: Request, db: Db, user: Manager
) -> AlertRead:
    alert = await _load(db, alert_id)
    if alert.status != AlertStatus.RESOLVED.value:
        await service.resolve_alert(db, alert, actor=user.email)
        await _audit(db, request, user, "alert_resolved", alert)
    await db.commit()
    return alert_read(alert)
