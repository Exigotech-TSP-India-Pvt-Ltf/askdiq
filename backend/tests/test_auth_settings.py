"""SSO / session settings validation (planchanges4 B2.1.2, B2.2.3 startup guard)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import MOCK_CLIENT_ID, MOCK_TENANT_ID, Settings

BACKEND_DIR = Path(__file__).resolve().parents[1]

_BASE: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "azure_openai_api_key": "k",
    "azure_openai_endpoint": "https://example.invalid/",
    "azure_openai_api_version": "2024-12-01-preview",
    "azure_deployment_name": "gpt-5-mini",
    "session_secret": "s" * 40,
}
_ENTRA: dict[str, Any] = {
    "entra_tenant_id": "11111111-1111-1111-1111-111111111111",
    "entra_client_id": "22222222-2222-2222-2222-222222222222",
    "entra_client_secret": "client-secret-value",
    "entra_redirect_uri": "https://app.example.com/auth/callback",
}


def make(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **{**_BASE, **overrides})


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*_ENTRA, "auth_dev_mock", "env", "app_base_url", "superadmin_emails"):
        monkeypatch.delenv(name.upper(), raising=False)


def test_real_mode_requires_every_entra_value() -> None:
    for missing in _ENTRA:
        values = {**_ENTRA, missing: None}
        with pytest.raises(ValidationError) as exc:
            make(auth_dev_mock=False, **values)
        assert missing.upper() in str(exc.value)


def test_real_mode_loads_with_all_entra_values() -> None:
    settings = make(auth_dev_mock=False, env="production", **_ENTRA)
    assert settings.oidc_authority == "https://login.microsoftonline.com"
    assert settings.entra_redirect_uri == _ENTRA["entra_redirect_uri"]


def test_mock_mode_needs_no_entra_values_and_fills_placeholders() -> None:
    settings = make(auth_dev_mock=True, env="development")
    assert settings.entra_tenant_id == MOCK_TENANT_ID
    assert settings.entra_client_id == MOCK_CLIENT_ID
    assert settings.entra_client_secret is not None
    assert settings.entra_redirect_uri == "http://localhost:3000/auth/callback"
    assert settings.oidc_authority == "http://localhost:3000/mock-idp"


@pytest.mark.parametrize("env", ["production", "staging", "test", "dev"])
def test_mock_mode_is_refused_outside_development(env: str) -> None:
    with pytest.raises(ValidationError, match="AUTH_DEV_MOCK"):
        make(auth_dev_mock=True, env=env, **_ENTRA)


def test_session_secret_has_no_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    values = {k: v for k, v in _BASE.items() if k != "session_secret"}
    with pytest.raises(ValidationError, match="session_secret"):
        Settings(_env_file=None, auth_dev_mock=True, **values)


def test_empty_session_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        make(auth_dev_mock=True, session_secret="")


def test_short_session_secret_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match="32 bytes"):
        make(auth_dev_mock=False, env="production", session_secret="short", **_ENTRA)  # noqa: S106


def test_legacy_jwt_secret_is_gone() -> None:
    settings = make(auth_dev_mock=True)
    assert not hasattr(settings, "api_jwt_secret")
    assert "api_jwt_secret" not in Settings.model_fields


def test_superadmin_emails_are_parsed_case_insensitively() -> None:
    settings = make(
        auth_dev_mock=True, superadmin_emails=" Owner@Example.com, ,b@example.com "
    )
    assert settings.superadmin_email_set == {"owner@example.com", "b@example.com"}


def test_settings_injected_through_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_DEV_MOCK", "false")
    monkeypatch.setenv("ENV", "production")
    for key, value in _ENTRA.items():
        monkeypatch.setenv(key.upper(), value)
    settings = make()
    assert settings.auth_dev_mock is False
    assert settings.entra_tenant_id == _ENTRA["entra_tenant_id"]
    assert settings.entra_client_secret is not None
    assert "client-secret-value" not in repr(settings)


def test_app_refuses_to_boot_with_mock_outside_development() -> None:
    """Startup guard: settings load (the first thing main.py does) fails."""
    env = {
        **os.environ,
        "AUTH_DEV_MOCK": "true",
        "ENV": "production",
        "SESSION_SECRET": "x" * 40,
    }
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            "-c",
            "from app.core.config import get_settings; get_settings()",
        ],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode != 0
    assert "AUTH_DEV_MOCK=true is only allowed when ENV=development" in result.stderr
