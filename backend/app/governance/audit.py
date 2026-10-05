"""Writes an audit trail entry for every query/ingest/guardrail event.

Every query is logged with its request id, actor, retrieved chunk ids,
guardrail decisions, and response — giving a reproducible record of what
the system did and why, which is the backbone of the governance
requirement (not just guardrails, but auditability of decisions).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import AuditLog


async def record_audit_event(
    session: AsyncSession,
    request_id: str,
    actor: str,
    action: str,
    detail: dict,
    ip_address: str | None = None,
) -> None:
    session.add(
        AuditLog(
            request_id=request_id,
            actor=actor,
            action=action,
            detail=detail,
            ip_address=ip_address,
        )
    )
    await session.commit()
