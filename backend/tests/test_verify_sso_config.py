"""`scripts/verify_sso_config.py` (B2.2.4): one FAIL line per broken condition,
exit code 1, and no secret ever printed. HTTP is mocked; Microsoft is never
called."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.auth.mock_idp import MockKeyStore
from app.core.config import Settings

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_sso_config.py"
FIXTURE = Path(__file__).parent / "fixtures" / "entra_discovery_v2.json"
TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
CLIENT_SECRET = "TOP-SECRET-client-secret-value-123"  # noqa: S105 - test canary
SESSION_SECRET = "TOP-SECRET-session-secret-0123456789abcdef"  # noqa: S105 - test canary
DISCOVERY = (
    f"https://login.microsoftonline.com/{TENANT}/v2.0/.well-known/openid-configuration"
)
JWKS = f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"
SECRETS = (CLIENT_SECRET, SESSION_SECRET)


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("verify_sso_config", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module
    spec.loader.exec_module(module)
    return module


verify = _load_script()

GOOD: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://u:p@localhost/db",
    "azure_openai_api_key": "k",
    "azure_openai_endpoint": "https://example.invalid/",
    "azure_openai_api_version": "2024-12-01-preview",
    "azure_deployment_name": "gpt-5-mini",
    "entra_tenant_id": TENANT,
    "entra_client_id": "22222222-2222-2222-2222-222222222222",
    "entra_client_secret": SecretStr(CLIENT_SECRET),
    "entra_redirect_uri": "https://askdiq.example.com/auth/callback",
    "app_base_url": "https://askdiq.example.com",
    "session_secret": SecretStr(SESSION_SECRET),
    "session_cookie_secure": True,
    "superadmin_emails": "owner@example.com",
    "auth_dev_mock": False,
    "env": "production",
}


def settings_with(**changes: Any) -> Settings:
    return Settings.model_construct(**{**GOOD, **changes})


def entra_transport(
    *, discovery: dict | None = None, jwks: dict | None = None, status: int = 200
) -> httpx.MockTransport:
    document = discovery or json.loads(FIXTURE.read_text(encoding="utf-8"))
    keys = jwks if jwks is not None else MockKeyStore().jwks()

    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status)
        if str(request.url) == DISCOVERY:
            return httpx.Response(200, json=document)
        if str(request.url) == JWKS:
            return httpx.Response(200, json=keys)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def run(settings: Settings, **transport: Any) -> tuple[str, bool]:
    async with httpx.AsyncClient(transport=entra_transport(**transport)) as client:
        results = await verify.run_checks(settings, client)
    output = verify.format_results(results)
    for secret in SECRETS:
        assert secret not in output
    return output, all(r.passed for r in results)


async def test_all_checks_pass_for_a_correct_configuration() -> None:
    output, ok = await run(settings_with())
    assert ok
    assert "FAIL" not in output
    assert output.count("PASS") == 9


@pytest.mark.parametrize(
    ("changes", "failing"),
    [
        ({"entra_client_secret": None}, "required_values_present"),
        ({"entra_client_id": ""}, "required_values_present"),
        ({"session_secret": SecretStr("too-short")}, "session_secret_length"),
        ({"auth_dev_mock": True}, "auth_dev_mock_disabled"),
        (
            {"entra_redirect_uri": "https://askdiq.example.com/callback"},
            "redirect_uri_matches_app_base_url",
        ),
        ({"session_cookie_secure": False}, "cookie_secure_matches_scheme"),
        (
            {"app_base_url": "http://localhost:3000"},
            "cookie_secure_matches_scheme",
        ),
        ({"superadmin_emails": " , "}, "superadmin_emails_set"),
    ],
)
async def test_each_static_failure_gives_a_fail_line(
    changes: dict[str, Any], failing: str
) -> None:
    output, ok = await run(settings_with(**changes))
    assert not ok
    assert f"FAIL  {failing}" in output


async def test_unreachable_discovery_fails() -> None:
    output, ok = await run(settings_with(), status=503)
    assert not ok
    assert "FAIL  discovery_reachable" in output


async def test_issuer_for_another_tenant_fails() -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document["issuer"] = "https://login.microsoftonline.com/other-tenant/v2.0"
    output, ok = await run(settings_with(), discovery=document)
    assert not ok
    assert "FAIL  issuer_matches_tenant" in output


async def test_empty_jwks_fails() -> None:
    output, ok = await run(settings_with(), jwks={"keys": []})
    assert not ok
    assert "FAIL  jwks_has_keys" in output


async def test_missing_tenant_skips_remote_checks() -> None:
    output, ok = await run(settings_with(entra_tenant_id=None))
    assert not ok
    assert "FAIL  discovery_reachable: skipped" in output


async def test_main_reports_settings_errors_without_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(SCRIPT.parent)  # no .env here: env vars only
    monkeypatch.setenv("AUTH_DEV_MOCK", "false")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", SESSION_SECRET)
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.delenv("ENTRA_TENANT_ID", raising=False)
    monkeypatch.delenv("ENTRA_REDIRECT_URI", raising=False)
    assert await verify.main_async() == 1
    output = capsys.readouterr().out
    assert "FAIL  settings_load" in output
    assert "ENTRA_TENANT_ID" in output
    for secret in SECRETS:
        assert secret not in output


async def test_main_end_to_end_with_mocked_entra(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    httpx_mock: Any,
) -> None:
    monkeypatch.chdir(SCRIPT.parent)
    env = {
        "AUTH_DEV_MOCK": "false",
        "ENV": "production",
        "ENTRA_TENANT_ID": TENANT,
        "ENTRA_CLIENT_ID": GOOD["entra_client_id"],
        "ENTRA_CLIENT_SECRET": CLIENT_SECRET,
        "ENTRA_REDIRECT_URI": GOOD["entra_redirect_uri"],
        "APP_BASE_URL": GOOD["app_base_url"],
        "SESSION_SECRET": SESSION_SECRET,
        "SESSION_COOKIE_SECURE": "true",
        "SUPERADMIN_EMAILS": "owner@example.com",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    httpx_mock.add_response(
        url=DISCOVERY, json=json.loads(FIXTURE.read_text(encoding="utf-8"))
    )
    httpx_mock.add_response(url=JWKS, json=MockKeyStore().jwks())

    assert await verify.main_async() == 0
    output = capsys.readouterr().out
    assert "FAIL" not in output
    for value in (*SECRETS, TENANT, GOOD["entra_client_id"]):
        assert value not in output
