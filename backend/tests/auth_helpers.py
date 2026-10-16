"""Helpers for tests that use real SSO sessions (marker ``real_auth``)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CSRF_HEADER_VALUE
from app.auth.mock_idp import MockIdentity, provider
from app.auth.oidc import OidcClient, get_oidc_client
from app.auth.sessions import SessionService
from app.core.config import get_settings
from app.core.state import MemoryState, set_state_backend
from app.models.db_models import User


async def make_user(
    db: AsyncSession,
    *,
    role: str | None = None,
    status: str = "pending",
    email: str | None = None,
) -> User:
    email = email or f"user-{uuid.uuid4().hex[:12]}@example.com"
    user = User(
        full_name=email.split("@")[0],
        email=email,
        role=role,
        status=status,
        entra_oid=str(uuid.uuid4()),
        tenant_id=get_settings().entra_tenant_id,
    )
    db.add(user)
    await db.flush()
    return user


async def session_headers(
    db: AsyncSession, user: User, *, csrf: bool = True
) -> dict[str, str]:
    """Headers the Next proxy sends for this user's session."""
    issued = await SessionService(db).create_session(user.id)
    await db.commit()
    headers = {"X-Session": issued.token}
    if csrf:
        headers |= {
            "X-Requested-With": CSRF_HEADER_VALUE,
            "Origin": get_settings().app_origin,
        }
    return headers


# ---------------------------------------------------------------------------
# Full SSO sign-in through the mock IdP (PR-07 route tests)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def mock_idp_sign_in() -> AsyncIterator[None]:
    """Route ``/auth/sso/*`` to the in-process mock IdP with fresh state.

    The app's OIDC client fetches discovery/JWKS and exchanges codes over
    HTTP; here that HTTP goes to the mock IdP mounted on the same app
    (``AUTH_DEV_MOCK=true``), so the real validation code runs end to end.
    """
    from main import app  # noqa: PLC0415 - avoid importing the app at module load

    provider.reset()
    set_state_backend(MemoryState())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        oidc = OidcClient(get_settings(), http_client=http)
        app.dependency_overrides[get_oidc_client] = lambda: oidc
        try:
            yield
        finally:
            app.dependency_overrides.pop(get_oidc_client, None)
            provider.reset()
            set_state_backend(None)


async def authorize(
    client: httpx.AsyncClient, identity: MockIdentity
) -> dict[str, str]:
    """Run the browser half of the flow; returns the callback ``code``/``state``."""
    provider.auto_identity = identity
    login = await client.get("/auth/sso/login")
    assert login.status_code == 200, login.text
    redirect = await client.get(login.json()["authorize_url"])
    assert redirect.status_code == 302, redirect.text
    query = parse_qs(urlsplit(redirect.headers["location"]).query)
    return {"code": query["code"][0], "state": query["state"][0]}


async def sso_sign_in(
    client: httpx.AsyncClient,
    identity: MockIdentity,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Full sign-in; returns the ``POST /auth/sso/callback`` response."""
    body = await authorize(client, identity)
    return await client.post("/auth/sso/callback", json=body, headers=headers or {})


def proxy_headers(token: str) -> dict[str, str]:
    """What the Next proxy sends for a session cookie value."""
    return {
        "X-Session": token,
        "X-Requested-With": CSRF_HEADER_VALUE,
        "Origin": get_settings().app_origin,
    }


async def signed_in(client: httpx.AsyncClient, identity: MockIdentity) -> dict:
    response = await sso_sign_in(client, identity)
    assert response.status_code == 200, response.text
    body = response.json()
    body["headers"] = proxy_headers(body["session"])
    return body
