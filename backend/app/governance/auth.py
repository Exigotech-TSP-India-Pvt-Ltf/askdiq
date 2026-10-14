"""API-key auth: one shared key (no per-user accounts) gates every
protected route. Using `Security(api_key_header)` registers an OpenAPI
security scheme, which is what makes the "Authorize" button appear in
Swagger UI (top right of /docs) — paste the key there once and every
"Try it out" call sends it automatically.

Since there are no accounts, `X-Client-Id` (set by the frontend, persisted
in localStorage) is what scopes each browser to its own chat sessions —
without it, every visitor would share one identity and see each other's
chat history. It's NOT a security boundary (client-controlled, spoofable)
— only `X-API-Key` gates access; `X-Client-Id` just partitions sessions.
"""

import re

from fastapi import Header, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import get_settings

settings = get_settings()

DEFAULT_ACTOR = "default@local"

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Allows UUID v4 or safe alphanumeric strings with dash/underscore/dot/at, max 64 chars.
_SAFE_CLIENT_ID_RE = re.compile(
    r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[a-zA-Z0-9_\-\.@]{1,64})$"
)


def sanitize_client_id(client_id: str | None) -> str:
    """Validate client_id format; normalize to DEFAULT_ACTOR if invalid/suspicious."""
    if not client_id:
        return DEFAULT_ACTOR
    cleaned = client_id.strip()
    if len(cleaned) <= 64 and _SAFE_CLIENT_ID_RE.match(cleaned):
        return cleaned
    return DEFAULT_ACTOR


async def get_current_actor(
    api_key: str | None = Security(api_key_header),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> str:
    if not settings.api_key_name or api_key != settings.api_key_name:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Access Denied!")
    return sanitize_client_id(x_client_id)
