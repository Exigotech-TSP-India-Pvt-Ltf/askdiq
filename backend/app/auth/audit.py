"""Auth audit events (newplan §7.5) written to the immutable ``audit_log``.

Every sign-in, access decision and user-management action is recorded in the
caller's transaction (``commit=False``), so the audit row and the change it
describes commit or roll back together. Free text (justifications, decision
notes) is PII-redacted and target emails are masked; the ``actor`` keeps the
acting user's email so the trail shows who did what.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import get_request_id
from app.core.request_meta import get_client_ip
from app.governance.audit import record_audit_event
from app.governance.pii import redact_pii

ANONYMOUS_ACTOR = "anonymous"
AUDIT_TEXT_MAX_LENGTH = 500


class AuthEvent(StrEnum):
    LOGIN_SUCCESS = "login_success"
    LOGIN_DENIED = "login_denied"
    ACCESS_REQUESTED = "access_requested"
    ACCESS_APPROVED = "access_approved"
    ACCESS_REJECTED = "access_rejected"
    ROLE_CHANGED = "role_changed"
    USER_SUSPENDED = "user_suspended"
    USER_REACTIVATED = "user_reactivated"
    SESSION_REVOKED = "session_revoked"
    LOGOUT = "logout"
    USER_INVITED = "user_invited"


# Readable only with `audit:auth:read` (superadmin) on the governance views.
AUTH_AUDIT_ACTIONS: frozenset[str] = frozenset(e.value for e in AuthEvent)


def mask_email(email: str | None) -> str:
    """``alice@example.com`` -> ``a***@example.com`` (domain kept for triage)."""
    local, _, domain = (email or "").partition("@")
    if not local or not domain:
        return "***"
    return f"{local[0]}***@{domain}"


def mask_text(text: str | None) -> str | None:
    if not text:
        return None
    return redact_pii(text)[0][:AUDIT_TEXT_MAX_LENGTH]


@dataclass(frozen=True, slots=True)
class AuditContext:
    """Who acted, from where, under which request id."""

    request_id: str
    actor: str
    ip_address: str | None

    @classmethod
    def from_request(
        cls, request: Request, actor: str = ANONYMOUS_ACTOR
    ) -> AuditContext:
        return cls(
            request_id=get_request_id(request),
            actor=actor,
            ip_address=get_client_ip(request),
        )

    def as_actor(self, actor: str) -> AuditContext:
        return AuditContext(self.request_id, actor, self.ip_address)


def target_detail(user_id: uuid.UUID, email: str | None) -> dict[str, Any]:
    return {"target_user_id": str(user_id), "target_email": mask_email(email)}


async def record_auth_event(
    db: AsyncSession,
    ctx: AuditContext,
    event: AuthEvent,
    detail: dict[str, Any],
) -> None:
    """Insert one audit row in the current transaction (the caller commits)."""
    await record_audit_event(
        db,
        request_id=ctx.request_id,
        actor=ctx.actor,
        action=event.value,
        detail=detail,
        ip_address=ctx.ip_address,
        commit=False,
    )
