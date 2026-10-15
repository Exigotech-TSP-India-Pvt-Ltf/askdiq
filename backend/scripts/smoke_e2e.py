"""Full-stack smoke test (no browser): Next.js frontend -> proxy -> FastAPI.

Run after the backend (uvicorn) and the frontend (`next start`) are up:

    python scripts/smoke_e2e.py --frontend-url http://localhost:3000 \
        --backend-url http://127.0.0.1:8000

Base checks always run. Later PRs register sign-in and role journeys in
``JOURNEYS`` and select them with ``--journey <name>`` (repeatable). One
cookie-carrying ``httpx.AsyncClient`` is shared by every step so a journey
can sign in once and reuse the session cookie.

``--journey signin`` (PR-18, planchanges4 F1.5) drives the SSO sign-in
through port 3000 against the backend's mock Entra IdP, exactly as a browser
would: ``/login`` -> ``/auth/login`` (state cookie) -> ``/mock-idp`` authorize
form -> ``/auth/callback`` (httpOnly session cookie) -> ``/api/proxy/auth/me``
-> a pending user gets 403 on a dashboard API -> a forged ``state`` is
rejected -> sign out invalidates the session. It needs the ``/auth`` routes
(PR-07) and both servers in mock mode, so it is opt-in and not run by CI
until that backend is in the base:

    backend:  ENV=development AUTH_DEV_MOCK=true APP_BASE_URL=http://localhost:3000
    frontend: AUTH_DEV_MOCK=true (forwards /mock-idp/* to the backend)

    python scripts/smoke_e2e.py --journey signin [--signin-email new@example.com]

Never logs cookie, state or session values.

Exits 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

DEFAULT_FRONTEND_URL = "http://localhost:3000"
DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 15.0
HTTP_OK = 200
HTTP_NO_CONTENT = 204
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_SERVER_ERROR = 500
HTTP_TEMPORARY_REDIRECT = 307
HTTP_PERMANENT_REDIRECT = 308
EVALUATION_LIVE_PATH = "/admin/evaluation-live"
# Retired routes that must redirect to Evaluation (Live) (newplan §2.3).
LEGACY_EVALUATION_PATHS = ("/admin/evaluation", "/evaluation")
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
SESSION_COOKIE = "askdiq_session"
STATE_COOKIE = "askdiq_oidc_state"
SIGNIN_NEXT = "/admin"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("smoke_e2e")


@dataclass(frozen=True)
class SmokeContext:
    client: httpx.AsyncClient
    frontend_url: str
    backend_url: str
    signin_email: str = ""
    # Values handed from one journey step to the next (never logged).
    state: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


Step = Callable[[SmokeContext], Awaitable[CheckResult]]


async def _expect_status(
    ctx: SmokeContext,
    name: str,
    url: str,
    accept: Callable[[int], bool],
    expectation: str,
) -> CheckResult:
    try:
        response = await ctx.client.get(url)
    except httpx.HTTPError as exc:
        return CheckResult(name, False, f"GET {url} failed: {exc!r}")
    ok = accept(response.status_code)
    detail = f"GET {url} -> {response.status_code} (expected {expectation})"
    return CheckResult(name, ok, detail)


async def check_frontend_home(ctx: SmokeContext) -> CheckResult:
    return await _expect_status(
        ctx,
        "frontend home",
        f"{ctx.frontend_url}/",
        lambda code: code < HTTP_SERVER_ERROR,
        f"< {HTTP_SERVER_ERROR}",
    )


async def check_proxy_health(ctx: SmokeContext) -> CheckResult:
    return await _expect_status(
        ctx,
        "frontend proxy -> backend /health",
        f"{ctx.frontend_url}/api/proxy/health",
        lambda code: code == HTTP_OK,
        str(HTTP_OK),
    )


async def check_backend_health(ctx: SmokeContext) -> CheckResult:
    return await _expect_status(
        ctx,
        "backend /health",
        f"{ctx.backend_url}/health",
        lambda code: code == HTTP_OK,
        str(HTTP_OK),
    )


async def _expect_redirect(ctx: SmokeContext, path: str, target: str) -> CheckResult:
    """GET ``path`` without following redirects; expect 307/308 to ``target``."""
    name = f"legacy redirect {path} -> {target}"
    url = f"{ctx.frontend_url}{path}"
    try:
        response = await ctx.client.get(url, follow_redirects=False)
    except httpx.HTTPError as exc:
        return CheckResult(name, False, f"GET {url} failed: {exc!r}")
    location = response.headers.get("location", "")
    ok = (
        response.status_code in {HTTP_TEMPORARY_REDIRECT, HTTP_PERMANENT_REDIRECT}
        and httpx.URL(location).path == target
    )
    detail = (
        f"GET {url} -> {response.status_code} location={location!r} "
        f"(expected {HTTP_TEMPORARY_REDIRECT}/{HTTP_PERMANENT_REDIRECT} to {target})"
    )
    return CheckResult(name, ok, detail)


async def check_legacy_evaluation_redirects(ctx: SmokeContext) -> CheckResult:
    results = [
        await _expect_redirect(ctx, path, EVALUATION_LIVE_PATH)
        for path in LEGACY_EVALUATION_PATHS
    ]
    failed = [r for r in results if not r.ok]
    return CheckResult(
        "legacy evaluation redirects",
        not failed,
        "; ".join(r.detail for r in (failed or results)),
    )


BASE_CHECKS: tuple[Step, ...] = (
    check_backend_health,
    check_frontend_home,
    check_proxy_health,
    check_legacy_evaluation_redirects,
)

# ---------------------------------------------------------------- signin


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


def _set_cookie(response: httpx.Response, name: str) -> str:
    """The Set-Cookie header for ``name`` (empty when absent)."""
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    return ""


def _has_flags(cookie: str, *flags: str) -> bool:
    lowered = cookie.lower()
    return bool(cookie) and all(flag in lowered for flag in flags)


def _location(response: httpx.Response) -> str:
    return response.headers.get("location", "")


async def _get(ctx: SmokeContext, url: str) -> httpx.Response:
    return await ctx.client.get(url, follow_redirects=False)


async def _guard(name: str, step: Callable[[], Awaitable[CheckResult]]) -> CheckResult:
    try:
        return await step()
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return CheckResult(name, False, f"{type(exc).__name__}: {exc}")


async def check_signin_backend_has_auth(ctx: SmokeContext) -> CheckResult:
    name = "signin: backend exposes /auth routes (PR-07)"

    async def step() -> CheckResult:
        response = await ctx.client.get(f"{ctx.backend_url}/openapi.json")
        paths = (
            response.json().get("paths", {}) if response.status_code == HTTP_OK else {}
        )
        ok = "/auth/me" in paths and "/auth/sso/callback" in paths
        return CheckResult(name, ok, "present" if ok else "missing: run against PR-07+")

    return await _guard(name, step)


async def check_signin_login_page(ctx: SmokeContext) -> CheckResult:
    name = "signin: GET /login renders the SSO button"

    async def step() -> CheckResult:
        response = await _get(ctx, f"{ctx.frontend_url}/login")
        ok = (
            response.status_code == HTTP_OK
            and "Sign in with Microsoft" in response.text
        )
        return CheckResult(name, ok, f"-> {response.status_code}")

    return await _guard(name, step)


async def check_signin_start(ctx: SmokeContext) -> CheckResult:
    name = "signin: /auth/login binds an httpOnly state cookie and redirects to the IdP"

    async def step() -> CheckResult:
        response = await _get(ctx, f"{ctx.frontend_url}/auth/login?next={SIGNIN_NEXT}")
        authorize = _location(response)
        cookie = _set_cookie(response, STATE_COOKIE)
        ok = (
            response.status_code in REDIRECT_CODES
            and "/oauth2/v2.0/authorize" in authorize
            and _has_flags(cookie, "httponly", "samesite=lax")
        )
        if ok:
            ctx.state["authorize_url"] = authorize
        return CheckResult(name, ok, f"-> {response.status_code}")

    return await _guard(name, step)


async def check_signin_mock_idp(ctx: SmokeContext) -> CheckResult:
    name = "signin: mock IdP (through port 3000) redirects to /auth/callback"

    async def step() -> CheckResult:
        page = await _get(ctx, ctx.state["authorize_url"])
        if page.status_code in REDIRECT_CODES:  # auto-approve mode
            redirect = page
        else:
            parser = _FormParser()
            parser.feed(page.text)
            action = urljoin(str(page.url), parser.action or "")
            form = {
                **parser.hidden,
                "email": ctx.signin_email,
                "name": ctx.signin_email.split("@", maxsplit=1)[0],
            }
            redirect = await ctx.client.post(action, data=form, follow_redirects=False)
        callback = _location(redirect)
        ok = (
            redirect.status_code in REDIRECT_CODES
            and urlsplit(callback).path == "/auth/callback"
        )
        if ok:
            ctx.state["callback_url"] = urljoin(ctx.frontend_url, callback)
        return CheckResult(name, ok, f"-> {redirect.status_code}")

    return await _guard(name, step)


async def check_signin_forged_state(ctx: SmokeContext) -> CheckResult:
    name = "signin: a callback with a forged state is rejected (login CSRF)"

    async def step() -> CheckResult:
        forged = (
            f"{ctx.frontend_url}/auth/callback?code=forged&state={uuid.uuid4().hex}"
        )
        response = await _get(ctx, forged)
        ok = (
            response.status_code in REDIRECT_CODES
            and "error=state_mismatch" in _location(response)
        )
        # The rejection deletes the state cookie; start a fresh, valid attempt.
        restart = await check_signin_start(ctx)
        return CheckResult(name, ok and restart.ok, f"-> {response.status_code}")

    return await _guard(name, step)


async def check_signin_callback(ctx: SmokeContext) -> CheckResult:
    name = (
        "signin: /auth/callback sets the httpOnly session cookie, pending user -> /chat"
    )

    async def step() -> CheckResult:
        response = await _get(ctx, ctx.state["callback_url"])
        cookie = _set_cookie(response, SESSION_COOKIE)
        ok = (
            response.status_code in REDIRECT_CODES
            and urlsplit(_location(response)).path == "/chat"
            and _has_flags(cookie, "httponly", "samesite=lax", "path=/")
            and "session" not in _location(response).lower()
        )
        return CheckResult(name, ok, f"-> {response.status_code}")

    return await _guard(name, step)


async def check_signin_me(ctx: SmokeContext) -> CheckResult:
    name = "signin: /api/proxy/auth/me returns the pending user"

    async def step() -> CheckResult:
        response = await _get(ctx, f"{ctx.frontend_url}/api/proxy/auth/me")
        body = response.json() if response.status_code == HTTP_OK else {}
        ok = (
            body.get("user", {}).get("email") == ctx.signin_email
            and body.get("status") == "pending"
        )
        return CheckResult(name, ok, f"-> {response.status_code}")

    return await _guard(name, step)


async def check_signin_pending_forbidden(ctx: SmokeContext) -> CheckResult:
    name = "signin: a pending user gets 403 on a dashboard API"

    async def step() -> CheckResult:
        response = await _get(ctx, f"{ctx.frontend_url}/api/proxy/admin/dashboard")
        ok = response.status_code == HTTP_FORBIDDEN
        return CheckResult(
            name, ok, f"-> {response.status_code} (expected {HTTP_FORBIDDEN})"
        )

    return await _guard(name, step)


async def check_signin_logout(ctx: SmokeContext) -> CheckResult:
    name = "signin: sign out revokes the session (/auth/me -> 401)"

    async def step() -> CheckResult:
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(ctx.frontend_url))
        response = await ctx.client.post(
            f"{ctx.frontend_url}/api/proxy/auth/logout",
            headers={"Origin": origin},
            follow_redirects=False,
        )
        after = await _get(ctx, f"{ctx.frontend_url}/api/proxy/auth/me")
        ok = (
            response.status_code == HTTP_NO_CONTENT
            and after.status_code == HTTP_UNAUTHORIZED
        )
        return CheckResult(
            name, ok, f"logout -> {response.status_code}, me -> {after.status_code}"
        )

    return await _guard(name, step)


SIGNIN_JOURNEY: tuple[Step, ...] = (
    check_signin_backend_has_auth,
    check_signin_login_page,
    check_signin_start,
    check_signin_forged_state,
    check_signin_mock_idp,
    check_signin_callback,
    check_signin_me,
    check_signin_pending_forbidden,
    check_signin_logout,
)

# Named journeys, selected with --journey <name> (repeatable).
JOURNEYS: dict[str, tuple[Step, ...]] = {"signin": SIGNIN_JOURNEY}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--frontend-url",
        default=os.environ.get("SMOKE_FRONTEND_URL", DEFAULT_FRONTEND_URL),
        help="Next.js base URL (env SMOKE_FRONTEND_URL)",
    )
    parser.add_argument(
        "--backend-url",
        default=os.environ.get("SMOKE_BACKEND_URL", DEFAULT_BACKEND_URL),
        help="FastAPI base URL (env SMOKE_BACKEND_URL)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Per-request timeout in seconds",
    )
    parser.add_argument(
        "--signin-email",
        default=os.environ.get("SMOKE_SIGNIN_EMAIL", ""),
        help="Mock IdP identity for --journey signin (default: a fresh "
        "smoke-<random>@example.com, i.e. a new pending user)",
    )
    parser.add_argument(
        "--journey",
        action="append",
        default=[],
        choices=sorted(JOURNEYS),
        help="Extra journey to run after the base checks (repeatable)",
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> list[CheckResult]:
    steps: list[Step] = list(BASE_CHECKS)
    for journey in args.journey:
        steps.extend(JOURNEYS[journey])

    results: list[CheckResult] = []
    async with httpx.AsyncClient(
        timeout=args.timeout,
        follow_redirects=True,
        cookies=httpx.Cookies(),
    ) as client:
        ctx = SmokeContext(
            client=client,
            frontend_url=args.frontend_url.rstrip("/"),
            backend_url=args.backend_url.rstrip("/"),
            signin_email=args.signin_email
            or f"smoke-{uuid.uuid4().hex[:10]}@example.com",
        )
        for step in steps:
            result = await step(ctx)
            log = logger.info if result.ok else logger.error
            log(
                "[%s] %s: %s",
                "PASS" if result.ok else "FAIL",
                result.name,
                result.detail,
            )
            results.append(result)
    return results


def main(argv: list[str] | None = None) -> int:
    results = asyncio.run(run(parse_args(argv)))
    failed = [r for r in results if not r.ok]
    logger.info("%d/%d checks passed", len(results) - len(failed), len(results))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
