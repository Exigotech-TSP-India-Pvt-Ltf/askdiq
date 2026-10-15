"""Entra ID OIDC client: authorization code flow with PKCE (planchanges4 B2.2).

One code path serves real Entra and the local mock IdP; only the authority
base differs (``Settings.oidc_authority``). Endpoints come from the discovery
document, never from hard-coded URLs.

- ``build_authorize_url`` creates ``state``, ``nonce`` and an S256 PKCE
  verifier, stored single-use in the shared ``StateBackend`` (10 min TTL), so
  the callback can land on any worker or replica.
- ``complete_login`` consumes the state, exchanges the code server-side with
  the client secret, and validates the ID token: RS256 only, signature via
  JWKS (refetched once on an unknown ``kid``), ``iss``, ``aud``,
  ``tid == ENTRA_TENANT_ID``, ``exp``/``nbf``/``iat`` with leeway, ``nonce``
  and ``ver == "2.0"``. Every failure raises and is fail-closed.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWK

from app.core.config import Settings, get_settings
from app.core.logging_config import get_logger
from app.core.state import StateBackend, get_state_backend

logger = get_logger(__name__)

ALLOWED_ALGORITHMS = ("RS256",)
SCOPES = "openid profile email"
ID_TOKEN_VERSION = "2.0"  # noqa: S105 - claim value, not a secret
STATE_KEY_PREFIX = "oidc:state:"
_REQUIRED_DISCOVERY_FIELDS = (
    "issuer",
    "authorization_endpoint",
    "token_endpoint",
    "jwks_uri",
)
_REQUIRED_CLAIMS = ["exp", "iat", "nbf", "iss", "aud", "sub"]


class OidcError(Exception):
    """Any OIDC failure; ``reason`` is safe to log (never contains secrets)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class OidcStateError(OidcError):
    """Unknown, expired or replayed ``state`` (the callback answers 400)."""


class TokenValidationError(OidcError):
    """The ID token failed validation."""


@dataclass(frozen=True, slots=True)
class ProviderMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


@dataclass(frozen=True, slots=True)
class AuthorizeRequest:
    url: str
    state: str


@dataclass(frozen=True, slots=True)
class IdentityClaims:
    oid: str
    tid: str
    sub: str
    email: str
    name: str
    email_claim_present: bool


def discovery_url(settings: Settings) -> str:
    return (
        f"{settings.oidc_authority}/{settings.entra_tenant_id}"
        "/v2.0/.well-known/openid-configuration"
    )


def parse_discovery(document: dict[str, Any], tenant_id: str) -> ProviderMetadata:
    """Validate a discovery document (Microsoft's v2.0 shape) and pin the tenant."""
    missing = [f for f in _REQUIRED_DISCOVERY_FIELDS if not document.get(f)]
    if missing:
        raise OidcError(f"discovery document missing: {', '.join(missing)}")
    algs = document.get("id_token_signing_alg_values_supported") or ["RS256"]
    if "RS256" not in algs:
        raise OidcError("discovery document does not support RS256")
    # Multi-tenant documents use a "{tenantid}" placeholder in the issuer.
    issuer = str(document["issuer"]).replace("{tenantid}", tenant_id)
    if tenant_id not in issuer:
        raise OidcError("discovery issuer does not match the configured tenant")
    return ProviderMetadata(
        issuer=issuer,
        authorization_endpoint=str(document["authorization_endpoint"]),
        token_endpoint=str(document["token_endpoint"]),
        jwks_uri=str(document["jwks_uri"]),
    )


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def parse_jwks(document: dict[str, Any]) -> dict[str, PyJWK]:
    """``kid -> key`` for every usable RSA signing key in a JWKS document."""
    keys: dict[str, PyJWK] = {}
    for entry in document.get("keys") or []:
        kid = entry.get("kid")
        if not kid or entry.get("kty") != "RSA" or entry.get("use", "sig") != "sig":
            continue
        try:
            keys[kid] = PyJWK(entry, algorithm="RS256")
        except jwt.PyJWTError:
            continue
    return keys


class OidcClient:
    """OIDC relying party; metadata and JWKS are cached per process (public data)."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
        state_backend: StateBackend | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings or get_settings()
        self._http_client = http_client
        self._state_backend = state_backend
        self._clock = clock
        self._metadata: ProviderMetadata | None = None
        self._metadata_at = 0.0
        self._jwks: dict[str, PyJWK] = {}
        self._jwks_at = 0.0
        self._metadata_lock = asyncio.Lock()
        self._jwks_lock = asyncio.Lock()

    @property
    def _state(self) -> StateBackend:
        return self._state_backend or get_state_backend()

    @asynccontextmanager
    async def _http(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._http_client is not None:
            yield self._http_client
            return
        async with httpx.AsyncClient(
            timeout=self._settings.oidc_http_timeout_seconds
        ) as client:
            yield client

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            async with self._http() as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise OidcError(f"request failed: {type(exc).__name__}") from exc
        if response.status_code != httpx.codes.OK:
            raise OidcError(f"unexpected status {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise OidcError("response is not JSON") from exc
        if not isinstance(body, dict):
            raise OidcError("response is not a JSON object")
        return body

    def _fresh(self, fetched_at: float) -> bool:
        return self._clock() - fetched_at < self._settings.oidc_metadata_cache_seconds

    async def metadata(self) -> ProviderMetadata:
        if self._metadata is not None and self._fresh(self._metadata_at):
            return self._metadata
        async with self._metadata_lock:
            if self._metadata is None or not self._fresh(self._metadata_at):
                document = await self._get_json(discovery_url(self._settings))
                self._metadata = parse_discovery(
                    document, str(self._settings.entra_tenant_id)
                )
                self._metadata_at = self._clock()
        return self._metadata

    async def _refresh_jwks(self, jwks_uri: str) -> None:
        self._jwks = parse_jwks(await self._get_json(jwks_uri))
        self._jwks_at = self._clock()

    async def signing_key(self, kid: str) -> PyJWK:
        """Key for ``kid``; an unknown kid triggers exactly one JWKS refetch."""
        if kid in self._jwks and self._fresh(self._jwks_at):
            return self._jwks[kid]
        meta = await self.metadata()
        async with self._jwks_lock:
            await self._refresh_jwks(meta.jwks_uri)
        key = self._jwks.get(kid)
        if key is None:
            raise TokenValidationError("unknown signing key")
        return key

    async def build_authorize_url(self) -> AuthorizeRequest:
        meta = await self.metadata()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        stored = await self._state.put_once(
            STATE_KEY_PREFIX + state,
            {"nonce": nonce, "code_verifier": verifier},
            ttl=self._settings.oidc_state_ttl_seconds,
        )
        if not stored:  # 256-bit collision: practically impossible, fail closed.
            raise OidcError("could not persist state")
        params = {
            "client_id": self._settings.entra_client_id,
            "response_type": "code",
            "redirect_uri": self._settings.entra_redirect_uri,
            "response_mode": "query",
            "scope": SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
        return AuthorizeRequest(
            url=f"{meta.authorization_endpoint}?{urlencode(params)}", state=state
        )

    async def complete_login(self, code: str, state: str) -> IdentityClaims:
        """Consume ``state`` (single use), exchange ``code`` and validate the token."""
        if not code or not state:
            raise OidcStateError("missing code or state")
        stored = await self._state.take_once(STATE_KEY_PREFIX + state)
        if stored is None:
            raise OidcStateError("unknown, expired or replayed state")
        id_token = await self.exchange_code(code, str(stored["code_verifier"]))
        return await self.validate_id_token(id_token, str(stored["nonce"]))

    async def exchange_code(self, code: str, code_verifier: str) -> str:
        meta = await self.metadata()
        secret = self._settings.entra_client_secret
        form = {
            "client_id": str(self._settings.entra_client_id),
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": str(self._settings.entra_redirect_uri),
            "code_verifier": code_verifier,
            "scope": SCOPES,
            "client_secret": secret.get_secret_value() if secret else "",
        }
        try:
            async with self._http() as client:
                response = await client.post(meta.token_endpoint, data=form)
        except httpx.HTTPError as exc:
            raise OidcError(f"token request failed: {type(exc).__name__}") from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code != httpx.codes.OK:
            error = body.get("error", "unknown_error") if isinstance(body, dict) else ""
            raise OidcError(f"token endpoint error: {error}")
        id_token = body.get("id_token") if isinstance(body, dict) else None
        if not id_token:
            raise OidcError("token response has no id_token")
        return str(id_token)

    async def validate_id_token(self, token: str, nonce: str) -> IdentityClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenValidationError("malformed token") from exc
        if header.get("alg") not in ALLOWED_ALGORITHMS:
            raise TokenValidationError("disallowed signing algorithm")
        kid = header.get("kid")
        if not kid:
            raise TokenValidationError("token has no kid")
        key = await self.signing_key(str(kid))
        meta = await self.metadata()
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=list(ALLOWED_ALGORITHMS),
                audience=self._settings.entra_client_id,
                issuer=meta.issuer,
                leeway=self._settings.oidc_clock_skew_seconds,
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as exc:
            raise TokenValidationError(f"invalid token: {type(exc).__name__}") from exc
        return _identity_from_claims(claims, str(self._settings.entra_tenant_id), nonce)


def _same(a: object, b: str) -> bool:
    return isinstance(a, str) and hmac.compare_digest(a.encode(), b.encode())


def _identity_from_claims(
    claims: dict[str, Any], tenant_id: str, nonce: str
) -> IdentityClaims:
    if not _same(claims.get("tid"), tenant_id):
        raise TokenValidationError("wrong tenant")
    if not _same(claims.get("nonce"), nonce):
        raise TokenValidationError("nonce mismatch")
    if claims.get("ver") != ID_TOKEN_VERSION:
        raise TokenValidationError("unsupported token version")
    oid = claims.get("oid")
    if not isinstance(oid, str) or not oid:
        raise TokenValidationError("token has no oid")
    # Entra often omits `email`; fall back to `preferred_username`.
    raw_email = claims.get("email") or claims.get("preferred_username")
    if not isinstance(raw_email, str) or "@" not in raw_email:
        raise TokenValidationError("token has no usable email")
    email = raw_email.strip().lower()
    name = claims.get("name")
    return IdentityClaims(
        oid=oid,
        tid=tenant_id,
        sub=str(claims["sub"]),
        email=email,
        name=name if isinstance(name, str) and name else email,
        email_claim_present=bool(claims.get("email")),
    )


_client: OidcClient | None = None


def get_oidc_client() -> OidcClient:
    """Process-wide client (shares the metadata/JWKS cache)."""
    global _client  # noqa: PLW0603 - lazy process singleton
    if _client is None:
        _client = OidcClient()
    return _client
