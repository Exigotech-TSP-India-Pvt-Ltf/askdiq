"""User Management: users, roles, access requests (newplan §7.6, §7.8; B2.4.2).

Safeguards (all answer 409):

- Nobody can change their own role or status (no self-demote/suspend).
- The last active superadmin can never be demoted or suspended. Every change
  that could remove one first locks all active superadmin rows, so two
  superadmins demoting each other at once cannot both succeed.
- ``PATCH`` carries the row ``version`` (optimistic concurrency).
- An access request is decided once: the row is locked
  (``SELECT ... FOR UPDATE``) and must still be pending, so of two concurrent
  approvals exactly one succeeds.
- Bulk approve runs in one transaction and is all-or-nothing.

Lock order is always: superadmin rows -> access requests -> target users (by
id), which keeps concurrent decisions deadlock-free. Every action writes its
audit event (newplan §7.5) in the same transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.audit import (
    AuditContext,
    AuthEvent,
    mask_text,
    record_auth_event,
    target_detail,
)
from app.auth.permissions import AccessRequestStatus, Role, UserStatus
from app.auth.principal import AuthUser
from app.auth.service import transaction
from app.auth.sessions import SessionService, utcnow
from app.auth.user_repository import (
    AccessRequestRepository,
    AccessRequestRow,
    AuthStatsRepository,
    UserFilters,
    UserListRow,
    UserRepository,
)
from app.core.exceptions import ConflictError, NotFoundError
from app.models.db_models import AccessRequest, AuthSession, User

DETAIL_USER_NOT_FOUND = "User not found."
DETAIL_REQUEST_NOT_FOUND = "Access request not found."
DETAIL_STALE_VERSION = "This user was changed by someone else. Reload and try again."
DETAIL_SELF_CHANGE = "You cannot change your own role or status."
DETAIL_LAST_SUPERADMIN = "The last active superadmin cannot be demoted or suspended."
DETAIL_ROLE_REQUIRED = "Assign a role before activating this user."
DETAIL_ALREADY_DECIDED = "This access request has already been decided."
DETAIL_TARGET_SUSPENDED = "This user is suspended. Reactivate them first."
DETAIL_EMAIL_EXISTS = "A user with this email already exists."
LIVE_SESSIONS_LIMIT = 50
LOGIN_EVENT = AuthEvent.LOGIN_SUCCESS.value


@dataclass(frozen=True, slots=True)
class UserChange:
    version: int
    role: Role | None = None
    status: UserStatus | None = None


@dataclass(frozen=True, slots=True)
class UserDetail:
    row: UserListRow
    sessions: list[AuthSession]


@dataclass(frozen=True, slots=True)
class UserStats:
    days: int
    status_counts: dict[str, int]
    open_requests: int
    logins_24h: int
    logins_per_day: list[tuple[date, int]]
    funnel: dict[str, int]
    role_mix: dict[str, int]
    time_to_approve: dict[str, int]


def _naive_utc(moment: datetime) -> datetime:
    """``audit_log.created_at`` is naive UTC (legacy column)."""
    return moment.astimezone(UTC).replace(tzinfo=None)


class UserAdminService:
    def __init__(
        self, db: AsyncSession, clock: Callable[[], datetime] = utcnow
    ) -> None:
        self._db = db
        self._clock = clock
        self._users = UserRepository(db)
        self._requests = AccessRequestRepository(db)
        self._stats = AuthStatsRepository(db)
        self._sessions = SessionService(db, clock=clock)

    # ------------------------------------------------------------------ reads

    async def list_users(self, filters: UserFilters) -> tuple[list[UserListRow], int]:
        return await self._users.list(filters)

    async def get_user(self, user_id: uuid.UUID) -> UserDetail:
        user = await self._users.get(user_id)
        if user is None:
            raise NotFoundError(DETAIL_USER_NOT_FOUND)
        return UserDetail(
            row=UserListRow(user, await self._users.approver_email(user)),
            sessions=await self._sessions.live_sessions(user_id, LIVE_SESSIONS_LIMIT),
        )

    async def list_requests(
        self, status: AccessRequestStatus | None, limit: int, offset: int
    ) -> tuple[list[AccessRequestRow], int]:
        return await self._requests.list(status, limit, offset)

    async def stats(self, days: int) -> UserStats:
        now = self._clock()
        since = now - timedelta(days=days)
        per_day = await self._stats.events_per_day(
            LOGIN_EVENT, _naive_utc(since.replace(hour=0, minute=0, second=0))
        )
        first_day = (now - timedelta(days=days - 1)).date()
        series = []
        for offset in range(days):
            day = first_day + timedelta(days=offset)
            series.append((day, per_day.get(day, 0)))
        status_counts = await self._users.count_by_status()
        return UserStats(
            days=days,
            status_counts=status_counts,
            open_requests=await self._requests.count_pending(),
            logins_24h=await self._stats.count_events_since(
                LOGIN_EVENT, _naive_utc(now - timedelta(hours=24))
            ),
            logins_per_day=series,
            funnel={
                "signed_in": await self._users.count_signed_in(),
                "requested": await self._requests.count_users_with_status(None),
                "approved": await self._requests.count_users_with_status(
                    AccessRequestStatus.APPROVED
                ),
                "active": status_counts.get(UserStatus.ACTIVE.value, 0),
            },
            role_mix=await self._users.active_role_mix(),
            time_to_approve=await self._requests.approval_histogram(since),
        )

    # ------------------------------------------------------------ user changes

    async def update_user(
        self,
        actor: AuthUser,
        user_id: uuid.UUID,
        change: UserChange,
        ctx: AuditContext,
    ) -> UserListRow:
        async with transaction(self._db):
            current = await self._users.get(user_id)
            if current is None:
                raise NotFoundError(DETAIL_USER_NOT_FOUND)
            superadmins: list[uuid.UUID] = []
            if current.role == Role.SUPERADMIN.value:
                superadmins = await self._users.lock_active_superadmins()
            pending = await self._requests.pending_for_user(user_id, for_update=True)
            user = await self._users.get(user_id, for_update=True)
            if user is None:
                raise NotFoundError(DETAIL_USER_NOT_FOUND)
            if user.version != change.version:
                raise ConflictError(DETAIL_STALE_VERSION)

            old_role, old_status = user.role, user.status
            new_role = change.role.value if change.role else old_role
            new_status = change.status.value if change.status else old_status
            if (new_role, new_status) == (old_role, old_status):
                return UserListRow(user, await self._users.approver_email(user))
            if user.id == actor.id:
                raise ConflictError(DETAIL_SELF_CHANGE)
            if new_status == UserStatus.ACTIVE.value and new_role is None:
                raise ConflictError(DETAIL_ROLE_REQUIRED)
            was_superadmin = (
                old_role == Role.SUPERADMIN.value
                and old_status == UserStatus.ACTIVE.value
            )
            stays_superadmin = (
                new_role == Role.SUPERADMIN.value
                and new_status == UserStatus.ACTIVE.value
            )
            if was_superadmin and not stays_superadmin and len(superadmins) <= 1:
                raise ConflictError(DETAIL_LAST_SUPERADMIN)

            now = self._clock()
            user.role = new_role
            user.status = new_status
            user.version += 1
            base = target_detail(user.id, user.email)
            actor_ctx = ctx.as_actor(actor.email)
            if new_role != old_role:
                await record_auth_event(
                    self._db,
                    actor_ctx,
                    AuthEvent.ROLE_CHANGED,
                    {**base, "from_role": old_role, "to_role": new_role},
                )
            if new_status != old_status:
                event = self._status_event(old_status, new_status)
                revoked: int | None = None
                if event is AuthEvent.USER_SUSPENDED:
                    # Sign the user out everywhere now, in the same transaction,
                    # instead of relying on the per-request status check alone.
                    revoked = await self._sessions.revoke_all(user.id)
                if event is AuthEvent.ACCESS_APPROVED:
                    user.approved_by = actor.id
                    user.approved_at = now
                    if pending is not None:
                        self._decide(pending, actor, AccessRequestStatus.APPROVED, now)
                await record_auth_event(
                    self._db,
                    actor_ctx,
                    event,
                    {
                        **base,
                        "from_status": old_status,
                        "to_status": new_status,
                        "role": new_role,
                        "access_request_id": str(pending.id) if pending else None,
                        **(
                            {"sessions_revoked": revoked} if revoked is not None else {}
                        ),
                    },
                )
            approver = await self._users.approver_email(user)
        return UserListRow(user, approver)

    @staticmethod
    def _status_event(old_status: str, new_status: str) -> AuthEvent:
        if new_status == UserStatus.SUSPENDED.value:
            return AuthEvent.USER_SUSPENDED
        if old_status == UserStatus.SUSPENDED.value:
            return AuthEvent.USER_REACTIVATED
        return AuthEvent.ACCESS_APPROVED

    async def revoke_sessions(
        self, actor: AuthUser, user_id: uuid.UUID, ctx: AuditContext
    ) -> int:
        async with transaction(self._db):
            user = await self._users.get(user_id)
            if user is None:
                raise NotFoundError(DETAIL_USER_NOT_FOUND)
            revoked = await self._sessions.revoke_all(user_id)
            await record_auth_event(
                self._db,
                ctx.as_actor(actor.email),
                AuthEvent.SESSION_REVOKED,
                {**target_detail(user.id, user.email), "revoked": revoked},
            )
        return revoked

    async def invite(
        self,
        actor: AuthUser,
        email: str,
        role: Role,
        name: str | None,
        ctx: AuditContext,
    ) -> UserListRow:
        email = email.strip().lower()
        try:
            async with transaction(self._db):
                if await self._users.get_by_email(email) is not None:
                    raise ConflictError(DETAIL_EMAIL_EXISTS)
                now = self._clock()
                user = await self._users.add(
                    User(
                        id=uuid.uuid4(),
                        full_name=(name or "").strip() or email.split("@")[0],
                        email=email,
                        role=role.value,
                        status=UserStatus.ACTIVE.value,
                        approved_by=actor.id,
                        approved_at=now,
                        version=1,
                    )
                )
                await record_auth_event(
                    self._db,
                    ctx.as_actor(actor.email),
                    AuthEvent.USER_INVITED,
                    {**target_detail(user.id, user.email), "role": role.value},
                )
        except IntegrityError as exc:  # concurrent invite of the same email
            raise ConflictError(DETAIL_EMAIL_EXISTS) from exc
        return UserListRow(user, actor.email)

    # -------------------------------------------------------- access requests

    def _decide(
        self,
        request: AccessRequest,
        actor: AuthUser,
        status: AccessRequestStatus,
        now: datetime,
        note: str | None = None,
    ) -> None:
        request.status = status.value
        request.decided_by = actor.id
        request.decided_at = now
        request.decision_note = note

    async def _approve_locked(
        self,
        actor: AuthUser,
        request: AccessRequest,
        user: User,
        role: Role,
        now: datetime,
        ctx: AuditContext,
    ) -> None:
        if request.status != AccessRequestStatus.PENDING.value:
            raise ConflictError(DETAIL_ALREADY_DECIDED)
        if user.status == UserStatus.SUSPENDED.value:
            raise ConflictError(DETAIL_TARGET_SUSPENDED)
        self._decide(request, actor, AccessRequestStatus.APPROVED, now)
        user.role = role.value
        user.status = UserStatus.ACTIVE.value
        user.approved_by = actor.id
        user.approved_at = now
        user.version += 1
        await record_auth_event(
            self._db,
            ctx.as_actor(actor.email),
            AuthEvent.ACCESS_APPROVED,
            {
                **target_detail(user.id, user.email),
                "access_request_id": str(request.id),
                "requested_role": request.requested_role,
                "role": role.value,
            },
        )

    async def approve(
        self,
        actor: AuthUser,
        request_id: uuid.UUID,
        role: Role,
        ctx: AuditContext,
    ) -> AccessRequestRow:
        async with transaction(self._db):
            request = await self._requests.get(request_id, for_update=True)
            if request is None:
                raise NotFoundError(DETAIL_REQUEST_NOT_FOUND)
            user = await self._users.get(request.user_id, for_update=True)
            if user is None:
                raise NotFoundError(DETAIL_USER_NOT_FOUND)
            await self._approve_locked(actor, request, user, role, self._clock(), ctx)
        return AccessRequestRow(request, user, actor.email)

    async def reject(
        self,
        actor: AuthUser,
        request_id: uuid.UUID,
        note: str,
        ctx: AuditContext,
    ) -> AccessRequestRow:
        async with transaction(self._db):
            request = await self._requests.get(request_id, for_update=True)
            if request is None:
                raise NotFoundError(DETAIL_REQUEST_NOT_FOUND)
            if request.status != AccessRequestStatus.PENDING.value:
                raise ConflictError(DETAIL_ALREADY_DECIDED)
            user = await self._users.get(request.user_id, for_update=True)
            if user is None:
                raise NotFoundError(DETAIL_USER_NOT_FOUND)
            self._decide(
                request, actor, AccessRequestStatus.REJECTED, self._clock(), note
            )
            if user.status == UserStatus.PENDING.value:
                user.status = UserStatus.REJECTED.value
                user.version += 1
            await record_auth_event(
                self._db,
                ctx.as_actor(actor.email),
                AuthEvent.ACCESS_REJECTED,
                {
                    **target_detail(user.id, user.email),
                    "access_request_id": str(request.id),
                    "note": mask_text(note),
                },
            )
        return AccessRequestRow(request, user, actor.email)

    async def bulk_approve(
        self,
        actor: AuthUser,
        request_ids: Sequence[uuid.UUID],
        role: Role,
        ctx: AuditContext,
    ) -> list[uuid.UUID]:
        """Approve every request or none (one transaction)."""
        wanted = sorted(set(request_ids))
        async with transaction(self._db):
            requests = await self._requests.lock_many(wanted)
            if len(requests) != len(wanted):
                raise NotFoundError(DETAIL_REQUEST_NOT_FOUND)
            users = {
                u.id: u
                for u in await self._users.lock_many([r.user_id for r in requests])
            }
            now = self._clock()
            for request in requests:
                user = users.get(request.user_id)
                if user is None:
                    raise NotFoundError(DETAIL_USER_NOT_FOUND)
                await self._approve_locked(actor, request, user, role, now, ctx)
        return [r.id for r in requests]
