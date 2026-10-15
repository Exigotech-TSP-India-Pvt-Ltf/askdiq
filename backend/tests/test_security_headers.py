"""Security headers, CORS and docs hardening (planchanges4 B1.1, OWASP A02).

These tests do not need a database: ``get_db`` is overridden with a stub that
fails, which also exercises the error paths.
"""

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import register_exception_handlers
from app.core.security_headers import (
    SecurityHeadersMiddleware,
    build_security_headers,
)
from main import api_docs_enabled, app, docs_urls

settings = get_settings()
ORIGIN = settings.cors_allowed_origins[0]
REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "cross-origin-opener-policy": "same-origin",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
}
FAKE_UUID = "00000000-0000-0000-0000-000000000001"


def _assert_security_headers(res, *, hsts: bool = False) -> None:
    for name, value in EXPECTED.items():
        assert res.headers.get(name) == value, (
            f"{res.request.method} {res.request.url.path} -> {res.status_code} "
            f"missing/incorrect {name}"
        )
    assert ("strict-transport-security" in res.headers) is hsts


class _UnavailableSession:
    """Stands in for AsyncSession: any use fails like a database outage."""

    def __getattr__(self, name: str):
        raise RuntimeError("database unavailable in header tests")


async def _broken_db() -> AsyncGenerator[_UnavailableSession, None]:
    yield _UnavailableSession()


@pytest.fixture
async def no_db_client() -> AsyncGenerator[AsyncClient, None]:
    app.dependency_overrides[get_db] = _broken_db
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()


def _concrete_path(path: str) -> str:
    """Fill every path parameter with a syntactically valid UUID."""
    return "/".join(
        FAKE_UUID if segment.startswith("{") else segment for segment in path.split("/")
    )


def test_security_header_values_come_from_config() -> None:
    headers = build_security_headers(settings)
    for name, value in EXPECTED.items():
        assert headers[name] == value
    custom = settings.model_copy(update={"security_frame_options": "SAMEORIGIN"})
    assert build_security_headers(custom)["x-frame-options"] == "SAMEORIGIN"


def test_hsts_only_in_production() -> None:
    dev = settings.model_copy(update={"env": "development"})
    prod = settings.model_copy(update={"env": "production"})
    assert "strict-transport-security" not in build_security_headers(dev)
    assert (
        build_security_headers(prod)["strict-transport-security"]
        == "max-age=63072000; includeSubDomains"
    )


async def test_every_route_carries_security_headers(
    no_db_client: AsyncClient,
) -> None:
    """Route walk: every registered route, authenticated or not, success or
    error, returns the headers (HSTS absent outside production)."""
    walked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = _concrete_path(route.path)
        for method in sorted(route.methods - {"HEAD"}):
            res = await no_db_client.request(method, path)
            _assert_security_headers(res)
            walked += 1
    assert walked > 10


@pytest.mark.parametrize(
    ("method", "path", "headers", "status"),
    [
        ("GET", "/branding", {}, 200),
        ("GET", "/does-not-exist", {}, 404),
        ("PUT", "/branding", {}, 405),
        ("GET", "/admin/usage/summary", {}, 401),
        (
            "GET",
            "/admin/usage/queries?limit=999",
            {"X-API-Key": settings.api_key_name or "ci-test-key"},
            422,
        ),
        (
            "GET",
            "/admin/usage/summary",
            {"X-API-Key": settings.api_key_name or "ci-test-key"},
            500,
        ),
    ],
)
async def test_success_and_error_responses_carry_headers(
    no_db_client: AsyncClient,
    method: str,
    path: str,
    headers: dict[str, str],
    status: int,
) -> None:
    res = await no_db_client.request(method, path, headers=headers)
    assert res.status_code == status
    _assert_security_headers(res)


async def test_hsts_present_when_production() -> None:
    mini = FastAPI()
    register_exception_handlers(mini)

    @mini.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "yes"}

    prod = settings.model_copy(update={"env": "production"})
    mini.add_middleware(SecurityHeadersMiddleware, headers=build_security_headers(prod))
    async with AsyncClient(
        transport=ASGITransport(app=mini), base_url="http://testserver"
    ) as ac:
        ok = await ac.get("/ping")
        missing = await ac.get("/missing")
    _assert_security_headers(ok, hsts=True)
    _assert_security_headers(missing, hsts=True)


async def test_route_can_override_a_security_header() -> None:
    from fastapi.responses import PlainTextResponse

    mini = FastAPI()

    @mini.get("/asset")
    async def asset() -> PlainTextResponse:
        return PlainTextResponse("x", headers={"X-Frame-Options": "SAMEORIGIN"})

    mini.add_middleware(SecurityHeadersMiddleware)
    async with AsyncClient(
        transport=ASGITransport(app=mini), base_url="http://testserver"
    ) as ac:
        res = await ac.get("/asset")
    assert res.headers.get_list("x-frame-options") == ["SAMEORIGIN"]


# --- CORS -------------------------------------------------------------------


async def test_cors_preflight_allows_listed_header(no_db_client: AsyncClient) -> None:
    res = await no_db_client.options(
        "/admin/usage/summary",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-API-Key, Content-Type",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == ORIGIN
    _assert_security_headers(res)


async def test_cors_preflight_rejects_unknown_header(
    no_db_client: AsyncClient,
) -> None:
    res = await no_db_client.options(
        "/admin/usage/summary",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Evil-Header",
        },
    )
    assert res.status_code == 400
    assert "Disallowed CORS headers" in res.text
    _assert_security_headers(res)


async def test_cors_preflight_rejects_unlisted_method(
    no_db_client: AsyncClient,
) -> None:
    res = await no_db_client.options(
        "/admin/usage/summary",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "PUT"},
    )
    assert res.status_code == 400


def test_cors_config_has_no_wildcards() -> None:
    assert "*" not in settings.cors_allow_headers
    assert "*" not in settings.cors_allow_methods
    assert {h.lower() for h in settings.cors_allow_headers} == {
        "content-type",
        "x-api-key",
        "x-request-id",
        "x-client-id",
        "if-none-match",
    }


# --- Docs -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "enable", "expected"),
    [
        ("development", False, True),
        ("production", False, False),
        ("production", True, True),
    ],
)
def test_api_docs_gating(env: str, enable: bool, expected: bool) -> None:
    cfg = settings.model_copy(update={"env": env, "enable_api_docs": enable})
    assert api_docs_enabled(cfg) is expected


async def test_docs_return_404_in_production_mode() -> None:
    prod = settings.model_copy(update={"env": "production", "enable_api_docs": False})
    mini = FastAPI(**docs_urls(prod))
    async with AsyncClient(
        transport=ASGITransport(app=mini), base_url="http://testserver"
    ) as ac:
        for path in ("/docs", "/redoc", "/openapi.json"):
            res = await ac.get(path)
            assert res.status_code == 404, path


async def test_docs_served_in_development(no_db_client: AsyncClient) -> None:
    # The suite runs with ENV=development, so the real app exposes docs.
    res = await no_db_client.get("/openapi.json")
    assert res.status_code == 200
    _assert_security_headers(res)
    docs = await no_db_client.get("/docs")
    assert docs.status_code == 200
    # Swagger UI needs its own scripts, so the strict API CSP is skipped there.
    assert "content-security-policy" not in docs.headers


# --- nginx ------------------------------------------------------------------


def test_nginx_conf_hardening() -> None:
    conf = (REPO_ROOT / "askdiq.conf").read_text(encoding="utf-8")
    assert "server_tokens off;" in conf
    assert "client_max_body_size 15m;" in conf
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in conf
    for header in (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Cross-Origin-Opener-Policy",
        "X-Frame-Options",
        "Content-Security-Policy",
    ):
        lines = [
            line.strip()
            for line in conf.splitlines()
            if line.strip().startswith(f"add_header {header} ")
        ]
        assert len(lines) == 1, header
        assert lines[0].endswith(" always;"), header
