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

from fastapi import Header, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import get_settings

settings = get_settings()

DEFAULT_ACTOR = "default@local"

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_current_actor(
    api_key: str | None = Security(api_key_header),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
) -> str:
    if not settings.api_key_name or api_key != settings.api_key_name:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Access Denied!")
    return x_client_id or DEFAULT_ACTOR
