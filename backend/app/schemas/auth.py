"""Request/response models for the `/auth/*` routes."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.auth.permissions import AccessRequestStatus, Permission, Role, UserStatus

JUSTIFICATION_MAX_LENGTH = 1000
# Printable text only (rejects NUL and other control characters).
SAFE_TEXT_PATTERN = r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$"


class SsoLoginRead(BaseModel):
    authorize_url: str = Field(
        description="Entra (or mock IdP) authorize URL; redirect the browser here."
    )


class SsoCallbackRequest(BaseModel):
    """What the Next `/auth/callback` handler forwards from the IdP redirect."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=4096, pattern=r"^[\x21-\x7e]+$")
    state: str = Field(min_length=16, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")


class UserIdentityRead(BaseModel):
    id: uuid.UUID
    email: str
    name: str


class MeRead(BaseModel):
    user: UserIdentityRead
    role: Role | None
    status: UserStatus
    permissions: list[Permission] = Field(
        description="What the API will allow; the UI hides everything else."
    )
    version: int = Field(description="Bumped on every role or status change.")
    access_contact_email: str | None = Field(
        description="Admin contact for the Access Required / suspended pages."
    )


class SessionIssuedRead(BaseModel):
    """Returned to the Next callback handler only; it sets the cookie."""

    session: str = Field(description="Raw session id; store it in the httpOnly cookie.")
    cookie_name: str
    cookie_secure: bool
    expires_at: datetime
    me: MeRead


class AccessRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_role: Role | None = None
    justification: str | None = Field(
        default=None, max_length=JUSTIFICATION_MAX_LENGTH, pattern=SAFE_TEXT_PATTERN
    )


class AccessRequestRead(BaseModel):
    id: uuid.UUID
    status: AccessRequestStatus
    requested_role: Role | None
    justification: str | None
    created_at: datetime
    decided_at: datetime | None
    decision_note: str | None


class AccessRequestStatusRead(BaseModel):
    user_status: UserStatus
    role: Role | None
    request: AccessRequestRead | None = Field(
        description="The most recent request, or null if none was made."
    )
    access_contact_email: str | None
