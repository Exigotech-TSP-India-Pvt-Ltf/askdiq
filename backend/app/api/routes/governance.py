"""Exposes the audit trail that app/governance/audit.py already writes on
every query/ingest — makes governance data actually visible, not just
recorded and forgotten.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.governance.auth import get_current_actor
from app.models.db_models import AuditLog
from app.schemas.governance import AuditLogEntry

router = APIRouter()


@router.get("/governance/audit-log", response_model=list[AuditLogEntry])
async def list_audit_log(
    limit: int = Query(default=100, ge=1, le=500),
    action: str | None = Query(
        default=None, description="Filter by action, e.g. 'query' or 'ingest'"
    ),
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(get_current_actor),
) -> list[AuditLogEntry]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)

    if action:
        stmt = stmt.where(AuditLog.action == action)

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
