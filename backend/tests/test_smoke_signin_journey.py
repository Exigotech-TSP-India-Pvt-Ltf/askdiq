"""`smoke_e2e.py --journey signin` (PR-18, planchanges4 F1.5) against a fake stack.

The real journey needs the PR-07 `/auth` routes and both servers in mock mode,
so CI does not run it yet. These tests drive every step through an
``httpx.MockTransport`` that behaves like the Next.js frontend on port 3000,
so the journey logic (cookie flags, redirects, forged-state rejection,
logout) is verified now and the flag can simply be switched on later.
"""

from __future__ import annotations

import argparse
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from scripts import smoke_e2e

FRONTEND = "http://localhost:3000"
BACKEND = "http://127.0.0.1:8000"
STATE = "state0123456789abcdef"
AUTHORIZE = f"{FRONTEND}/mock-idp/tenant/oauth2/v2.0/authorize?state={STATE}"
EMAIL = "smoke-new@example.com"
COOKIE_FLAGS = "; Path=/; HttpOnly; SameSite=lax"
FORM = (
    '<form method="post">'
    f'<input type="hidden" name="state" value="{STATE}">'
    '<input name="email"></form>'
)


class FakeStack:
    """Minimal stand-in for Next.js (port 3000) + FastAPI with the mock IdP."""

    def __init__(self, *, has_auth: bool = True, session_flags: str = COOKIE_FLAGS):
        self.has_auth = has_auth
        self.session_flags = session_flags
        self.signed_in = False
        self.posted_email = ""

    def __call__(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911 - one branch per route
        url = urlsplit(str(request.url))
        path = url.path
        cookies = request.headers.get("cookie", "")
        if path == "/openapi.json":
            paths = {"/auth/me": {}, "/auth/sso/callback": {}} if self.has_auth else {}
            return httpx.Response(200, json={"paths": paths})
        if path == "/login":
            return httpx.Response(200, text="<button>Sign in with Microsoft</button>")
        if path == "/auth/login":
            headers = [
                ("location", AUTHORIZE),
                ("set-cookie", f"askdiq_oidc_state={STATE}{COOKIE_FLAGS}"),
            ]
            return httpx.Response(303, headers=headers)
        if path.endswith("/authorize") and request.method == "GET":
            return httpx.Response(200, text=FORM)
        if path.endswith("/authorize"):
            form = parse_qs(request.content.decode())
            self.posted_email = form["email"][0]
            location = f"{FRONTEND}/auth/callback?code=c1&state={form['state'][0]}"
            return httpx.Response(302, headers={"location": location})
        if path == "/auth/callback":
            query = parse_qs(url.query)
            if f"askdiq_oidc_state={query['state'][0]}" not in cookies:
                return httpx.Response(
                    303, headers={"location": "/login?error=state_mismatch"}
                )
            self.signed_in = True
            cookie = f"askdiq_session=s3cr3t{self.session_flags}"
            return httpx.Response(
                303, headers=[("location", "/chat"), ("set-cookie", cookie)]
            )
        if path == "/api/proxy/auth/me":
            if not (self.signed_in and "askdiq_session=" in cookies):
                return httpx.Response(401, json={"detail": "Not authenticated."})
            body = {"user": {"email": self.posted_email}, "status": "pending"}
            return httpx.Response(200, json=body)
        if path == "/api/proxy/admin/dashboard":
            return httpx.Response(
                403, json={"detail": "Dashboard access requires approval."}
            )
        if path == "/api/proxy/auth/logout":
            assert request.headers["origin"] == FRONTEND
            self.signed_in = False
            return httpx.Response(204)
        return httpx.Response(404)


async def run_journey(stack: FakeStack) -> list[smoke_e2e.CheckResult]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(stack)) as client:
        ctx = smoke_e2e.SmokeContext(
            client=client,
            frontend_url=FRONTEND,
            backend_url=BACKEND,
            signin_email=EMAIL,
        )
        return [await step(ctx) for step in smoke_e2e.JOURNEYS["signin"]]


async def test_signin_journey_passes_against_a_correct_stack() -> None:
    stack = FakeStack()
    results = await run_journey(stack)
    assert [r.name for r in results if not r.ok] == []
    assert len(results) == len(smoke_e2e.SIGNIN_JOURNEY)
    assert stack.posted_email == EMAIL
    # Nothing secret is ever put in a detail line.
    assert all("s3cr3t" not in r.detail and STATE not in r.detail for r in results)


async def test_signin_journey_flags_a_cookie_without_httponly() -> None:
    results = await run_journey(FakeStack(session_flags="; Path=/; SameSite=lax"))
    failed = [r.name for r in results if not r.ok]
    assert failed == [
        "signin: /auth/callback sets the httpOnly session cookie, pending user -> /chat"
    ]


async def test_signin_journey_reports_a_backend_without_auth_routes() -> None:
    results = await run_journey(FakeStack(has_auth=False))
    assert not results[0].ok
    assert "PR-07" in results[0].detail


def test_signin_is_opt_in_and_selectable() -> None:
    assert smoke_e2e.parse_args([]).journey == []
    args = smoke_e2e.parse_args(["--journey", "signin", "--signin-email", EMAIL])
    assert args.journey == ["signin"]
    assert args.signin_email == EMAIL
    with pytest.raises(SystemExit):
        smoke_e2e.parse_args(["--journey", "unknown"])


async def test_run_only_adds_the_journey_when_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def fake(ctx: smoke_e2e.SmokeContext) -> smoke_e2e.CheckResult:
        seen.append(ctx.signin_email)
        return smoke_e2e.CheckResult("fake", True, "")

    monkeypatch.setattr(smoke_e2e, "BASE_CHECKS", (fake,))
    monkeypatch.setattr(smoke_e2e, "JOURNEYS", {"signin": (fake, fake)})
    base = argparse.Namespace(
        frontend_url=FRONTEND, backend_url=BACKEND, timeout=1.0, signin_email=""
    )
    assert len(await smoke_e2e.run(argparse.Namespace(**vars(base), journey=[]))) == 1
    results = await smoke_e2e.run(argparse.Namespace(**vars(base), journey=["signin"]))
    assert len(results) == 3
    assert seen[-1].startswith("smoke-")
    assert seen[-1].endswith("@example.com")
