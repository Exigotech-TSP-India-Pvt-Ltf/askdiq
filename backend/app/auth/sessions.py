"""Sign-in session lifecycle (OWASP A04/A07).

- Ids are ``secrets.token_urlsafe(32)``; only an HMAC-SHA256 of the id
  (keyed with ``SESSION_SECRET``) is stored, so a database leak yields no
  usable session ids.
- Absolute TTL (``SESSION_TTL_MINUTES``) and idle TTL
  (``SESSION_IDLE_MINUTES``); ``last_seen_at`` is written at most once per
  ``SESSION_TOUCH_INTERVAL_SECONDS`` to avoid a write per request.
- ``create_session(replaces=...)`` rotates: the old id is revoked on login.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Role, UserStatus
from app.auth.principal import AuthUser
from app.auth.repository import AuthSessionRepository
from app.core.config import Settings, get_settings
from app.models.db_models import AuthSession, User

# token_urlsafe(32) is 43 chars; anything far outside that is never valid.
MAX_TOKEN_LENGTH = 128
USER_AGENT_MAX_LENGTH = 512


def utcnow() -> datetime:
    return datetime.now(UTC)


def hash_session_id(raw: str, settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    key = cfg.session_secret.get_secret_value().encode()
    return hmac.new(key, raw.encode(), hashlib.sha256).hexdigest()


def to_auth_user(user: User, session_id: uuid.UUID | None = None) -> AuthUser:
    role = Role(user.role) if user.role in Role._value2member_map_ else None
    return AuthUser(
        id=user.id,
        email=user.email,
        name=user.full_name,
        role=role,
        status=UserStatus(user.status),
        version=user.version,
        session_id=session_id,
    )


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A new session; ``token`` is returned to the proxy once and never stored."""

    token: str
    session_id: uuid.UUID
    expires_at: datetime


class SessionService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._repo = AuthSessionRepository(db)
        self._settings = settings or get_settings()
        self._clock = clock

    def _hash(self, raw: str) -> str:
        return hash_session_id(raw, self._settings)

    def _idle_deadline(self, now: datetime, expires_at: datetime) -> datetime:
        return min(
            now + timedelta(minutes=self._settings.session_idle_minutes), expires_at
        )

    async def create_session(
        self,
        user_id: uuid.UUID,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
        replaces: str | None = None,
    ) -> IssuedSession:
        """Issue a session; revoke ``replaces`` first (rotation on login)."""
        now = self._clock()
        if replaces:
            await self.revoke(replaces)
        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(minutes=self._settings.session_ttl_minutes)
        row = await self._repo.add(
            AuthSession(
                id=uuid.uuid4(),
                id_hash=self._hash(token),
                user_id=user_id,
                created_at=now,
                expires_at=expires_at,
                idle_expires_at=self._idle_deadline(now, expires_at),
                last_seen_at=now,
                ip_address=ip_address,
                user_agent=(user_agent or "")[:USER_AGENT_MAX_LENGTH] or None,
            )
        )
        return IssuedSession(token=token, session_id=row.id, expires_at=expires_at)

    async def resolve(self, raw: str) -> AuthUser | None:
        """The session's user (loaded fresh), or None if invalid/expired/revoked."""
        if not raw or len(raw) > MAX_TOKEN_LENGTH:
            return None
        now = self._clock()
        found = await self._repo.get_live_with_user(self._hash(raw), now)
        if found is None:
            return None
        session, user = found
        interval = timedelta(seconds=self._settings.session_touch_interval_seconds)
        if now - session.last_seen_at >= interval:
            await self._repo.touch(
                session.id, now, self._idle_deadline(now, session.expires_at)
            )
        return to_auth_user(user, session.id)

    async def revoke(self, raw: str) -> bool:
        if not raw or len(raw) > MAX_TOKEN_LENGTH:
            return False
        return await self._repo.revoke_by_hash(self._hash(raw), self._clock())

    async def revoke_all(self, user_id: uuid.UUID) -> int:
        return await self._repo.revoke_all_for_user(user_id, self._clock())

    async def live_sessions(self, user_id: uuid.UUID, limit: int) -> list[AuthSession]:
        return await self._repo.list_live_for_user(user_id, self._clock(), limit)
