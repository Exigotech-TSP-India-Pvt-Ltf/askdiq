"""OAuth2 client-credentials token for Azure Resource Manager (plain httpx).

One POST to ``{authority}/{tenant}/oauth2/v2.0/token``; the token is cached
in-process until ``expires_in - 300 s`` and refreshed by one caller at a time.
Neither the secret nor the token is ever logged or put in an exception.
``azure-identity`` is deliberately not used (plan section 3.2); this module is
the single seam to swap if managed identity is adopted later.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import httpx
from pydantic import SecretStr

from app.azure_insights.errors import AzureAuthError, AzureNotConfiguredError
from app.core.config import Settings, get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

AUTHORITY_HOST = "https://login.microsoftonline.com"
ARM_SCOPE = "https://management.azure.com/.default"
# Refresh this long before the token expires.
EXPIRY_MARGIN_SECONDS = 300.0
_MIN_LIFETIME_SECONDS = 60.0


class ClientCredentialsTokenProvider:  # pylint: disable=too-many-instance-attributes
    """Caches one bearer token for ``scope``."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        tenant_id: str,
        client_id: str,
        client_secret: SecretStr,
        *,
        scope: str = ARM_SCOPE,
        authority_host: str = AUTHORITY_HOST,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token_url = (
            f"{authority_host.rstrip('/')}/{tenant_id.strip()}/oauth2/v2.0/token"
        )
        self._client_id = client_id.strip()
        self._secret = client_secret
        self._scope = scope
        self._timeout = timeout
        self._transport = transport
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self.fetch_count = 0

    def __repr__(self) -> str:  # never show the secret or token
        return f"ClientCredentialsTokenProvider(client_id={self._client_id!r})"

    def invalidate(self) -> None:
        """Drop the cached token (after a 401 from ARM)."""
        self._token = None
        self._expires_at = 0.0

    def _valid(self) -> bool:
        return self._token is not None and self._clock() < self._expires_at

    async def token(self) -> str:
        if self._valid():
            return self._token  # type: ignore[return-value]
        async with self._lock:
            if not self._valid():
                await self._fetch()
            return self._token  # type: ignore[return-value]

    async def _fetch(self) -> None:
        form = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._secret.get_secret_value(),
            "scope": self._scope,
        }
        secret = (self._secret.get_secret_value(),)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as http:
                response = await http.post(self._token_url, data=form)
        except httpx.HTTPError as exc:
            raise AzureAuthError("token_request_failed", type(exc).__name__) from None
        self.fetch_count += 1
        body = _json(response)
        if response.status_code != httpx.codes.OK or "access_token" not in body:
            # AAD error: ``error`` (e.g. invalid_client) + description with an
            # AADSTS code; never the request (it holds the secret).
            description = str(body.get("error_description", "")).partition("\n")[0]
            err = AzureAuthError(
                str(body.get("error") or f"http_{response.status_code}"),
                description[:200],
                status_code=response.status_code,
            )
            err.message = _mask(err.message, secret)
            logger.warning("azure_token_request_failed", code=err.code)
            raise err
        lifetime = float(body.get("expires_in") or 3600)
        self._token = str(body["access_token"])
        self._expires_at = self._clock() + max(
            _MIN_LIFETIME_SECONDS, lifetime - EXPIRY_MARGIN_SECONDS
        )
        logger.info("azure_token_acquired", expires_in=int(lifetime))


def _json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _mask(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


_providers: dict[tuple[str, str], ClientCredentialsTokenProvider] = {}


def get_token_provider(
    settings: Settings | None = None,
) -> ClientCredentialsTokenProvider:
    """The process-wide provider for the configured app registration."""
    cfg = settings or get_settings()
    if not cfg.azure_insights_configured or cfg.azure_client_secret is None:
        raise AzureNotConfiguredError()
    key = (str(cfg.azure_tenant_id), str(cfg.azure_client_id))
    provider = _providers.get(key)
    if provider is None:
        provider = ClientCredentialsTokenProvider(
            key[0],
            key[1],
            cfg.azure_client_secret,
            timeout=cfg.azure_http_timeout_seconds,
        )
        _providers.clear()  # one registration at a time
        _providers[key] = provider
    return provider


def reset_token_providers() -> None:
    """Forget cached tokens (tests, secret rotation)."""
    _providers.clear()
