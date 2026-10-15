"""Helpers for tests that use real SSO sessions (marker ``real_auth``)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CSRF_HEADER_VALUE
from app.auth.sessions import SessionService
from app.core.config import get_settings
from app.models.db_models import User


async def make_user(
    db: AsyncSession,
    *,
    role: str | None = None,
    status: str = "pending",
    email: str | None = None,
) -> User:
    email = email or f"user-{uuid.uuid4().hex[:12]}@example.com"
    user = User(
        full_name=email.split("@")[0],
        email=email,
        role=role,
        status=status,
        entra_oid=str(uuid.uuid4()),
        tenant_id=get_settings().entra_tenant_id,
    )
    db.add(user)
    await db.flush()
    return user


async def session_headers(
    db: AsyncSession, user: User, *, csrf: bool = True
) -> dict[str, str]:
    """Headers the Next proxy sends for this user's session."""
    issued = await SessionService(db).create_session(user.id)
    await db.commit()
    headers = {"X-Session": issued.token}
    if csrf:
        headers |= {
            "X-Requested-With": CSRF_HEADER_VALUE,
            "Origin": get_settings().app_origin,
        }
    return headers
