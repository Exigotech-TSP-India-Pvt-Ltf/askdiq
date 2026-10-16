"""Sign-in, self-service access requests and logout (newplan §7.2, B2.4.1).

Status rules applied on every SSO sign-in (newplan §7.2):

- The user is upserted on the Entra object id (``oid``). A pre-provisioned
  (invited) row with the same email and no ``oid`` yet is linked instead.
- A new user is ``pending`` with no role: Chat works, dashboards answer 403.
- An email in ``SUPERADMIN_EMAILS`` becomes an active superadmin on its first
  sign-in (while still ``pending``). The bootstrap never overrides a later
  decision, so a suspended superadmin email stays suspended.
- ``active``, ``pending`` and ``rejected`` users get a session (rotated on
  login); ``suspended`` users are denied (403) and get no session.
- Every outcome writes ``login_success`` or ``login_denied`` to the audit log.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.audit import (
    AuditContext,
    AuthEvent,
    mask_email,
    mask_text,
    record_auth_event,
    target_detail,
)
from app.auth.oidc import IdentityClaims
from app.auth.permissions import AccessRequestStatus, Role, UserStatus
from app.auth.principal import AuthUser
from app.auth.sessions import IssuedSession, SessionService, to_auth_user, utcnow
from app.auth.user_repository import AccessRequestRepository, UserRepository
from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, ForbiddenError
from app.models.db_models import AccessRequest, User

DETAIL_SUSPENDED = "Account suspended."
DETAIL_SIGN_IN_DENIED = "Sign-in denied."
DETAIL_ALREADY_ACTIVE = "You already have dashboard access."
DETAIL_REQUEST_PENDING = "An access request is already pending."


@dataclass(frozen=True, slots=True)
class SignInResult:
    user: AuthUser
    session: IssuedSession


@dataclass(frozen=True, slots=True)
class AccessRequestState:
    user: User
    request: AccessRequest | None


@asynccontextmanager
async def transaction(db: AsyncSession) -> AsyncIterator[None]:
    """Commit on success; roll back on any error so nothing half-done persists."""
    try:
        yield
    except BaseException:
        await db.rollback()
        raise
    await db.commit()


class AuthService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._clock = clock
        self._users = UserRepository(db)
        self._requests = AccessRequestRepository(db)
        self._sessions = SessionService(db, self._settings, clock)

    @property
    def access_contact_email(self) -> str | None:
        return self._settings.access_contact_email or None

    # ------------------------------------------------------------------ login

    async def record_login_denied(self, ctx: AuditContext, reason: str) -> None:
        """Audit a failed sign-in (bad/replayed state, invalid token, IdP down)."""
        async with transaction(self._db):
            await record_auth_event(
                self._db, ctx, AuthEvent.LOGIN_DENIED, {"reason": reason}
            )

    async def sign_in(
        self,
        identity: IdentityClaims,
        ctx: AuditContext,
        *,
        user_agent: str | None = None,
        replaces: str | None = None,
    ) -> SignInResult:
        try:
            return await self._sign_in(identity, ctx, user_agent, replaces)
        except IntegrityError:
            # A concurrent first sign-in inserted the same oid/email; the row
            # now exists, so one retry takes the "known user" path.
            return await self._sign_in(identity, ctx, user_agent, replaces)

    async def _sign_in(
        self,
        identity: IdentityClaims,
        ctx: AuditContext,
        user_agent: str | None,
        replaces: str | None,
    ) -> SignInResult:
        ctx = ctx.as_actor(identity.email)
        now = self._clock()
        async with transaction(self._db):
            user, created = await self._upsert(identity)
            if user is None:
                await record_auth_event(
                    self._db,
                    ctx,
                    AuthEvent.LOGIN_DENIED,
                    {
                        "reason": "identity_conflict",
                        "email": mask_email(identity.email),
                    },
                )
                denied: ForbiddenError | None = ForbiddenError(DETAIL_SIGN_IN_DENIED)
            elif user.status == UserStatus.SUSPENDED.value:
                await record_auth_event(
                    self._db,
                    ctx,
                    AuthEvent.LOGIN_DENIED,
                    {"reason": "suspended", **target_detail(user.id, user.email)},
                )
                denied = ForbiddenError(
                    DETAIL_SUSPENDED,
                    extra={"access_contact_email": self.access_contact_email},
                )
            else:
                denied = None
                bootstrapped = self._bootstrap_superadmin(user, now)
                user.last_login_at = now
                issued = await self._sessions.create_session(
                    user.id,
                    ip_address=ctx.ip_address,
                    user_agent=user_agent,
                    replaces=replaces,
                )
                await record_auth_event(
                    self._db,
                    ctx,
                    AuthEvent.LOGIN_SUCCESS,
                    {
                        **target_detail(user.id, user.email),
                        "status": user.status,
                        "role": user.role,
                        "created": created,
                        "superadmin_bootstrap": bootstrapped,
                        "session_id": str(issued.session_id),
                    },
                )
        if denied is not None:
            raise denied
        return SignInResult(user=to_auth_user(user, issued.session_id), session=issued)

    async def _upsert(self, identity: IdentityClaims) -> tuple[User | None, bool]:
        """The user for ``identity`` (locked), creating it on first sign-in.

        Returns ``(None, False)`` when the email already belongs to a
        different Entra identity (never merge two identities).
        """
        user = await self._users.get_by_oid(identity.oid, for_update=True)
        if user is None:
            user = await self._users.get_by_email(identity.email, for_update=True)
            if user is not None:
                if user.entra_oid is not None:
                    return None, False
                user.entra_oid = identity.oid  # first sign-in of an invite
            else:
                user = await self._users.add(
                    User(
                        id=uuid.uuid4(),
                        full_name=identity.name[:256],
                        email=identity.email,
                        entra_oid=identity.oid,
                        tenant_id=identity.tid,
                        role=None,
                        status=UserStatus.PENDING.value,
                        version=1,
                    )
                )
                return user, True
        elif user.email.lower() != identity.email:
            # Keep the email current unless another row already owns it.
            if await self._users.get_by_email(identity.email) is None:
                user.email = identity.email
        user.full_name = identity.name[:256]
        user.tenant_id = identity.tid
        return user, False

    def _bootstrap_superadmin(self, user: User, now: datetime) -> bool:
        if (
            user.email.lower() not in self._settings.superadmin_email_set
            or user.status != UserStatus.PENDING.value
        ):
            return False
        user.role = Role.SUPERADMIN.value
        user.status = UserStatus.ACTIVE.value
        user.approved_at = now
        user.version += 1
        return True

    # ----------------------------------------------------------------- logout

    async def logout(self, user: AuthUser, raw_session: str, ctx: AuditContext) -> None:
        async with transaction(self._db):
            revoked = await self._sessions.revoke(raw_session)
            await record_auth_event(
                self._db,
                ctx.as_actor(user.email),
                AuthEvent.LOGOUT,
                {
                    **target_detail(user.id, user.email),
                    "session_id": str(user.session_id) if user.session_id else None,
                    "revoked": revoked,
                },
            )

    # --------------------------------------------------------- access request

    async def access_request_state(self, user: AuthUser) -> AccessRequestState:
        row = await self._users.get(user.id)
        if row is None:  # deleted between session lookup and now
            raise ForbiddenError(DETAIL_SIGN_IN_DENIED)
        return AccessRequestState(
            user=row, request=await self._requests.latest_for_user(user.id)
        )

    async def request_access(
        self,
        user: AuthUser,
        requested_role: Role | None,
        justification: str | None,
        ctx: AuditContext,
    ) -> AccessRequest:
        try:
            async with transaction(self._db):
                row = await self._users.get(user.id, for_update=True)
                if row is None or row.status == UserStatus.SUSPENDED.value:
                    raise ForbiddenError(DETAIL_SUSPENDED)
                if row.status == UserStatus.ACTIVE.value and row.role is not None:
                    raise ConflictError(DETAIL_ALREADY_ACTIVE)
                if await self._requests.pending_for_user(user.id) is not None:
                    raise ConflictError(DETAIL_REQUEST_PENDING)
                request = await self._requests.add(
                    AccessRequest(
                        id=uuid.uuid4(),
                        user_id=user.id,
                        requested_role=requested_role.value if requested_role else None,
                        justification=justification,
                        status=AccessRequestStatus.PENDING.value,
                        created_at=self._clock(),
                    )
                )
                if row.status == UserStatus.REJECTED.value:
                    # Asking again re-opens review.
                    row.status = UserStatus.PENDING.value
                    row.version += 1
                await record_auth_event(
                    self._db,
                    ctx.as_actor(user.email),
                    AuthEvent.ACCESS_REQUESTED,
                    {
                        **target_detail(user.id, user.email),
                        "access_request_id": str(request.id),
                        "requested_role": request.requested_role,
                        "justification": mask_text(justification),
                    },
                )
        except IntegrityError as exc:  # the one-pending-per-user index
            raise ConflictError(DETAIL_REQUEST_PENDING) from exc
        return request
