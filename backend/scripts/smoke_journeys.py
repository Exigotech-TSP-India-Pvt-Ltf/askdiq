#!/usr/bin/env python3
"""Full-stack sign-in smoke journeys through the frontend.

Drives the real stack on port 3000 with an ``httpx.AsyncClient`` cookie jar,
against the mock IdP (``AUTH_DEV_MOCK=true``, ``ENV=development``):

1. ``GET /login`` renders.
2. ``GET /api/proxy/auth/sso/login`` returns the authorize URL.
3. The mock authorize page is submitted for an email and redirects to
   ``/auth/callback?code=...&state=...``.
4. The Next callback handler sets the session cookie (must be ``HttpOnly``).
5. ``GET /api/proxy/auth/me`` returns the user.
6. A pending user gets 403 on ``/api/proxy/admin/dashboard``; a superadmin
   (an email in the backend's ``SUPERADMIN_EMAILS``) gets 200.
7. ``POST /api/proxy/auth/logout`` invalidates the cookie (``/auth/me`` -> 401).

Reusable: ``backend/scripts/smoke_e2e.py`` (PR-01) calls
``await run_signin_journeys(client_factory, base_url, superadmin_email)``
after its health checks and fails when any returned check failed. Standalone:

    cd backend
    venv\\Scripts\\python.exe scripts\\smoke_journeys.py --base-url http://localhost:3000

Needs the frontend side from PR-18 (``/login``, ``/auth/callback`` handler,
proxy forwarding ``/mock-idp/*`` in development). Never prints cookie or
session values.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

DEFAULT_BASE_URL = "http://localhost:3000"
DEFAULT_COOKIE_NAME = "askdiq_session"
TIMEOUT_SECONDS = 20.0
HTTP_OK = 200
HTTP_NO_CONTENT = 204
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
REDIRECTS = frozenset({301, 302, 303, 307, 308})


@dataclass
class Check:
    name: str
    ok: bool
    note: str = ""


@dataclass
class Journey:
    label: str
    checks: list[Check] = field(default_factory=list)

    def record(self, name: str, ok: bool, note: str = "") -> bool:
        self.checks.append(Check(f"{self.label}: {name}", ok, note))
        return ok

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


class _FormParser(HTMLParser):
    """Collects the mock authorize form's action and hidden inputs."""

    def __init__(self) -> None:
        super().__init__()
        self.action: str | None = None
        self.hidden: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v or "" for k, v in attrs}
        if tag == "form" and self.action is None:
            self.action = values.get("action", "")
        if tag == "input" and values.get("type") == "hidden" and values.get("name"):
            self.hidden[values["name"]] = values.get("value", "")


def _origin(base_url: str) -> str:
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}"


def _cookie_is_http_only(response: httpx.Response, cookie_name: str) -> bool:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{cookie_name}="):
            return "httponly" in header.lower()
    return False


async def _follow_to_callback(
    client: httpx.AsyncClient, authorize_url: str, email: str, journey: Journey
) -> httpx.Response | None:
    """Submit the mock "pick a user" form; return the redirect to /auth/callback."""
    page = await client.get(authorize_url)
    if page.status_code in REDIRECTS:  # auto-approve mode
        return page
    if not journey.record("mock authorize page", page.status_code == HTTP_OK):
        return None
    parser = _FormParser()
    parser.feed(page.text)
    action = urljoin(str(page.url), parser.action or "")
    form = {**parser.hidden, "email": email, "name": email.split("@", maxsplit=1)[0]}
    return await client.post(action, data=form)


async def sign_in(
    client: httpx.AsyncClient,
    base_url: str,
    email: str,
    journey: Journey,
    cookie_name: str = DEFAULT_COOKIE_NAME,
) -> bool:
    """Steps 1-5: sign in through the frontend; the cookie lands in the jar."""
    login_page = await client.get(f"{base_url}/login")
    journey.record("GET /login", login_page.status_code == HTTP_OK)
    start = await client.get(f"{base_url}/api/proxy/auth/sso/login")
    if not journey.record(
        "GET /api/proxy/auth/sso/login", start.status_code == HTTP_OK
    ):
        return False
    redirect = await _follow_to_callback(
        client, start.json()["authorize_url"], email, journey
    )
    if redirect is None or not journey.record(
        "mock IdP redirects to /auth/callback",
        redirect.status_code in REDIRECTS
        and urlsplit(redirect.headers.get("location", "")).path == "/auth/callback",
    ):
        return False
    callback = await client.get(redirect.headers["location"])
    journey.record(
        "callback sets an HttpOnly session cookie",
        _cookie_is_http_only(callback, cookie_name),
    )
    me = await client.get(f"{base_url}/api/proxy/auth/me")
    return journey.record(
        "GET /api/proxy/auth/me returns the user",
        me.status_code == HTTP_OK and me.json().get("user", {}).get("email") == email,
    )


async def logout(client: httpx.AsyncClient, base_url: str, journey: Journey) -> None:
    """Step 7: sign out; the old cookie no longer works."""
    response = await client.post(
        f"{base_url}/api/proxy/auth/logout", headers={"Origin": _origin(base_url)}
    )
    accepted = {HTTP_OK, HTTP_NO_CONTENT, *REDIRECTS}
    journey.record("POST /api/proxy/auth/logout", response.status_code in accepted)
    me = await client.get(f"{base_url}/api/proxy/auth/me")
    journey.record("cookie invalid after logout", me.status_code == HTTP_UNAUTHORIZED)


async def _journey(
    client_factory: Callable[[], httpx.AsyncClient],
    base_url: str,
    label: str,
    email: str,
    expected_dashboard_status: int,
) -> Journey:
    journey = Journey(label)
    async with client_factory() as client:
        if await sign_in(client, base_url, email, journey):
            dashboard = await client.get(f"{base_url}/api/proxy/admin/dashboard")
            journey.record(
                f"/api/proxy/admin/dashboard is {expected_dashboard_status}",
                dashboard.status_code == expected_dashboard_status,
                f"got {dashboard.status_code}",
            )
            await logout(client, base_url, journey)
    return journey


def default_client() -> httpx.AsyncClient:
    """Cookie jar on, redirects handled by hand so each hop is checked."""
    return httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False)


async def run_signin_journeys(
    client_factory: Callable[[], httpx.AsyncClient] = default_client,
    base_url: str = DEFAULT_BASE_URL,
    superadmin_email: str = "",
) -> list[Check]:
    """Pending-user and superadmin journeys; returns every check."""
    base_url = base_url.rstrip("/")
    pending_email = f"smoke-{uuid.uuid4().hex[:10]}@example.com"
    journeys = [
        await _journey(
            client_factory, base_url, "pending user", pending_email, HTTP_FORBIDDEN
        )
    ]
    if superadmin_email:
        journeys.append(
            await _journey(
                client_factory, base_url, "superadmin", superadmin_email, HTTP_OK
            )
        )
    else:
        journeys.append(Journey("superadmin", [Check("superadmin: email set", False)]))
    return [check for j in journeys for check in j.checks]


def _first_superadmin() -> str:
    raw = os.environ.get("SUPERADMIN_EMAILS", "")
    return next((e.strip() for e in raw.split(",") if e.strip()), "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--superadmin-email",
        default=_first_superadmin(),
        help="An email in the backend's SUPERADMIN_EMAILS (default: the first one).",
    )
    args = parser.parse_args()
    checks = asyncio.run(
        run_signin_journeys(default_client, args.base_url, args.superadmin_email)
    )
    for check in checks:
        status = "PASS" if check.ok else "FAIL"
        suffix = f" ({check.note})" if check.note and not check.ok else ""
        print(f"{status} {check.name}{suffix}")
    return 0 if checks and all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
