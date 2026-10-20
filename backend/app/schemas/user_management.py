"""Request/response models for User Management (`/admin/users-accounts`,
`/admin/access-requests`)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.auth.permissions import AccessRequestStatus, Role, UserStatus
from app.schemas.auth import SAFE_TEXT_PATTERN

NOTE_MAX_LENGTH = 1000
BULK_APPROVE_MAX = 100
NAME_MAX_LENGTH = 256


class UserAccountRead(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: Role | None
    status: UserStatus
    last_login_at: datetime | None
    created_at: datetime | None
    approved_by_email: str | None
    approved_at: datetime | None
    version: int = Field(description="Send back on PATCH (optimistic concurrency).")


class UserAccountPage(BaseModel):
    items: list[UserAccountRead]
    total: int
    limit: int
    offset: int


class AuthSessionRead(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip_address: str | None
    user_agent: str | None


class AuthAuditEntry(BaseModel):
    """One auth audit row (the `/governance/audit-log` entry shape)."""

    id: str
    request_id: str
    actor: str
    action: str
    detail: dict
    created_at: datetime
    ip_address: str | None = None


class UserAccountDetail(UserAccountRead):
    active_sessions: list[AuthSessionRead]
    recent_activity: list[AuthAuditEntry] = Field(
        default_factory=list,
        description="Newest auth events this user performed (needs `audit:auth:read`).",
    )
    recent_audit: list[AuthAuditEntry] = Field(
        default_factory=list,
        description="Newest auth events about this user (needs `audit:auth:read`).",
    )


class UserAccountUpdate(BaseModel):
    """Change role and/or status; ``version`` must match the current row."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    role: Role | None = None
    status: Literal[UserStatus.ACTIVE, UserStatus.SUSPENDED] | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> UserAccountUpdate:
        if self.role is None and self.status is None:
            raise ValueError("Provide a role and/or a status.")
        return self


class UserInviteCreate(BaseModel):
    """Pre-provision an account; the person signs in with SSO later."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=320)
    role: Role
    name: str | None = Field(
        default=None, max_length=NAME_MAX_LENGTH, pattern=SAFE_TEXT_PATTERN
    )


class RevokeSessionsRead(BaseModel):
    revoked: int


class AccessRequestAdminRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str
    user_name: str
    user_status: UserStatus
    requested_role: Role | None
    justification: str | None
    status: AccessRequestStatus
    created_at: datetime
    decided_at: datetime | None
    decided_by_email: str | None
    decision_note: str | None
    waiting_seconds: int | None = Field(
        description="Seconds since the request was made (pending requests only)."
    )


class AccessRequestPage(BaseModel):
    items: list[AccessRequestAdminRead]
    total: int
    limit: int
    offset: int


class AccessRequestApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role


class AccessRequestReject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(
        min_length=1, max_length=NOTE_MAX_LENGTH, pattern=SAFE_TEXT_PATTERN
    )


class AccessRequestBulkApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[uuid.UUID] = Field(min_length=1, max_length=BULK_APPROVE_MAX)
    role: Role

    @model_validator(mode="after")
    def _unique_ids(self) -> AccessRequestBulkApprove:
        if len(set(self.ids)) != len(self.ids):
            raise ValueError("ids must be unique.")
        return self


class BulkApproveRead(BaseModel):
    approved: int
    ids: list[uuid.UUID]


class UserKpis(BaseModel):
    total: int
    active: int
    pending: int
    rejected: int
    suspended: int
    open_requests: int
    logins_24h: int


class DailyCount(BaseModel):
    date: date
    count: int


class FunnelStage(BaseModel):
    stage: Literal["signed_in", "requested", "approved", "active"]
    count: int


class RoleCount(BaseModel):
    role: Role
    count: int


class HistogramBucket(BaseModel):
    bucket: str
    count: int


class UserStatsRead(BaseModel):
    days: int
    kpis: UserKpis
    logins_per_day: list[DailyCount]
    funnel: list[FunnelStage]
    role_mix: list[RoleCount]
    time_to_approve: list[HistogramBucket]
    events_per_day: dict[str, list[DailyCount]] = Field(
        default_factory=dict,
        description=(
            "Daily counts (UTC days, zero-filled, oldest first) of the auth "
            "events behind the KPI sparklines over the last "
            "`events_days` days, keyed by event."
        ),
    )
    events_days: int = 0


class AuthAuditPage(BaseModel):
    """Keyset page of auth events, newest first."""

    items: list[AuthAuditEntry]
    next_cursor: str | None = Field(
        default=None, description="Pass back as `cursor` for the next (older) page."
    )
    has_more: bool
    total: int = Field(description="Matching events (exact up to the count cap).")
    total_is_estimate: bool = False
