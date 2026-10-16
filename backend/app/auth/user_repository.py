"""Persistence for users, access requests and the User Management stats.

All SQL for these tables lives here; callers (the services in
``app.auth.service`` / ``app.auth.admin_service``) own the transaction.
Row locks (``SELECT ... FOR UPDATE``) always use ``populate_existing`` so the
locked values, not a stale identity-map copy, drive the decision.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import Date, Select, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.auth.permissions import AccessRequestStatus, Role, UserStatus
from app.models.db_models import AccessRequest, AuditLog, User

SortOrder = Literal["asc", "desc"]
UserSortKey = Literal["created_at", "last_login_at", "email", "name"]
USER_SORT_COLUMNS: dict[str, Any] = {
    "created_at": User.created_at,
    "last_login_at": User.last_login_at,
    "email": User.email,
    "name": User.full_name,
}
LIKE_ESCAPE = "\\"

# Time-to-approve histogram buckets: (label, upper bound in seconds or None).
HOUR = 3600
DAY = 24 * HOUR
APPROVAL_BUCKETS: tuple[tuple[str, int | None], ...] = (
    ("<1h", HOUR),
    ("1-4h", 4 * HOUR),
    ("4-24h", DAY),
    ("1-3d", 3 * DAY),
    (">3d", None),
)


def escape_like(text: str) -> str:
    """Escape LIKE wildcards so user input only ever matches literally."""
    return (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


@dataclass(frozen=True, slots=True)
class UserFilters:
    search: str | None = None
    role: Role | None = None
    status: UserStatus | None = None
    sort: UserSortKey = "created_at"
    order: SortOrder = "desc"
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class UserListRow:
    user: User
    approved_by_email: str | None


@dataclass(frozen=True, slots=True)
class AccessRequestRow:
    request: AccessRequest
    user: User
    decided_by_email: str | None


class UserRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def _one(self, stmt: Select[Any], *, for_update: bool) -> User | None:
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def get(self, user_id: uuid.UUID, *, for_update: bool = False) -> User | None:
        return await self._one(
            select(User).where(User.id == user_id), for_update=for_update
        )

    async def get_by_oid(self, oid: str, *, for_update: bool = False) -> User | None:
        return await self._one(
            select(User).where(User.entra_oid == oid), for_update=for_update
        )

    async def get_by_email(
        self, email: str, *, for_update: bool = False
    ) -> User | None:
        return await self._one(
            select(User).where(func.lower(User.email) == email.strip().lower()),
            for_update=for_update,
        )

    async def lock_many(self, user_ids: Sequence[uuid.UUID]) -> list[User]:
        """Lock users in id order (a fixed order prevents deadlocks)."""
        stmt = (
            select(User)
            .where(User.id.in_(list(user_ids)))
            .order_by(User.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def lock_active_superadmins(self) -> list[uuid.UUID]:
        """Lock every active superadmin row (serialises last-superadmin checks)."""
        stmt = (
            select(User.id)
            .where(User.role == Role.SUPERADMIN.value)
            .where(User.status == UserStatus.ACTIVE.value)
            .order_by(User.id)
            .with_for_update()
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def add(self, user: User) -> User:
        self._db.add(user)
        await self._db.flush()
        return user

    async def list(self, filters: UserFilters) -> tuple[list[UserListRow], int]:
        approver = aliased(User)
        conditions = []
        if filters.search:
            pattern = f"%{escape_like(filters.search.strip().lower())}%"
            conditions.append(
                func.lower(User.email).like(pattern, escape=LIKE_ESCAPE)
                | func.lower(User.full_name).like(pattern, escape=LIKE_ESCAPE)
            )
        if filters.role is not None:
            conditions.append(User.role == filters.role.value)
        if filters.status is not None:
            conditions.append(User.status == filters.status.value)

        total = await self._db.scalar(
            select(func.count()).select_from(User).where(*conditions)
        )
        column = USER_SORT_COLUMNS[filters.sort]
        ordering = column.desc() if filters.order == "desc" else column.asc()
        stmt = (
            select(User, approver.email)
            .outerjoin(approver, approver.id == User.approved_by)
            .where(*conditions)
            .order_by(ordering.nulls_last(), User.id)
            .limit(filters.limit)
            .offset(filters.offset)
        )
        rows = (await self._db.execute(stmt)).all()
        return [UserListRow(user=r[0], approved_by_email=r[1]) for r in rows], int(
            total or 0
        )

    async def approver_email(self, user: User) -> str | None:
        if user.approved_by is None:
            return None
        return await self._db.scalar(
            select(User.email).where(User.id == user.approved_by)
        )

    async def count_by_status(self) -> dict[str, int]:
        rows = await self._db.execute(
            select(User.status, func.count()).group_by(User.status)
        )
        return {str(status): int(count) for status, count in rows.all()}

    async def active_role_mix(self) -> dict[str, int]:
        rows = await self._db.execute(
            select(User.role, func.count())
            .where(User.status == UserStatus.ACTIVE.value, User.role.is_not(None))
            .group_by(User.role)
        )
        return {str(role): int(count) for role, count in rows.all()}

    async def count_signed_in(self) -> int:
        return int(
            await self._db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.last_login_at.is_not(None))
            )
            or 0
        )


class AccessRequestRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(self, row: AccessRequest) -> AccessRequest:
        self._db.add(row)
        await self._db.flush()
        return row

    async def get(
        self, request_id: uuid.UUID, *, for_update: bool = False
    ) -> AccessRequest | None:
        stmt = select(AccessRequest).where(AccessRequest.id == request_id)
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def lock_many(self, request_ids: Sequence[uuid.UUID]) -> list[AccessRequest]:
        stmt = (
            select(AccessRequest)
            .where(AccessRequest.id.in_(list(request_ids)))
            .order_by(AccessRequest.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def pending_for_user(
        self, user_id: uuid.UUID, *, for_update: bool = False
    ) -> AccessRequest | None:
        stmt = select(AccessRequest).where(
            AccessRequest.user_id == user_id,
            AccessRequest.status == AccessRequestStatus.PENDING.value,
        )
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def latest_for_user(self, user_id: uuid.UUID) -> AccessRequest | None:
        stmt = (
            select(AccessRequest)
            .where(AccessRequest.user_id == user_id)
            .order_by(AccessRequest.created_at.desc(), AccessRequest.id)
            .limit(1)
        )
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def list(
        self, status: AccessRequestStatus | None, limit: int, offset: int
    ) -> tuple[list[AccessRequestRow], int]:
        decider = aliased(User)
        conditions = []
        if status is not None:
            conditions.append(AccessRequest.status == status.value)
        total = await self._db.scalar(
            select(func.count()).select_from(AccessRequest).where(*conditions)
        )
        # Pending queue: oldest first (longest waiting); history: newest first.
        ordering = (
            AccessRequest.created_at.asc()
            if status is AccessRequestStatus.PENDING
            else AccessRequest.created_at.desc()
        )
        stmt = (
            select(AccessRequest, User, decider.email)
            .join(User, User.id == AccessRequest.user_id)
            .outerjoin(decider, decider.id == AccessRequest.decided_by)
            .where(*conditions)
            .order_by(ordering, AccessRequest.id)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._db.execute(stmt)).all()
        return [
            AccessRequestRow(request=r[0], user=r[1], decided_by_email=r[2])
            for r in rows
        ], int(total or 0)

    async def count_pending(self) -> int:
        return int(
            await self._db.scalar(
                select(func.count())
                .select_from(AccessRequest)
                .where(AccessRequest.status == AccessRequestStatus.PENDING.value)
            )
            or 0
        )

    async def count_users_with_status(self, status: AccessRequestStatus | None) -> int:
        stmt = select(func.count(func.distinct(AccessRequest.user_id)))
        if status is not None:
            stmt = stmt.where(AccessRequest.status == status.value)
        return int(await self._db.scalar(stmt) or 0)

    async def approval_histogram(self, since: datetime) -> dict[str, int]:
        """Approved requests decided since ``since``, bucketed by wait time."""
        seconds = func.extract(
            "epoch", AccessRequest.decided_at - AccessRequest.created_at
        )
        whens = [
            (seconds < upper, label)
            for label, upper in APPROVAL_BUCKETS
            if upper is not None
        ]
        bucket = case(*whens, else_=APPROVAL_BUCKETS[-1][0]).label("bucket")
        # Bucket in a subquery: grouping by an expression with bind params
        # would not match the SELECT list in Postgres.
        labelled = (
            select(bucket)
            .where(
                AccessRequest.status == AccessRequestStatus.APPROVED.value,
                AccessRequest.decided_at.is_not(None),
                AccessRequest.decided_at >= since,
            )
            .subquery()
        )
        rows = await self._db.execute(
            select(labelled.c.bucket, func.count()).group_by(labelled.c.bucket)
        )
        found = {str(label): int(count) for label, count in rows.all()}
        return {label: found.get(label, 0) for label, _ in APPROVAL_BUCKETS}


class AuthStatsRepository:
    """Sign-in counts from the audit trail (``audit_log.created_at`` is naive UTC)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def count_events_since(self, action: str, since_naive_utc: datetime) -> int:
        return int(
            await self._db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == action, AuditLog.created_at >= since_naive_utc
                )
            )
            or 0
        )

    async def events_per_day(
        self, action: str, since_naive_utc: datetime
    ) -> dict[date, int]:
        day = cast(AuditLog.created_at, Date)
        rows = await self._db.execute(
            select(day, func.count())
            .where(AuditLog.action == action, AuditLog.created_at >= since_naive_utc)
            .group_by(day)
        )
        return {d: int(count) for d, count in rows.all()}
