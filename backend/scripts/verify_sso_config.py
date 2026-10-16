#!/usr/bin/env python3
"""Owner preflight for real Entra ID SSO (planchanges4 B2.2.4).

Run after filling in ``backend/.env``; it loads settings the normal way and
prints one PASS/FAIL line per check. It never prints a configured value
(secrets, ids or URLs), only check names and generic reasons.

Usage:
    cd backend
    venv\\Scripts\\python.exe scripts\\verify_sso_config.py

Exit code 0 only when every check passes.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Ensure backend root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from pydantic import SecretStr, ValidationError

from app.auth.oidc import discovery_url, parse_jwks
from app.core.config import MIN_SESSION_SECRET_BYTES, Settings

REQUIRED_VALUES = (
    "entra_tenant_id",
    "entra_client_id",
    "entra_client_secret",
    "entra_redirect_uri",
    "session_secret",
)


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    passed: bool
    reason: str = ""


def _value(settings: Settings, name: str) -> str:
    raw = getattr(settings, name, None)
    if isinstance(raw, SecretStr):
        return raw.get_secret_value()
    return raw or ""


def _redirect_ok(actual: str, expected_base: str) -> bool:
    """Paths the frontend serves: `/auth/callback`, `/auth/callback/<provider>` and the
    Auth.js-style `/api/auth/callback/<provider>`."""
    if actual == expected_base:
        return True
    base = expected_base.removesuffix("/auth/callback")
    for prefix in (expected_base + "/", base + "/api/auth/callback/"):
        if actual.startswith(prefix):
            slug = actual[len(prefix) :]
            return bool(slug) and slug.replace("-", "").replace("_", "").isalnum()
    return False


def _static_checks(settings: Settings) -> list[CheckResult]:
    missing = [n.upper() for n in REQUIRED_VALUES if not _value(settings, n)]
    secret_len = len(_value(settings, "session_secret").encode())
    expected_redirect = f"{settings.app_base_url.rstrip('/')}/auth/callback"
    https = urlsplit(settings.app_base_url).scheme == "https"
    return [
        CheckResult(
            "required_values_present",
            not missing,
            f"missing: {', '.join(missing)}" if missing else "",
        ),
        CheckResult(
            "session_secret_length",
            secret_len >= MIN_SESSION_SECRET_BYTES,
            f"SESSION_SECRET must be at least {MIN_SESSION_SECRET_BYTES} bytes",
        ),
        CheckResult(
            "auth_dev_mock_disabled",
            not settings.auth_dev_mock,
            "AUTH_DEV_MOCK must be false for real Entra",
        ),
        CheckResult(
            "redirect_uri_matches_app_base_url",
            _redirect_ok(_value(settings, "entra_redirect_uri"), expected_redirect),
            "ENTRA_REDIRECT_URI must be APP_BASE_URL + /auth/callback"
            " (optionally + /<provider>, e.g. /auth/callback/microsoft-entra-id)",
        ),
        CheckResult(
            "cookie_secure_matches_scheme",
            settings.session_cookie_secure == https,
            "SESSION_COOKIE_SECURE must be true for https and false for http",
        ),
        CheckResult(
            "superadmin_emails_set",
            bool(settings.superadmin_email_set),
            "SUPERADMIN_EMAILS is empty",
        ),
    ]


async def _get_json(client: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
    try:
        response = await client.get(url)
        body = response.json() if response.status_code == httpx.codes.OK else None
    except (httpx.HTTPError, ValueError):
        return None
    return body if isinstance(body, dict) else None


async def _remote_checks(
    settings: Settings, client: httpx.AsyncClient
) -> list[CheckResult]:
    names = ("discovery_reachable", "issuer_matches_tenant", "jwks_has_keys")
    tenant = _value(settings, "entra_tenant_id")
    if not tenant:
        return [
            CheckResult(n, False, "skipped: ENTRA_TENANT_ID missing") for n in names
        ]
    # Always the real authority, even if AUTH_DEV_MOCK is (wrongly) on.
    real = settings.model_copy(update={"auth_dev_mock": False})
    document = await _get_json(client, discovery_url(real))
    if document is None:
        return [
            CheckResult(names[0], False, "discovery document unreachable"),
            CheckResult(names[1], False, "skipped: no discovery document"),
            CheckResult(names[2], False, "skipped: no discovery document"),
        ]
    issuer = str(document.get("issuer", "")).replace("{tenantid}", tenant)
    jwks_uri = document.get("jwks_uri")
    jwks = await _get_json(client, str(jwks_uri)) if jwks_uri else None
    return [
        CheckResult(names[0], True),
        CheckResult(
            names[1], tenant in issuer, "discovery issuer does not contain the tenant"
        ),
        CheckResult(
            names[2],
            bool(jwks and parse_jwks(jwks)),
            "JWKS unreachable or has no RSA signing keys",
        ),
    ]


async def run_checks(
    settings: Settings, client: httpx.AsyncClient | None = None
) -> list[CheckResult]:
    results = _static_checks(settings)
    if client is not None:
        return results + await _remote_checks(settings, client)
    async with httpx.AsyncClient(timeout=settings.oidc_http_timeout_seconds) as owned:
        return results + await _remote_checks(settings, owned)


def format_results(results: list[CheckResult]) -> str:
    lines = []
    for r in results:
        if r.passed:
            lines.append(f"PASS  {r.name}")
        else:
            lines.append(f"FAIL  {r.name}: {r.reason}")
    return "\n".join(lines)


def load_settings() -> tuple[Settings | None, list[CheckResult]]:
    """Settings from env/.env; on failure, report field names only (no values)."""
    try:
        return Settings(), []
    except ValidationError as exc:
        problems = []
        for error in exc.errors(include_input=False, include_url=False):
            loc = ".".join(str(p) for p in error["loc"]).upper()
            problems.append(f"{loc}: {error['type']}" if loc else str(error["msg"]))
        return None, [CheckResult("settings_load", False, "; ".join(problems))]


async def main_async() -> int:
    settings, failures = load_settings()
    if settings is None:
        print(format_results(failures))
        return 1
    results = await run_checks(settings)
    print(format_results(results))
    return 0 if all(r.passed for r in results) else 1


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
