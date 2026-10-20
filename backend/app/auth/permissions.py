"""Roles, statuses and the single permission table.

The API enforces these through `app.auth.deps.require_permission`; the
frontend reads the same list from `GET /auth/me`, so hiding a control in the
UI and rejecting the call in the API always agree.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from types import MappingProxyType


class Role(StrEnum):
    SUPERADMIN = "superadmin"
    ADMIN = "admin"
    OPERATOR = "operator"


class UserStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    SUSPENDED = "suspended"


class AccessRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Permission(StrEnum):
    DASHBOARD_READ = "dashboard:read"
    DOCUMENTS_WRITE = "documents:write"
    FEEDBACK_TRIAGE = "feedback:triage"
    FEEDBACK_EXPORT = "feedback:export"
    EVAL_RUN = "eval:run"
    TESTIP_WRITE = "testip:write"
    USAGE_EXPORT = "usage:export"
    USERS_MANAGE = "users:manage"
    AUDIT_AUTH_READ = "audit:auth:read"
    BRANDING_WRITE = "branding:write"
    TAXONOMY_WRITE = "taxonomy:write"
    MODELS_WRITE = "models:write"
    ALERTS_MANAGE = "alerts:manage"


_OPERATOR = frozenset({Permission.DASHBOARD_READ})
_ADMIN = _OPERATOR | {
    Permission.DOCUMENTS_WRITE,
    Permission.FEEDBACK_TRIAGE,
    Permission.FEEDBACK_EXPORT,
    Permission.EVAL_RUN,
    Permission.TESTIP_WRITE,
    Permission.USAGE_EXPORT,
    Permission.ALERTS_MANAGE,
}
_SUPERADMIN = _ADMIN | {
    Permission.USERS_MANAGE,
    Permission.AUDIT_AUTH_READ,
    Permission.BRANDING_WRITE,
    Permission.TAXONOMY_WRITE,
    Permission.MODELS_WRITE,
}

ROLE_PERMISSIONS: MappingProxyType[Role, frozenset[Permission]] = MappingProxyType(
    {
        Role.OPERATOR: _OPERATOR,
        Role.ADMIN: _ADMIN,
        Role.SUPERADMIN: _SUPERADMIN,
    }
)

# Statuses that may use Chat (signed in, not suspended).
CHAT_STATUSES = frozenset({UserStatus.ACTIVE, UserStatus.PENDING, UserStatus.REJECTED})


@lru_cache(maxsize=8)
def permissions_for(role: str | None) -> frozenset[Permission]:
    """Permissions granted by ``role`` (empty for no role or an unknown one)."""
    if role is None or role not in Role._value2member_map_:
        return frozenset()
    return ROLE_PERMISSIONS[Role(role)]
