"""Azure Insights errors and the sanitiser applied to every stored/logged message.

Only an error ``code`` and a short ``message`` ever leave this package: no
headers, no bearer tokens, no client secret, no full response bodies.
"""

from __future__ import annotations

import re

MAX_ERROR_MESSAGE_CHARS = 300
MAX_ERROR_CODE_CHARS = 80

_SCRUB = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    # JWTs (access tokens) wherever they appear.
    re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"),
    re.compile(r"(?i)(client_secret|access_token|password)=[^&\s]+"),
)


def sanitize(text: object, secrets: tuple[str, ...] = ()) -> str:
    """One line, secrets and tokens masked, at most ``MAX_ERROR_MESSAGE_CHARS``."""
    value = " ".join(str(text or "").split())
    for secret in secrets:
        if secret:
            value = value.replace(secret, "***")
    for pattern in _SCRUB:
        value = pattern.sub("***", value)
    if len(value) > MAX_ERROR_MESSAGE_CHARS:
        value = value[: MAX_ERROR_MESSAGE_CHARS - 1] + "…"
    return value


def sanitize_code(code: object) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]", "", str(code or ""))[:MAX_ERROR_CODE_CHARS]


class AzureInsightsError(Exception):
    """Base error: ``code`` and ``message`` are already sanitised."""

    def __init__(
        self, code: str, message: str = "", *, status_code: int | None = None
    ) -> None:
        self.code = sanitize_code(code) or "error"
        self.message = sanitize(message)
        self.status_code = status_code
        super().__init__(f"{self.code}: {self.message}" if self.message else self.code)


class AzureNotConfiguredError(AzureInsightsError):
    """Credentials or ids are missing (AZURE_INSIGHTS_ENABLED=false)."""

    def __init__(self, message: str = "Azure Insights is not configured") -> None:
        super().__init__("not_configured", message)


class AzureAuthError(AzureInsightsError):
    """The client-credentials token request failed (bad/expired secret, tenant)."""


class AzureApiError(AzureInsightsError):
    """A non-retryable HTTP error from Azure (4xx other than 429, or 5xx after
    the in-process retries)."""


class AzureThrottledError(AzureInsightsError):
    """Azure asked us to wait (429 / low remaining reads); retry after
    ``retry_after`` seconds instead of spending a job attempt."""

    def __init__(
        self, retry_after: float, message: str = "", *, code: str = "throttled"
    ) -> None:
        super().__init__(code, message, status_code=429)
        self.retry_after = max(1.0, float(retry_after))


class AzureBudgetExhaustedError(AzureInsightsError):
    """The monthly call cap for a source is reached (we stop, never get billed)."""

    def __init__(self, source: str, cap: int) -> None:
        super().__init__(
            "budget_exhausted",
            f"Monthly call cap reached for {source} ({cap} calls); "
            "polling resumes next month",
        )
        self.source = source
        self.cap = cap


class AzureUnsupportedError(AzureApiError):
    """Azure answered that this subscription / billing setup does not offer
    the API (e.g. Cost Management on classic CSP or sponsorship offers).
    Recorded as state ``unsupported`` (not a failure streak): only the
    affected panel says so, the other sources keep working."""
