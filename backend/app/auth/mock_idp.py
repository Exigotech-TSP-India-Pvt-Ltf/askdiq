"""Entra-faithful mock OIDC provider (development and tests only, B2.2.3).

Mounted only when ``AUTH_DEV_MOCK=true`` (and settings refuse that flag
unless ``ENV=development``). It mirrors Microsoft identity platform v2.0:

- ``/mock-idp/{tenant}/v2.0/.well-known/openid-configuration`` (Microsoft's
  discovery shape; issuer ``{base}/{tid}/v2.0``)
- ``/mock-idp/{tenant}/oauth2/v2.0/authorize`` (GET renders a tiny "pick a
  user" form; tests set ``provider.auto_identity`` to auto-approve)
- ``/mock-idp/{tenant}/oauth2/v2.0/token`` (single-use codes, PKCE S256,
  client secret; OAuth error shape ``{error, error_description}``)
- ``/mock-idp/{tenant}/discovery/v2.0/keys`` (RS256 JWKS with ``kid``;
  ``provider.keys.rotate()`` serves a second key)

ID tokens carry ``ver "2.0"``, ``oid``, ``tid``, ``sub``, ``name``,
``preferred_username``, ``nonce``, ``aud``, ``iat``, ``nbf`` and ``exp``;
``email`` is omitted unless the identity asks for it, as Entra often does.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jwt.algorithms import RSAAlgorithm

from app.auth.oidc import pkce_challenge
from app.core.config import Settings, get_settings
from app.core.state import get_state_backend

PREFIX = "/mock-idp"
CODE_TTL_SECONDS = 600
ID_TOKEN_LIFETIME_SECONDS = 3600
RSA_KEY_BITS = 2048
_CODE_KEY = "mockidp:code:"


@dataclass(slots=True)
class MockIdentity:
    """The user the mock signs in; tests choose every claim."""

    email: str = "new.user@example.com"
    name: str = "New User"
    oid: str = field(default_factory=lambda: str(uuid.uuid4()))
    tid: str | None = None  # None = the configured tenant
    preferred_username: str | None = None  # None = email
    include_email: bool = False


class MockKeyStore:
    """RS256 signing keys; the newest is active, older ones stay in the JWKS."""

    def __init__(self) -> None:
        self._keys: list[tuple[str, rsa.RSAPrivateKey]] = []

    def _ensure(self) -> None:
        if not self._keys:
            self.rotate()

    def rotate(self) -> str:
        """Add a new active signing key (the key-rotation hook); returns its kid."""
        kid = secrets.token_urlsafe(16)
        key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_BITS)
        self._keys.append((kid, key))
        return kid

    @property
    def active_kid(self) -> str:
        self._ensure()
        return self._keys[-1][0]

    def jwks(self) -> dict[str, Any]:
        self._ensure()
        keys = []
        for kid, key in self._keys:
            jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
            jwk.update({"kid": kid, "use": "sig"})
            keys.append(jwk)
        return {"keys": keys}

    def sign(self, claims: dict[str, Any], kid: str | None = None) -> str:
        self._ensure()
        chosen = kid or self.active_kid
        key = next(k for key_id, k in self._keys if key_id == chosen)
        return jwt.encode(
            claims, key, algorithm="RS256", headers={"kid": chosen, "typ": "JWT"}
        )


class MockIdentityProvider:
    def __init__(self) -> None:
        self.keys = MockKeyStore()
        # When set, GET /authorize approves immediately (tests, smoke runs).
        self.auto_identity: MockIdentity | None = None

    def reset(self) -> None:
        self.keys = MockKeyStore()
        self.auto_identity = None


provider = MockIdentityProvider()


def issuer_for(settings: Settings, tenant: str) -> str:
    return f"{settings.oidc_authority}/{tenant}/v2.0"


def discovery_document(settings: Settings, tenant: str) -> dict[str, Any]:
    """Same keys and URL layout as Microsoft's v2.0 discovery document."""
    base = settings.oidc_authority
    return {
        "token_endpoint": f"{base}/{tenant}/oauth2/v2.0/token",
        "token_endpoint_auth_methods_supported": [
            "client_secret_post",
            "private_key_jwt",
            "client_secret_basic",
        ],
        "jwks_uri": f"{base}/{tenant}/discovery/v2.0/keys",
        "response_modes_supported": ["query", "fragment", "form_post"],
        "subject_types_supported": ["pairwise"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "response_types_supported": [
            "code",
            "id_token",
            "code id_token",
            "id_token token",
        ],
        "scopes_supported": ["openid", "profile", "email", "offline_access"],
        "issuer": issuer_for(settings, tenant),
        "request_uri_parameter_supported": False,
        "userinfo_endpoint": f"{base}/oidc/userinfo",
        "authorization_endpoint": f"{base}/{tenant}/oauth2/v2.0/authorize",
        "device_authorization_endpoint": f"{base}/{tenant}/oauth2/v2.0/devicecode",
        "http_logout_supported": True,
        "frontchannel_logout_supported": True,
        "end_session_endpoint": f"{base}/{tenant}/oauth2/v2.0/logout",
        "claims_supported": [
            "sub",
            "iss",
            "cloud_instance_name",
            "cloud_instance_host_name",
            "cloud_graph_host_name",
            "msgraph_host",
            "aud",
            "exp",
            "iat",
            "auth_time",
            "acr",
            "nonce",
            "preferred_username",
            "name",
            "tid",
            "ver",
            "at_hash",
            "c_hash",
            "email",
        ],
        "kerberos_endpoint": f"{base}/{tenant}/kerberos",
        "tenant_region_scope": "OC",
        "cloud_instance_name": "microsoftonline.com",
        "cloud_graph_host_name": "graph.windows.net",
        "msgraph_host": "graph.microsoft.com",
        "rbac_url": "https://pas.windows.net",
    }


def build_id_token(
    settings: Settings, identity: MockIdentity, nonce: str | None, tenant: str
) -> str:
    now = int(time.time())
    client_id = str(settings.entra_client_id)
    sub_digest = hashlib.sha256(f"{identity.oid}:{client_id}".encode()).digest()
    claims: dict[str, Any] = {
        "aud": client_id,
        "iss": issuer_for(settings, tenant),
        "iat": now,
        "nbf": now,
        "exp": now + ID_TOKEN_LIFETIME_SECONDS,
        "name": identity.name,
        "oid": identity.oid,
        "preferred_username": identity.preferred_username or identity.email,
        "rh": "0.mock-idp",
        "sid": str(uuid.uuid4()),
        "sub": base64.urlsafe_b64encode(sub_digest).rstrip(b"=").decode(),
        "tid": identity.tid or tenant,
        "uti": secrets.token_urlsafe(16),
        "ver": "2.0",
    }
    if nonce is not None:
        claims["nonce"] = nonce
    if identity.include_email:
        claims["email"] = identity.email
    return provider.keys.sign(claims)


def _oauth_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description}, status_code=status_code
    )


def _check_authorize(
    settings: Settings, tenant: str, params: dict[str, str]
) -> JSONResponse | None:
    if tenant != settings.entra_tenant_id:
        return _oauth_error("invalid_request", "AADSTS90002: Tenant not found.")
    if params.get("client_id") != settings.entra_client_id:
        return _oauth_error(
            "unauthorized_client", "AADSTS700016: Application not found."
        )
    if params.get("response_type") != "code":
        return _oauth_error(
            "unsupported_response_type", "Only response_type=code is supported."
        )
    if params.get("redirect_uri") != settings.entra_redirect_uri:
        return _oauth_error(
            "invalid_request", "AADSTS50011: The redirect URI does not match."
        )
    if "openid" not in (params.get("scope") or "").split():
        return _oauth_error("invalid_scope", "The openid scope is required.")
    if params.get("code_challenge_method") != "S256" or not params.get(
        "code_challenge"
    ):
        return _oauth_error("invalid_request", "PKCE with S256 is required.")
    return None


async def _issue_code(params: dict[str, str], identity: MockIdentity) -> str:
    code = secrets.token_urlsafe(32)
    await get_state_backend().put_once(
        _CODE_KEY + code,
        {
            "client_id": params["client_id"],
            "redirect_uri": params["redirect_uri"],
            "code_challenge": params["code_challenge"],
            "nonce": params.get("nonce"),
            "identity": {
                "email": identity.email,
                "name": identity.name,
                "oid": identity.oid,
                "tid": identity.tid,
                "preferred_username": identity.preferred_username,
                "include_email": identity.include_email,
            },
        },
        ttl=CODE_TTL_SECONDS,
    )
    return code


def _redirect_with_code(params: dict[str, str], code: str) -> RedirectResponse:
    query = urlencode({"code": code, "state": params.get("state", "")})
    return RedirectResponse(f"{params['redirect_uri']}?{query}", status_code=302)


_AUTHORIZE_FIELDS = (
    "client_id",
    "response_type",
    "redirect_uri",
    "response_mode",
    "scope",
    "state",
    "nonce",
    "code_challenge",
    "code_challenge_method",
)


def _pick_user_form(params: dict[str, str]) -> HTMLResponse:
    hidden = "".join(
        f'<input type="hidden" name="{name}" value="{html.escape(params.get(name, ""))}">'
        for name in _AUTHORIZE_FIELDS
    )
    page = (
        "<!doctype html><html><head><title>Mock Entra sign-in</title></head><body>"
        "<h1>Mock Entra ID (development only)</h1>"
        '<form method="post">'
        f"{hidden}"
        '<label>Email <input name="email" type="email" required></label><br>'
        '<label>Name <input name="name"></label><br>'
        '<label>Object id (oid) <input name="oid"></label><br>'
        '<label><input type="checkbox" name="include_email" value="true">'
        " Include the email claim</label><br>"
        '<button type="submit">Sign in</button>'
        "</form></body></html>"
    )
    return HTMLResponse(page)


router = APIRouter(prefix=PREFIX, tags=["mock-idp"], include_in_schema=False)


@router.get("/{tenant}/v2.0/.well-known/openid-configuration")
async def mock_discovery(tenant: str) -> dict[str, Any]:
    return discovery_document(get_settings(), tenant)


@router.get("/{tenant}/discovery/v2.0/keys")
async def mock_jwks(tenant: str) -> dict[str, Any]:  # noqa: ARG001 - Entra URL shape
    return provider.keys.jwks()


@router.get("/{tenant}/oauth2/v2.0/authorize", response_model=None)
async def mock_authorize(
    tenant: str, request: Request
) -> HTMLResponse | JSONResponse | RedirectResponse:
    params = {k: v for k, v in request.query_params.items() if k in _AUTHORIZE_FIELDS}
    error = _check_authorize(get_settings(), tenant, params)
    if error is not None:
        return error
    if provider.auto_identity is not None:
        return _redirect_with_code(
            params, await _issue_code(params, provider.auto_identity)
        )
    return _pick_user_form(params)


@router.post("/{tenant}/oauth2/v2.0/authorize", response_model=None)
async def mock_authorize_submit(
    tenant: str,
    request: Request,
    email: str = Form(..., max_length=320),
    name: str = Form("", max_length=256),
    oid: str = Form("", max_length=64),
    include_email: bool = Form(False),
) -> JSONResponse | RedirectResponse:
    form = await request.form()
    params = {k: str(form.get(k, "")) for k in _AUTHORIZE_FIELDS if form.get(k)}
    error = _check_authorize(get_settings(), tenant, params)
    if error is not None:
        return error
    identity = MockIdentity(
        email=email.strip(),
        name=name.strip() or email.strip(),
        oid=oid.strip() or str(uuid.uuid5(uuid.NAMESPACE_URL, email.lower())),
        include_email=include_email,
    )
    return _redirect_with_code(params, await _issue_code(params, identity))


def _client_credentials(
    request: Request, client_id: str, client_secret: str
) -> tuple[str, str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode()
            basic_id, _, basic_secret = decoded.partition(":")
        except ValueError:
            return client_id, client_secret
        return basic_id, basic_secret
    return client_id, client_secret


@router.post("/{tenant}/oauth2/v2.0/token")
async def mock_token(
    tenant: str,
    request: Request,
    grant_type: str = Form(""),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    code_verifier: str = Form(""),
) -> JSONResponse:
    settings = get_settings()
    if tenant != settings.entra_tenant_id:
        return _oauth_error("invalid_request", "AADSTS90002: Tenant not found.")
    if grant_type != "authorization_code":
        return _oauth_error(
            "unsupported_grant_type", "Only authorization_code is supported."
        )
    client_id, client_secret = _client_credentials(request, client_id, client_secret)
    expected_secret = settings.entra_client_secret
    if client_id != settings.entra_client_id or not (
        expected_secret
        and hmac.compare_digest(
            client_secret.encode(), expected_secret.get_secret_value().encode()
        )
    ):
        return _oauth_error(
            "invalid_client",
            "AADSTS7000215: Invalid client secret provided.",
            status_code=401,
        )
    stored = await get_state_backend().take_once(_CODE_KEY + code) if code else None
    if stored is None:
        return _oauth_error(
            "invalid_grant",
            "AADSTS70008: The provided authorization code has expired or was "
            "already redeemed.",
        )
    if stored["client_id"] != client_id or stored["redirect_uri"] != redirect_uri:
        return _oauth_error(
            "invalid_grant", "AADSTS50011: The redirect URI or client does not match."
        )
    if not code_verifier or not hmac.compare_digest(
        pkce_challenge(code_verifier), str(stored["code_challenge"])
    ):
        return _oauth_error(
            "invalid_grant",
            "AADSTS501481: The Code_Verifier does not match the code_challenge "
            "supplied in the authorization request.",
        )
    identity = MockIdentity(**stored["identity"])
    id_token = build_id_token(settings, identity, stored.get("nonce"), tenant)
    return JSONResponse(
        {
            "token_type": "Bearer",
            "scope": "openid profile email",
            "expires_in": ID_TOKEN_LIFETIME_SECONDS - 1,
            "ext_expires_in": ID_TOKEN_LIFETIME_SECONDS - 1,
            "access_token": secrets.token_urlsafe(48),
            "id_token": id_token,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def mount_mock_idp(app: FastAPI, settings: Settings) -> bool:
    """Include the mock router only when AUTH_DEV_MOCK is on (dev/test)."""
    if not settings.auth_dev_mock:
        return False
    app.include_router(router)
    return True
