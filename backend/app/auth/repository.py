"""Persistence for sign-in sessions (`auth_sessions` joined to `users`)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import AuthSession, User


class AuthSessionRepository:
    """All SQL for `auth_sessions`; callers own the transaction."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(self, row: AuthSession) -> AuthSession:
        self._db.add(row)
        await self._db.flush()
        return row

    async def get_live_with_user(
        self, id_hash: str, now: datetime
    ) -> tuple[AuthSession, User] | None:
        """The unrevoked, unexpired session for ``id_hash`` and its user."""
        stmt = (
            select(AuthSession, User)
            .join(User, User.id == AuthSession.user_id)
            .where(
                AuthSession.id_hash == id_hash,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
                AuthSession.idle_expires_at > now,
            )
        )
        row = (await self._db.execute(stmt)).first()
        return None if row is None else (row[0], row[1])

    async def touch(
        self, session_id: uuid.UUID, now: datetime, idle_expires_at: datetime
    ) -> None:
        await self._db.execute(
            update(AuthSession)
            .where(AuthSession.id == session_id)
            .values(last_seen_at=now, idle_expires_at=idle_expires_at)
        )

    async def revoke_by_hash(self, id_hash: str, now: datetime) -> bool:
        result = await self._db.execute(
            update(AuthSession)
            .where(AuthSession.id_hash == id_hash, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return bool(result.rowcount)

    async def revoke_all_for_user(self, user_id: uuid.UUID, now: datetime) -> int:
        result = await self._db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return int(result.rowcount or 0)

    async def list_live_for_user(
        self, user_id: uuid.UUID, now: datetime, limit: int
    ) -> list[AuthSession]:
        """Unrevoked, unexpired sessions, most recently used first."""
        stmt = (
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
                AuthSession.idle_expires_at > now,
            )
            .order_by(AuthSession.last_seen_at.desc())
            .limit(limit)
        )
        return list((await self._db.execute(stmt)).scalars().all())
