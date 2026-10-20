"""Exposes the audit trail that app/governance/audit.py already writes on
every query/ingest — makes governance data actually visible, not just
recorded and forgotten.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.audit import AUTH_AUDIT_ACTIONS
from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.database import get_db
from app.models.db_models import AuditLog
from app.schemas.governance import AuditLogEntry

router = APIRouter()


@router.get(
    "/governance/audit-log",
    response_model=list[AuditLogEntry],
    description=(
        "Newest first. Auth events are only included for "
        "callers with `audit:auth:read` (superadmin)."
    ),
)
async def list_audit_log(
    limit: int = Query(default=100, ge=1, le=500),
    action: str | None = Query(
        default=None, description="Filter by action, e.g. 'query' or 'ingest'"
    ),
    category: Literal["auth"] | None = Query(
        default=None,
        description=(
            "`auth`: sign-in, access and role events only, so they are not "
            "crowded out of `limit` by query / ingest rows."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> list[AuditLogEntry]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)

    if action:
        stmt = stmt.where(AuditLog.action == action)
    if category == "auth":
        stmt = stmt.where(AuditLog.action.in_(AUTH_AUDIT_ACTIONS))
    # Auth events (sign-ins, approvals, role changes) need `audit:auth:read`.
    if not user.has(Permission.AUDIT_AUTH_READ):
        stmt = stmt.where(AuditLog.action.not_in(AUTH_AUDIT_ACTIONS))

    rows = (await db.execute(stmt)).scalars().all()

    return [
        AuditLogEntry(
            id=str(row.id),
            request_id=row.request_id,
            actor=row.actor,
            action=row.action,
            detail=row.detail,
            created_at=row.created_at,
            ip_address=row.ip_address,
        )
        for row in rows
    ]
