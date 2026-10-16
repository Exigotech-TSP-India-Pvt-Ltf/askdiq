"""The authenticated user resolved for one request."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.auth.permissions import Permission, Role, UserStatus, permissions_for


@dataclass(frozen=True, slots=True)
class AuthUser:
    """Snapshot of the user row loaded on every request (status is never cached)."""

    id: uuid.UUID
    email: str
    name: str
    role: Role | None
    status: UserStatus
    version: int
    session_id: uuid.UUID | None = None

    @property
    def is_active(self) -> bool:
        return self.status is UserStatus.ACTIVE and self.role is not None

    @property
    def permissions(self) -> frozenset[Permission]:
        """Only an active user with a role holds permissions."""
        return permissions_for(self.role) if self.is_active else frozenset()

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions
