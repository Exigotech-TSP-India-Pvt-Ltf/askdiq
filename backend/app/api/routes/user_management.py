"""User Management API (superadmin only: `users:manage`; B2.4.2, newplan §7.8).

`/admin/users-accounts` (list, detail, stats, PATCH role/status, revoke
sessions, invite) and `/admin/access-requests` (queue, approve, reject, bulk
approve). Safeguards, locking and auditing live in
``app.auth.admin_service.UserAdminService``.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin_service import (
    AUTH_EVENTS,
    UserAdminService,
    UserChange,
    UserStats,
)
from app.auth.audit import AuditContext, AuthEvent
from app.auth.deps import require_permission
from app.auth.live import STATS_DEFAULTS, USERS_STATS_KIND, remember_stats_days
from app.auth.permissions import AccessRequestStatus, Permission, Role, UserStatus
from app.auth.principal import AuthUser
from app.auth.sessions import utcnow
from app.auth.user_repository import (
    AccessRequestRow,
    AuthAuditFilters,
    SortOrder,
    UserFilters,
    UserListRow,
    UserSortKey,
)
from app.core.concurrency import ScopedRunner, get_scoped_runner
from app.core.config import get_settings
from app.core.database import get_db
from app.dashboard.snapshots import Gather, register, snapshot_response
from app.governance.pagination import Cursor
from app.models.db_models import AuditLog
from app.schemas.auth import SAFE_TEXT_PATTERN
from app.schemas.user_management import (
    AccessRequestAdminRead,
    AccessRequestApprove,
    AccessRequestBulkApprove,
    AccessRequestPage,
    AccessRequestReject,
    AuthAuditEntry,
    AuthAuditPage,
    AuthSessionRead,
    BulkApproveRead,
    DailyCount,
    FunnelStage,
    HistogramBucket,
    RevokeSessionsRead,
    RoleCount,
    UserAccountDetail,
    UserAccountPage,
    UserAccountRead,
    UserAccountUpdate,
    UserInviteCreate,
    UserKpis,
    UserStatsRead,
)

router = APIRouter(prefix="/admin", tags=["User Management"])
manage_users = require_permission(Permission.USERS_MANAGE)
read_auth_audit = require_permission(Permission.AUDIT_AUTH_READ)

PAGE_MAX = 200
OFFSET_MAX = 10_000
SEARCH_MAX_LENGTH = 100
STATS_DAYS_MAX = 90
AUDIT_PAGE_MAX = 100
AUDIT_PAGE_DEFAULT = 25
CURSOR_MAX_LENGTH = 200
FUNNEL_ORDER: tuple[Literal["signed_in", "requested", "approved", "active"], ...] = (
    "signed_in",
    "requested",
    "approved",
    "active",
)

_GUARD_ERRORS = {
    401: {"description": "Missing service key or no valid session."},
    403: {"description": "Not a superadmin, suspended, or CSRF check failed."},
}
_ERR_404 = {"description": "Not found."}
_ERR_409 = {"description": "Conflict (stale version, already decided, safeguard)."}
_ERR_422 = {"description": "Validation error."}


def user_read(row: UserListRow) -> UserAccountRead:
    user = row.user
    return UserAccountRead(
        id=user.id,
        email=user.email,
        name=user.full_name,
        role=user.role,
        status=user.status,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        approved_by_email=row.approved_by_email,
        approved_at=user.approved_at,
        version=user.version,
    )


def audit_read(row: AuditLog) -> AuthAuditEntry:
    return AuthAuditEntry(
        id=str(row.id),
        request_id=row.request_id,
        actor=row.actor,
        action=row.action,
        detail=row.detail,
        created_at=row.created_at,
        ip_address=row.ip_address,
    )


def stats_read(stats: UserStats) -> UserStatsRead:
    counts = stats.status_counts
    return UserStatsRead(
        days=stats.days,
        kpis=UserKpis(
            total=sum(counts.values()),
            active=counts.get(UserStatus.ACTIVE.value, 0),
            pending=counts.get(UserStatus.PENDING.value, 0),
            rejected=counts.get(UserStatus.REJECTED.value, 0),
            suspended=counts.get(UserStatus.SUSPENDED.value, 0),
            open_requests=stats.open_requests,
            logins_24h=stats.logins_24h,
        ),
        logins_per_day=[DailyCount(date=d, count=c) for d, c in stats.logins_per_day],
        funnel=[FunnelStage(stage=s, count=stats.funnel[s]) for s in FUNNEL_ORDER],
        role_mix=[
            RoleCount(role=r, count=stats.role_mix.get(r.value, 0)) for r in Role
        ],
        time_to_approve=[
            HistogramBucket(bucket=b, count=c) for b, c in stats.time_to_approve.items()
        ],
        events_per_day={
            action: [DailyCount(date=d, count=c) for d, c in series]
            for action, series in stats.events_per_day.items()
        },
        events_days=stats.events_days,
    )


async def _users_stats_snapshot(gather: Gather, params: dict) -> UserStatsRead:
    days = int(params["days"])
    (stats,) = await gather(lambda s: UserAdminService(s).stats(days))
    return stats_read(stats)


# Precomputed; recomputed whenever an auth change commits (app.auth.live).
register(USERS_STATS_KIND, _users_stats_snapshot, STATS_DEFAULTS)


def request_read(row: AccessRequestRow) -> AccessRequestAdminRead:
    req = row.request
    waiting = None
    if req.status == AccessRequestStatus.PENDING.value and req.created_at:
        waiting = max(0, int((utcnow() - req.created_at).total_seconds()))
    return AccessRequestAdminRead(
        id=req.id,
        user_id=req.user_id,
        user_email=row.user.email,
        user_name=row.user.full_name,
        user_status=row.user.status,
        requested_role=req.requested_role,
        justification=req.justification,
        status=req.status,
        created_at=req.created_at,
        decided_at=req.decided_at,
        decided_by_email=row.decided_by_email,
        decision_note=req.decision_note,
        waiting_seconds=waiting,
    )


# ---------------------------------------------------------------- users


@router.get(
    "/users-accounts",
    response_model=UserAccountPage,
    summary="List user accounts",
    description="Paginated; search matches email or name (literal, case-insensitive).",
    responses={**_GUARD_ERRORS, 422: _ERR_422},
)
async def list_user_accounts(
    search: str | None = Query(
        default=None, max_length=SEARCH_MAX_LENGTH, pattern=SAFE_TEXT_PATTERN
    ),
    role: Role | None = Query(default=None),
    status_filter: UserStatus | None = Query(default=None, alias="status"),
    sort: UserSortKey = Query(default="created_at"),
    order: SortOrder = Query(default="desc"),
    limit: int = Query(default=50, ge=1, le=PAGE_MAX),
    offset: int = Query(default=0, ge=0, le=OFFSET_MAX),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(manage_users),
) -> UserAccountPage:
    rows, total = await UserAdminService(db).list_users(
        UserFilters(
            search=search or None,
            role=role,
            status=status_filter,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )
    )
    return UserAccountPage(
        items=[user_read(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get(
    "/users-accounts/stats",
    response_model=UserStatsRead,
    summary="User Management KPIs and chart data",
    description=(
        "KPIs, sign-ins per day, request funnel, active role mix, the "
        "time-to-approve histogram for the last `days` days and the daily "
        "auth-event series behind the KPI sparklines. Precomputed "
        "(`dashboard_snapshots`) and recomputed whenever a sign-in, request, "
        "decision or user change commits; `X-Data-As-Of` is its age."
    ),
    responses={**_GUARD_ERRORS, 304: {"description": "ETag matched"}, 422: _ERR_422},
)
async def user_account_stats(
    request: Request,
    days: int = Query(default=30, ge=1, le=STATS_DAYS_MAX),
    runner: ScopedRunner = Depends(get_scoped_runner),
    user: AuthUser = Depends(manage_users),
) -> Response:
    remember_stats_days(days)
    return await snapshot_response(
        request, USERS_STATS_KIND, {"days": days}, runner.gather
    )


@router.get(
    "/auth-audit",
    response_model=AuthAuditPage,
    summary="Auth audit trail (keyset pages)",
    description=(
        "Sign-ins, denials, access decisions, role changes, suspensions and "
        "revocations, newest first, `limit` per page; pass `next_cursor` back "
        "as `cursor` for older events. `action` filters one event; `search` "
        "matches the actor or the (masked) target email, case-insensitive. "
        "Needs `audit:auth:read`."
    ),
    responses={**_GUARD_ERRORS, 422: _ERR_422},
)
async def list_auth_audit(
    limit: int = Query(default=AUDIT_PAGE_DEFAULT, ge=1, le=AUDIT_PAGE_MAX),
    cursor: str | None = Query(default=None, max_length=CURSOR_MAX_LENGTH),
    action: AuthEvent | None = Query(default=None),
    search: str | None = Query(
        default=None, max_length=SEARCH_MAX_LENGTH, pattern=SAFE_TEXT_PATTERN
    ),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(read_auth_audit),
) -> AuthAuditPage:
    filters = AuthAuditFilters(
        actions=(action.value,) if action else AUTH_EVENTS,
        limit=limit,
        cursor=Cursor.decode(cursor),
        search=(search or "").strip() or None,
    )
    rows, has_more, total = await UserAdminService(db).auth_audit(
        filters, get_settings().governance_exact_count_cap
    )
    last = rows[-1] if has_more and rows else None
    return AuthAuditPage(
        items=[audit_read(r) for r in rows],
        next_cursor=Cursor(last.created_at, last.id).encode() if last else None,
        has_more=has_more,
        total=total.value,
        total_is_estimate=total.is_estimate,
    )


@router.post(
    "/users-accounts/invite",
    response_model=UserAccountRead,
    status_code=status.HTTP_201_CREATED,
    summary="Invite (pre-provision) a user by email",
    description=(
        "Creates an active account with the given role; it is linked to the "
        "person's Entra identity on their first SSO sign-in."
    ),
    responses={
        **_GUARD_ERRORS,
        409: {"description": "Email already exists."},
        422: _ERR_422,
    },
)
async def invite_user(
    body: UserInviteCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(manage_users),
) -> UserAccountRead:
    row = await UserAdminService(db).invite(
        user, str(body.email), body.role, body.name, AuditContext.from_request(request)
    )
    return user_read(row)


@router.get(
    "/users-accounts/{user_id}",
    response_model=UserAccountDetail,
    summary="User account detail with active sessions and recent auth events",
    responses={**_GUARD_ERRORS, 404: _ERR_404, 422: _ERR_422},
)
async def get_user_account(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(manage_users),
) -> UserAccountDetail:
    detail = await UserAdminService(db).get_user(
        user_id, with_audit=user.has(Permission.AUDIT_AUTH_READ)
    )
    return UserAccountDetail(
        **user_read(detail.row).model_dump(),
        active_sessions=[
            AuthSessionRead(
                id=s.id,
                created_at=s.created_at,
                last_seen_at=s.last_seen_at,
                expires_at=s.expires_at,
                ip_address=s.ip_address,
                user_agent=s.user_agent,
            )
            for s in detail.sessions
        ],
        recent_activity=[audit_read(r) for r in detail.recent_activity],
        recent_audit=[audit_read(r) for r in detail.recent_audit],
    )


@router.patch(
    "/users-accounts/{user_id}",
    response_model=UserAccountRead,
    summary="Change a user's role and/or status",
    description=(
        "Send the `version` you read; a mismatch gives 409. You cannot change "
        "your own role or status, and the last active superadmin cannot be "
        "demoted or suspended (409). Takes effect on the user's next request."
    ),
    responses={**_GUARD_ERRORS, 404: _ERR_404, 409: _ERR_409, 422: _ERR_422},
)
async def update_user_account(
    user_id: uuid.UUID,
    body: UserAccountUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(manage_users),
) -> UserAccountRead:
    row = await UserAdminService(db).update_user(
        user,
        user_id,
        UserChange(version=body.version, role=body.role, status=body.status),
        AuditContext.from_request(request),
    )
    return user_read(row)


@router.post(
    "/users-accounts/{user_id}/revoke-sessions",
    response_model=RevokeSessionsRead,
    summary="Force sign-out: revoke all of a user's sessions",
    responses={**_GUARD_ERRORS, 404: _ERR_404, 422: _ERR_422},
)
async def revoke_user_sessions(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(manage_users),
) -> RevokeSessionsRead:
    revoked = await UserAdminService(db).revoke_sessions(
        user, user_id, AuditContext.from_request(request)
    )
    return RevokeSessionsRead(revoked=revoked)


# ------------------------------------------------------- access requests


@router.get(
    "/access-requests",
    response_model=AccessRequestPage,
    summary="Access request queue and decision history",
    description="`status=pending` (default) is oldest first; other views newest first.",
    responses={**_GUARD_ERRORS, 422: _ERR_422},
)
async def list_access_requests(
    status_filter: AccessRequestStatus | Literal["all"] = Query(
        default=AccessRequestStatus.PENDING, alias="status"
    ),
    limit: int = Query(default=50, ge=1, le=PAGE_MAX),
    offset: int = Query(default=0, ge=0, le=OFFSET_MAX),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(manage_users),
) -> AccessRequestPage:
    wanted = None if status_filter == "all" else AccessRequestStatus(status_filter)
    rows, total = await UserAdminService(db).list_requests(wanted, limit, offset)
    return AccessRequestPage(
        items=[request_read(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.post(
    "/access-requests/bulk-approve",
    response_model=BulkApproveRead,
    summary="Approve several requests with one role (all-or-nothing)",
    responses={**_GUARD_ERRORS, 404: _ERR_404, 409: _ERR_409, 422: _ERR_422},
)
async def bulk_approve_access_requests(
    body: AccessRequestBulkApprove,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(manage_users),
) -> BulkApproveRead:
    ids = await UserAdminService(db).bulk_approve(
        user, body.ids, body.role, AuditContext.from_request(request)
    )
    return BulkApproveRead(approved=len(ids), ids=ids)


@router.post(
    "/access-requests/{request_id}/approve",
    response_model=AccessRequestAdminRead,
    summary="Approve an access request with a role",
    responses={**_GUARD_ERRORS, 404: _ERR_404, 409: _ERR_409, 422: _ERR_422},
)
async def approve_access_request(
    request_id: uuid.UUID,
    body: AccessRequestApprove,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(manage_users),
) -> AccessRequestAdminRead:
    row = await UserAdminService(db).approve(
        user, request_id, body.role, AuditContext.from_request(request)
    )
    return request_read(row)


@router.post(
    "/access-requests/{request_id}/reject",
    response_model=AccessRequestAdminRead,
    summary="Reject an access request with a note",
    responses={**_GUARD_ERRORS, 404: _ERR_404, 409: _ERR_409, 422: _ERR_422},
)
async def reject_access_request(
    request_id: uuid.UUID,
    body: AccessRequestReject,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(manage_users),
) -> AccessRequestAdminRead:
    row = await UserAdminService(db).reject(
        user, request_id, body.note, AuditContext.from_request(request)
    )
    return request_read(row)
