"""OIDC client against the Entra-faithful mock IdP.

No test calls Microsoft: the mock is served in-process through
``ASGITransport`` and the recorded discovery document through
``httpx.MockTransport``.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI

from app.auth import mock_idp
from app.auth.mock_idp import MockIdentity, MockKeyStore, mount_mock_idp, provider
from app.auth.oidc import (
    SCOPES,
    IdentityClaims,
    OidcClient,
    OidcError,
    OidcStateError,
    TokenValidationError,
    discovery_url,
    parse_discovery,
    pkce_challenge,
)
from app.core.config import Settings, get_settings
from app.core.state import MemoryState

FIXTURE = Path(__file__).parent / "fixtures" / "entra_discovery_v2.json"


@pytest.fixture
def settings() -> Settings:
    cfg = get_settings()
    assert cfg.auth_dev_mock, "OIDC tests run against the mock IdP"
    # Never let a developer's real OIDC_DISCOVERY_URL (.env) leak into the tests.
    return cfg.model_copy(update={"oidc_discovery_url": ""})


@pytest.fixture(autouse=True)
def fresh_provider() -> None:
    provider.reset()
    yield
    provider.reset()


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    """An httpx client whose requests reach the mock IdP in-process."""
    mini = FastAPI()
    mini.include_router(mock_idp.router)
    requests: list[str] = []

    async def record(request: httpx.Request) -> None:
        requests.append(request.url.path)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mini),
        event_hooks={"request": [record]},
    ) as client:
        client.recorded = requests  # type: ignore[attr-defined]
        yield client


@pytest.fixture
def oidc(settings: Settings, http: httpx.AsyncClient) -> OidcClient:
    return OidcClient(settings, http_client=http, state_backend=MemoryState())


async def sign_in(
    oidc: OidcClient, http: httpx.AsyncClient, identity: MockIdentity
) -> IdentityClaims:
    provider.auto_identity = identity
    request = await oidc.build_authorize_url()
    response = await http.get(request.url)
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return await oidc.complete_login(query["code"][0], query["state"][0])


def valid_claims(settings: Settings, nonce: str = "n-1", **overrides: Any) -> dict:
    now = int(time.time())
    tenant = str(settings.entra_tenant_id)
    claims = {
        "aud": settings.entra_client_id,
        "iss": f"{settings.oidc_authority}/{tenant}/v2.0",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
        "name": "Ada Lovelace",
        "oid": "8f3a1c2e-0000-4000-8000-000000000001",
        "preferred_username": "ada@example.com",
        "sub": "pairwise-subject",
        "tid": tenant,
        "nonce": nonce,
        "ver": "2.0",
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


# ---------------------------------------------------------------------------
# Authorize URL, PKCE, state
# ---------------------------------------------------------------------------


async def test_authorize_url_has_pkce_state_nonce_and_scopes(
    oidc: OidcClient, settings: Settings
) -> None:
    request = await oidc.build_authorize_url()
    parts = urlsplit(request.url)
    params = {k: v[0] for k, v in parse_qs(parts.query).items()}

    tenant = settings.entra_tenant_id
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
        f"{settings.oidc_authority}/{tenant}/oauth2/v2.0/authorize"
    )
    assert params["client_id"] == settings.entra_client_id
    assert params["response_type"] == "code"
    assert params["redirect_uri"] == settings.entra_redirect_uri
    assert params["scope"] == SCOPES == "openid profile email"
    assert params["state"] == request.state
    assert len(params["state"]) >= 43
    assert len(params["nonce"]) >= 43
    assert params["code_challenge_method"] == "S256"
    assert len(params["code_challenge"]) == 43


async def test_endpoints_come_from_the_discovery_document(
    oidc: OidcClient, http: httpx.AsyncClient, settings: Settings
) -> None:
    await oidc.build_authorize_url()
    assert urlsplit(discovery_url(settings)).path in http.recorded  # type: ignore[attr-defined]


def test_explicit_discovery_url_is_used_for_real_entra(settings: Settings) -> None:
    url = "https://login.microsoftonline.com/tenant-x/v2.0/.well-known/openid-configuration"
    real = settings.model_copy(
        update={"auth_dev_mock": False, "oidc_discovery_url": url}
    )
    assert discovery_url(real) == url


def test_explicit_discovery_url_is_ignored_with_the_mock(settings: Settings) -> None:
    mock = settings.model_copy(
        update={"auth_dev_mock": True, "oidc_discovery_url": "https://example.com/x"}
    )
    assert discovery_url(mock).startswith(mock.oidc_authority)


def test_explicit_discovery_url_must_be_https(settings: Settings) -> None:
    real = settings.model_copy(
        update={"auth_dev_mock": False, "oidc_discovery_url": "http://evil.example/x"}
    )
    with pytest.raises(OidcError):
        discovery_url(real)


async def test_state_is_single_use(oidc: OidcClient, http: httpx.AsyncClient) -> None:
    provider.auto_identity = MockIdentity(email="replay@example.com")
    request = await oidc.build_authorize_url()
    location = (await http.get(request.url)).headers["location"]
    query = parse_qs(urlsplit(location).query)
    code, state = query["code"][0], query["state"][0]

    await oidc.complete_login(code, state)
    with pytest.raises(OidcStateError):
        await oidc.complete_login(code, state)


async def test_unknown_state_is_rejected(oidc: OidcClient) -> None:
    with pytest.raises(OidcStateError):
        await oidc.complete_login("some-code", "never-issued-state")


def test_pkce_challenge_matches_rfc7636_example() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


# ---------------------------------------------------------------------------
# Mock behaves like Entra; the real validator accepts its tokens
# ---------------------------------------------------------------------------


async def test_real_validator_accepts_mock_tokens_and_falls_back_to_upn(
    oidc: OidcClient, http: httpx.AsyncClient, settings: Settings
) -> None:
    identity = MockIdentity(email="Ada@Example.com", name="Ada", oid="oid-ada")
    claims = await sign_in(oidc, http, identity)
    assert claims.oid == "oid-ada"
    assert claims.tid == settings.entra_tenant_id
    assert claims.email == "ada@example.com"  # from preferred_username
    assert claims.email_claim_present is False
    assert claims.name == "Ada"


async def test_email_claim_is_used_when_present(
    oidc: OidcClient, http: httpx.AsyncClient
) -> None:
    identity = MockIdentity(
        email="real.mail@example.com",
        preferred_username="upn@corp.example.com",
        include_email=True,
    )
    claims = await sign_in(oidc, http, identity)
    assert claims.email == "real.mail@example.com"
    assert claims.email_claim_present is True


async def test_mock_id_token_has_entra_v2_claims(
    settings: Settings, http: httpx.AsyncClient
) -> None:
    token = mock_idp.build_id_token(
        settings,
        MockIdentity(email="x@example.com"),
        "nonce-1",
        settings.entra_tenant_id,
    )
    claims = jwt.decode(token, options={"verify_signature": False})
    assert {
        "ver",
        "oid",
        "tid",
        "sub",
        "name",
        "preferred_username",
        "nonce",
        "aud",
        "iat",
        "nbf",
        "exp",
    } <= set(claims)
    assert "email" not in claims
    assert claims["ver"] == "2.0"
    assert claims["iss"] == f"{settings.oidc_authority}/{settings.entra_tenant_id}/v2.0"
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256"
    assert header["kid"] == provider.keys.active_kid


async def test_mock_discovery_has_microsofts_shape(
    http: httpx.AsyncClient, settings: Settings
) -> None:
    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mock_doc = (await http.get(discovery_url(settings))).json()
    assert set(mock_doc) == set(recorded)
    assert mock_doc["issuer"].endswith(f"/{settings.entra_tenant_id}/v2.0")


async def test_key_rotation_is_picked_up(
    oidc: OidcClient, http: httpx.AsyncClient
) -> None:
    await sign_in(oidc, http, MockIdentity(email="first@example.com"))
    old_kid = provider.keys.active_kid
    new_kid = provider.keys.rotate()
    assert new_kid != old_kid
    jwks = (await http.get(_jwks_url(oidc))).json()
    assert {k["kid"] for k in jwks["keys"]} == {old_kid, new_kid}

    claims = await sign_in(oidc, http, MockIdentity(email="second@example.com"))
    assert claims.email == "second@example.com"


def _jwks_url(oidc: OidcClient) -> str:
    assert oidc._metadata is not None
    return oidc._metadata.jwks_uri


async def _code_with_verifier(
    http: httpx.AsyncClient, settings: Settings, verifier: str
) -> str:
    provider.auto_identity = MockIdentity(email="pkce@example.com")
    tenant = settings.entra_tenant_id
    response = await http.get(
        f"{settings.oidc_authority}/{tenant}/oauth2/v2.0/authorize",
        params={
            "client_id": settings.entra_client_id,
            "response_type": "code",
            "redirect_uri": settings.entra_redirect_uri,
            "scope": SCOPES,
            "state": "s",
            "nonce": "n",
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 302
    return parse_qs(urlsplit(response.headers["location"]).query)["code"][0]


async def _redeem(
    http: httpx.AsyncClient, settings: Settings, code: str, verifier: str, **extra: str
) -> httpx.Response:
    secret = settings.entra_client_secret
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": str(settings.entra_redirect_uri),
        "client_id": str(settings.entra_client_id),
        "client_secret": secret.get_secret_value() if secret else "",
        "code_verifier": verifier,
        **extra,
    }
    return await http.post(
        f"{settings.oidc_authority}/{settings.entra_tenant_id}/oauth2/v2.0/token",
        data=form,
    )


async def test_authorization_code_is_single_use(
    http: httpx.AsyncClient, settings: Settings
) -> None:
    verifier = secrets.token_urlsafe(64)
    code = await _code_with_verifier(http, settings, verifier)
    first = await _redeem(http, settings, code, verifier)
    assert first.status_code == 200
    assert first.json()["id_token"]

    again = await _redeem(http, settings, code, verifier)
    assert again.status_code == 400
    assert again.json()["error"] == "invalid_grant"
    assert "error_description" in again.json()


async def test_wrong_pkce_verifier_is_rejected(
    http: httpx.AsyncClient, settings: Settings
) -> None:
    code = await _code_with_verifier(http, settings, secrets.token_urlsafe(64))
    response = await _redeem(http, settings, code, secrets.token_urlsafe(64))
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_grant"
    assert "Code_Verifier" in body["error_description"]


async def test_wrong_client_secret_is_rejected(
    http: httpx.AsyncClient, settings: Settings
) -> None:
    verifier = secrets.token_urlsafe(64)
    code = await _code_with_verifier(http, settings, verifier)
    response = await _redeem(http, settings, code, verifier, client_secret="nope")  # noqa: S106 - wrong test value
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


async def test_authorize_rejects_unregistered_redirect_uri(
    http: httpx.AsyncClient, settings: Settings
) -> None:
    response = await http.get(
        f"{settings.oidc_authority}/{settings.entra_tenant_id}/oauth2/v2.0/authorize",
        params={
            "client_id": settings.entra_client_id,
            "response_type": "code",
            "redirect_uri": "https://evil.example.com/cb",
            "scope": SCOPES,
            "code_challenge": "x" * 43,
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 400
    assert set(response.json()) == {"error", "error_description"}


async def test_authorize_renders_pick_user_form_without_auto_identity(
    http: httpx.AsyncClient, oidc: OidcClient
) -> None:
    request = await oidc.build_authorize_url()
    response = await http.get(request.url)
    assert response.status_code == 200
    assert "<form" in response.text
    assert 'name="email"' in response.text


async def test_token_exchange_errors_fail_closed(
    oidc: OidcClient, http: httpx.AsyncClient
) -> None:
    with pytest.raises(OidcError, match="invalid_grant"):
        await oidc.exchange_code("not-a-real-code", "verifier")


# ---------------------------------------------------------------------------
# Negative ID token validation (all fail closed)
# ---------------------------------------------------------------------------


async def _validate(oidc: OidcClient, token: str, nonce: str = "n-1") -> None:
    await oidc.validate_id_token(token, nonce)


async def test_valid_crafted_token_passes(oidc: OidcClient, settings: Settings) -> None:
    claims = await oidc.validate_id_token(
        provider.keys.sign(valid_claims(settings)), "n-1"
    )
    assert claims.email == "ada@example.com"


@pytest.mark.parametrize(
    ("overrides", "nonce"),
    [
        ({"iss": "https://login.microsoftonline.com/other-tenant/v2.0"}, "n-1"),
        ({"aud": "some-other-client"}, "n-1"),
        ({"tid": "99999999-9999-9999-9999-999999999999"}, "n-1"),
        ({"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200}, "n-1"),
        ({"nbf": int(time.time()) + 3600}, "n-1"),
        ({}, "different-nonce"),
        ({"nonce": None}, "n-1"),
        ({"ver": "1.0"}, "n-1"),
        ({"oid": None}, "n-1"),
        ({"preferred_username": None}, "n-1"),
        ({"exp": None}, "n-1"),
    ],
    ids=[
        "wrong-iss",
        "wrong-aud",
        "wrong-tid",
        "expired",
        "not-yet-valid",
        "bad-nonce",
        "missing-nonce",
        "v1-token",
        "missing-oid",
        "no-email-or-upn",
        "missing-exp",
    ],
)
async def test_invalid_claims_are_rejected(
    oidc: OidcClient, settings: Settings, overrides: dict, nonce: str
) -> None:
    token = provider.keys.sign(valid_claims(settings, **overrides))
    with pytest.raises(TokenValidationError):
        await _validate(oidc, token, nonce)


async def test_expiry_leeway_is_sixty_seconds(
    oidc: OidcClient, settings: Settings
) -> None:
    now = int(time.time())
    within = valid_claims(settings, exp=now - 30, iat=now - 600, nbf=now - 600)
    await _validate(oidc, provider.keys.sign(within))
    beyond = valid_claims(settings, exp=now - 120, iat=now - 600, nbf=now - 600)
    with pytest.raises(TokenValidationError):
        await _validate(oidc, provider.keys.sign(beyond))


async def test_bad_signature_is_rejected(oidc: OidcClient, settings: Settings) -> None:
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        valid_claims(settings),
        attacker,
        algorithm="RS256",
        headers={"kid": provider.keys.active_kid},
    )
    with pytest.raises(TokenValidationError):
        await _validate(oidc, token)


async def test_tampered_payload_is_rejected(
    oidc: OidcClient, settings: Settings
) -> None:
    header, _payload, signature = provider.keys.sign(valid_claims(settings)).split(".")
    forged = jwt.utils.base64url_encode(
        json.dumps(valid_claims(settings, oid="someone-else")).encode()
    ).decode()
    with pytest.raises(TokenValidationError):
        await _validate(oidc, f"{header}.{forged}.{signature}")


async def test_alg_none_is_rejected(oidc: OidcClient, settings: Settings) -> None:
    token = jwt.encode(
        valid_claims(settings),
        key=None,
        algorithm="none",
        headers={"kid": provider.keys.active_kid},
    )
    with pytest.raises(TokenValidationError, match="algorithm"):
        await _validate(oidc, token)


async def test_hs256_key_confusion_is_rejected(
    oidc: OidcClient, settings: Settings
) -> None:
    token = jwt.encode(
        valid_claims(settings),
        "shared-secret-guess-shared-secret-guess",
        algorithm="HS256",
        headers={"kid": provider.keys.active_kid},
    )
    with pytest.raises(TokenValidationError, match="algorithm"):
        await _validate(oidc, token)


async def test_malformed_token_is_rejected(oidc: OidcClient) -> None:
    with pytest.raises(TokenValidationError):
        await _validate(oidc, "not.a.jwt")


async def test_unknown_kid_refetches_once_then_fails(
    oidc: OidcClient, http: httpx.AsyncClient, settings: Settings
) -> None:
    await _validate(oidc, provider.keys.sign(valid_claims(settings)))  # warm cache
    jwks_path = urlsplit(_jwks_url(oidc)).path
    before = http.recorded.count(jwks_path)  # type: ignore[attr-defined]

    stranger = MockKeyStore()
    token = stranger.sign(valid_claims(settings))
    with pytest.raises(TokenValidationError, match="unknown signing key"):
        await _validate(oidc, token)
    assert http.recorded.count(jwks_path) == before + 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Recorded Microsoft discovery document; router mounting
# ---------------------------------------------------------------------------


def test_recorded_entra_discovery_document_parses() -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    tenant = "9188040d-6c67-4c5b-b112-36a304b66dad"
    meta = parse_discovery(document, tenant)
    assert meta.issuer == f"https://login.microsoftonline.com/{tenant}/v2.0"
    assert meta.token_endpoint.endswith(f"/{tenant}/oauth2/v2.0/token")
    assert meta.jwks_uri.endswith(f"/{tenant}/discovery/v2.0/keys")


def test_discovery_for_another_tenant_is_rejected() -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with pytest.raises(OidcError, match="tenant"):
        parse_discovery(document, "11111111-1111-1111-1111-111111111111")


def test_multi_tenant_issuer_placeholder_is_pinned() -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document["issuer"] = "https://login.microsoftonline.com/{tenantid}/v2.0"
    meta = parse_discovery(document, "abc-tenant")
    assert meta.issuer == "https://login.microsoftonline.com/abc-tenant/v2.0"


async def test_real_mode_uses_microsoft_discovery_through_same_code(
    settings: Settings,
) -> None:
    tenant = "9188040d-6c67-4c5b-b112-36a304b66dad"
    real = settings.model_copy(
        update={"auth_dev_mock": False, "entra_tenant_id": tenant}
    )
    seen: list[str] = []
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=fixture)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        meta = await OidcClient(real, http_client=client).metadata()
    assert seen == [
        f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"
    ]
    assert meta.authorization_endpoint.startswith("https://login.microsoftonline.com/")


async def test_discovery_outage_fails_closed(settings: Settings) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(503))
    ) as client:
        with pytest.raises(OidcError):
            await OidcClient(settings, http_client=client).build_authorize_url()


def test_mock_router_is_absent_when_flag_is_off(settings: Settings) -> None:
    app = FastAPI()
    off = settings.model_copy(update={"auth_dev_mock": False})
    assert mount_mock_idp(app, off) is False
    assert not [r for r in app.routes if "mock-idp" in getattr(r, "path", "")]

    on_app = FastAPI()
    assert mount_mock_idp(on_app, settings) is True
    assert [r for r in on_app.routes if "mock-idp" in getattr(r, "path", "")]
