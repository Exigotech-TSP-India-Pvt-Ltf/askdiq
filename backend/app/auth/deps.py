"""Authorization dependencies and the proxy contract (B2.3.3, B2.3.5, B2.5).

Proxy contract: the Next.js proxy is the only intended caller. On every
request it must send:

- ``X-API-Key``: the service key (``API_KEY_NAME``). Proves the request came
  from our proxy. Missing or wrong gives 401, even with a valid session, so
  the backend cannot be used directly from a browser or another host.
- ``X-Session``: the raw session id from the httpOnly session cookie. Missing,
  unknown, expired or revoked gives 401.
- ``X-Requested-With: askdiq`` and the browser's ``Origin`` on state-changing
  methods (POST/PUT/PATCH/DELETE). Anything else gives 403 (CSRF defence; the
  origin must equal ``APP_BASE_URL``'s origin).
- ``X-Forwarded-For``: the client IP; honoured only when
  ``TRUST_PROXY_HEADERS=true`` (set it in deployment behind the proxy).
- ``X-AskDIQ-Proxy: 1``: marks the request as coming from the proxy, so it can
  never fall through to the website (API-key) chat path below.

Website contract (docs/technical/10-website-integration.md): the public website
calls the chat routes (``/query``, ``/sessions``, feedback) server-to-server
with ``X-API-Key`` and an optional per-visitor ``X-Client-Id`` and no session.
``require_chat_actor`` accepts either caller: a session (actor = email, CSRF
enforced as above) or the service key alone (actor = the sanitised client id,
never email-shaped, so a visitor id can never name an SSO user's sessions).

Dependency family (deny by default):

- ``current_user``: service key + session; any status (used by ``/auth/me``).
- ``require_role_or_chat``: signed in and not suspended.
- ``require_chat_actor``: chat routes; a session as above, or the website's
  service key alone (see "Website contract").
- ``require_active_user``: active with a role (any dashboard route).
- ``require_permission(p)``: active and the role grants ``p``.

The user row is loaded on every request, so a role or status change
(including suspension) applies to the very next request.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from functools import cache
from typing import Any, TypeVar

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import CHAT_STATUSES, Permission, UserStatus
from app.auth.principal import AuthUser
from app.auth.sessions import SessionService
from app.core.config import get_settings
from app.core.database import get_db
from app.governance.auth import DEFAULT_ACTOR, sanitize_client_id

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "x-requested-with"
CSRF_HEADER_VALUE = "askdiq"
SESSION_HEADER = "x-session"
PROXY_HEADER = "x-askdiq-proxy"
GUARD_ATTR = "__auth_guard__"
PERMISSION_ATTR = "__auth_permission__"

F = TypeVar("F", bound=Callable[..., Any])

DETAIL_SERVICE_KEY = "Access Denied!"  # unchanged legacy text; clients match on it
DETAIL_UNAUTHENTICATED = "Not authenticated."
DETAIL_CSRF = "Cross-site request rejected."
DETAIL_SUSPENDED = "Account suspended."
DETAIL_APPROVAL = "Dashboard access requires approval."
DETAIL_FORBIDDEN = "Insufficient permissions."

service_key_header = APIKeyHeader(
    name="X-API-Key",
    scheme_name="ServiceKey",
    description="Proxy-to-backend service key (API_KEY_NAME).",
    auto_error=False,
)
session_header = APIKeyHeader(
    name="X-Session",
    scheme_name="Session",
    description="Session id forwarded by the proxy from the session cookie.",
    auto_error=False,
)

client_id_header = APIKeyHeader(
    name="X-Client-Id",
    scheme_name="ClientId",
    description=(
        "Website callers only: stable per-visitor id (UUID recommended) that "
        "owns chat sessions and keys the per-visitor rate limit."
    ),
    auto_error=False,
)


def _guard(fn: F) -> F:
    setattr(fn, GUARD_ATTR, True)
    return fn


def is_guard(call: object) -> bool:
    return bool(getattr(call, GUARD_ATTR, False))


async def require_service_key(
    api_key: str | None = Security(service_key_header),
) -> None:
    expected = get_settings().api_key_name
    if (
        not expected
        or not api_key
        or not hmac.compare_digest(api_key.encode(), expected.encode())
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, DETAIL_SERVICE_KEY)


def enforce_csrf(request: Request) -> None:
    """Cookie-authenticated state changes need the proxy marker and our Origin."""
    if request.method in SAFE_METHODS:
        return
    marker = request.headers.get(CSRF_HEADER, "")
    origin = (request.headers.get("origin") or "").rstrip("/").lower()
    if marker != CSRF_HEADER_VALUE or origin != get_settings().app_origin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, DETAIL_CSRF)


async def authenticate_session(
    request: Request,
    token: str | None = Security(session_header),
    db: AsyncSession = Depends(get_db),
) -> AuthUser | None:
    """Resolve the forwarded session (the one place a session is authenticated)."""
    if not token:
        return None
    enforce_csrf(request)
    user = await SessionService(db).resolve(token)
    # Ends the lookup transaction (persists a throttled last_seen_at write)
    # and returns the connection to the pool before the handler runs.
    await db.commit()
    return user


@_guard
async def current_user(
    request: Request,
    _service: None = Depends(require_service_key),
    user: AuthUser | None = Depends(authenticate_session),
) -> AuthUser:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, DETAIL_UNAUTHENTICATED)
    request.state.auth_user = user
    return user


@_guard
async def require_role_or_chat(user: AuthUser = Depends(current_user)) -> AuthUser:
    """Chat is open to every signed-in user who is not suspended (newplan §7.2)."""
    if user.status not in CHAT_STATUSES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, DETAIL_SUSPENDED)
    return user


@_guard
async def require_active_user(user: AuthUser = Depends(current_user)) -> AuthUser:
    if user.status is UserStatus.SUSPENDED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, DETAIL_SUSPENDED)
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, DETAIL_APPROVAL)
    return user


@cache
def require_permission(permission: Permission) -> Callable[..., Awaitable[AuthUser]]:
    """Dependency enforcing ``permission`` (one callable per permission, so
    FastAPI's per-request dependency cache resolves it once)."""

    async def dependency(user: AuthUser = Depends(require_active_user)) -> AuthUser:
        if not user.has(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, DETAIL_FORBIDDEN)
        return user

    dependency.__name__ = f"require_permission_{permission.name.lower()}"
    setattr(dependency, PERMISSION_ATTR, permission)
    return _guard(dependency)


def api_key_actor(client_id: str | None) -> str:
    """Actor for a website (API-key) caller: the sanitised ``X-Client-Id``.

    SSO actors are emails, so an email-shaped client id is replaced by the
    shared default actor: a visitor id can never own an SSO user's sessions.
    """
    actor = sanitize_client_id(client_id)
    if "@" in actor and actor != DEFAULT_ACTOR:
        return DEFAULT_ACTOR
    return actor


@_guard
async def require_chat_actor(
    request: Request,
    _service: None = Depends(require_service_key),
    user: AuthUser | None = Depends(authenticate_session),
    client_id: str | None = Security(client_id_header),
) -> str:
    """Chat guard for the dashboard (session) and the website (service key).

    Both need a valid ``X-API-Key``. With ``X-Session`` the request is a
    cookie session (CSRF-checked in ``authenticate_session``) and the actor is
    the user's email. Without one, the key alone authenticates the website and
    the actor is ``api_key_actor(X-Client-Id)``. A request that carries a
    session header or the proxy marker never takes the API-key path, so an
    invalid session or an anonymous browser behind the proxy still gets 401.
    """
    if user is not None:
        request.state.auth_user = user
        if user.status not in CHAT_STATUSES:
            raise HTTPException(status.HTTP_403_FORBIDDEN, DETAIL_SUSPENDED)
        return user.email
    if SESSION_HEADER in request.headers or request.headers.get(PROXY_HEADER):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, DETAIL_UNAUTHENTICATED)
    return api_key_actor(client_id)
