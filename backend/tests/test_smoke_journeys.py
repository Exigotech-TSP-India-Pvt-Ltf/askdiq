"""The sign-in smoke journeys against a fake frontend (no network).

The real run happens in the full-stack smoke job; this proves the script
walks every hop of the proxy contract and fails when a hop is wrong.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke_journeys.py"
BASE = "http://localhost:3000"
BOSS = "boss@example.com"
AUTHORIZE = f"{BASE}/mock-idp/tenant/oauth2/v2.0/authorize"


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("smoke_journeys", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_script()


class FakeFrontend:
    """Next.js + backend behaviour as seen through port 3000."""

    def __init__(self, *, http_only: bool = True) -> None:
        self.http_only = http_only
        self.sessions: dict[str, str] = {}
        self.codes: dict[str, str] = {}

    def _email(self, request: httpx.Request) -> str | None:
        cookie = request.headers.get("cookie", "")
        token = cookie.removeprefix("askdiq_session=").split(";")[0]
        return self.sessions.get(token)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/login":
            return httpx.Response(200, text="<html>login</html>")
        if path == "/api/proxy/auth/sso/login":
            return httpx.Response(200, json={"authorize_url": f"{AUTHORIZE}?state=s1"})
        if path.endswith("/authorize") and request.method == "GET":
            form = (
                '<form method="post"><input type="hidden" name="state" value="s1">'
                '<input name="email"></form>'
            )
            return httpx.Response(200, text=form)
        if path.endswith("/authorize"):
            fields = parse_qs(request.content.decode())
            code = f"code-{len(self.codes)}"
            self.codes[code] = fields["email"][0]
            location = f"{BASE}/auth/callback?code={code}&state={fields['state'][0]}"
            return httpx.Response(302, headers={"location": location})
        if path == "/auth/callback":
            code = parse_qs(urlsplit(str(request.url)).query)["code"][0]
            token = f"tok-{code}"
            self.sessions[token] = self.codes.pop(code)
            flags = "; HttpOnly" if self.http_only else ""
            return httpx.Response(
                303,
                headers={
                    "location": "/",
                    "set-cookie": f"askdiq_session={token}; Path=/; SameSite=Lax{flags}",
                },
            )
        email = self._email(request)
        if email is None:
            return httpx.Response(401, json={"detail": "Not authenticated."})
        if path == "/api/proxy/auth/me":
            return httpx.Response(200, json={"user": {"email": email}})
        if path == "/api/proxy/admin/dashboard":
            return httpx.Response(200 if email == BOSS else 403, json={})
        if path == "/api/proxy/auth/logout":
            self.sessions = {t: e for t, e in self.sessions.items() if e != email}
            return httpx.Response(204)
        return httpx.Response(404)


def factory(frontend: FakeFrontend):
    def make() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(frontend))

    return make


async def test_both_journeys_pass_against_a_correct_frontend() -> None:
    checks = await smoke.run_signin_journeys(factory(FakeFrontend()), BASE, BOSS)
    failed = [c.name for c in checks if not c.ok]
    assert not failed, failed
    names = " ".join(c.name for c in checks)
    for hop in ("GET /login", "HttpOnly", "auth/me", "dashboard", "after logout"):
        assert hop in names


async def test_a_cookie_without_httponly_fails_the_smoke() -> None:
    checks = await smoke.run_signin_journeys(
        factory(FakeFrontend(http_only=False)), BASE, BOSS
    )
    failed = {c.name for c in checks if not c.ok}
    assert "pending user: callback sets an HttpOnly session cookie" in failed


async def test_missing_superadmin_email_fails() -> None:
    checks = await smoke.run_signin_journeys(factory(FakeFrontend()), BASE, "")
    assert not all(c.ok for c in checks)
