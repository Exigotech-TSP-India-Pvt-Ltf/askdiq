"""Async HTTP client for Azure control-plane / public pricing reads.

- one ``httpx.AsyncClient`` per job run (``async with AzureClient(...)``);
- bearer token from :mod:`app.azure_insights.auth` (omitted for public APIs),
  refreshed once on a 401;
- 429: honours ``Retry-After`` and the Cost Management
  ``x-ms-ratelimit-microsoft.costmanagement-qpu-retry-after`` header; a short
  wait is slept in-process, a long one raises :class:`AzureThrottledError` so
  the job is deferred (no attempt spent, no retry storm);
- stops before the ARM read bucket runs dry
  (``x-ms-ratelimit-remaining-subscription-reads`` below a floor);
- 5xx / connection errors: exponential backoff with jitter, a few retries;
- ``nextLink`` (ARM) / ``NextPageLink`` (Retail) pagination with a page cap;
- an optional call budget consulted before every request (metrics cap).

Nothing here logs headers, tokens or bodies; errors carry a sanitised
``code`` and ``message`` only.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, Protocol

import httpx

from app.azure_insights.errors import (
    AzureApiError,
    AzureThrottledError,
    sanitize,
)
from app.core.logging_config import get_logger
from app.jobs.queue import utcnow

logger = get_logger(__name__)

ARM_BASE_URL = "https://management.azure.com"
USER_AGENT = "askdiq-azure-insights/1"

RETRY_AFTER = "retry-after"
QPU_RETRY_AFTER = "x-ms-ratelimit-microsoft.costmanagement-qpu-retry-after"
QPU_CONSUMED = "x-ms-ratelimit-microsoft.costmanagement-qpu-consumed"
QPU_REMAINING = "x-ms-ratelimit-microsoft.costmanagement-qpu-remaining"
REMAINING_READS = "x-ms-ratelimit-remaining-subscription-reads"

# Defaults (overridable per client).
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 20.0
# Waits up to this long are slept in-process; longer ones defer the job.
INLINE_WAIT_MAX_SECONDS = 10.0
# Stop reading when fewer ARM reads than this remain in the bucket.
MIN_REMAINING_READS = 20
LOW_READS_PAUSE_SECONDS = 60.0
DEFAULT_THROTTLE_SECONDS = 60.0
MAX_PAGES = 50
NEXT_LINK_KEYS = ("nextLink", "NextPageLink")


class TokenSource(Protocol):
    async def token(self) -> str: ...

    def invalidate(self) -> None: ...


# Called before every HTTP request; raises AzureBudgetExhaustedError to stop.
CallBudget = Callable[[], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]


def parse_retry_after(headers: httpx.Headers) -> float | None:
    """Seconds to wait from ``Retry-After`` (seconds or HTTP date) or the
    Cost Management QPU header; None when absent/unparseable."""
    for name in (QPU_RETRY_AFTER, RETRY_AFTER):
        raw = headers.get(name)
        if not raw:
            continue
        raw = raw.strip()
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            continue
        return max(0.0, (when - utcnow()).total_seconds())
    return None


def backoff_delay(
    attempt: int,
    *,
    base: float = BACKOFF_BASE_SECONDS,
    cap: float = BACKOFF_MAX_SECONDS,
    rng: random.Random | None = None,
) -> float:
    """Exponential backoff with full jitter: uniform(0, min(cap, base*2^n))."""
    ceiling = min(cap, base * (2 ** max(0, attempt)))
    return (rng or random).uniform(0, ceiling)  # nosec B311 - jitter only


def error_from_response(response: httpx.Response) -> tuple[str, str]:
    """(code, message) from an ARM / Cost Management / Retail error body."""
    try:
        body = response.json()
    except ValueError:
        body = None
    code, message = f"http_{response.status_code}", ""
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        code = str(err.get("code") or err.get("Code") or code)
        message = str(err.get("message") or err.get("Message") or "")
    return code, message


class AzureClient:  # pylint: disable=too-many-instance-attributes
    """Retrying, budgeted JSON client for one job run."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        base_url: str = ARM_BASE_URL,
        token_source: TokenSource | None = None,
        timeout: float = 30.0,
        budget: CallBudget | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = MAX_RETRIES,
        inline_wait_max: float = INLINE_WAIT_MAX_SECONDS,
        min_remaining_reads: int = MIN_REMAINING_READS,
        sleep: Sleep = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token_source = token_source
        self._budget = budget
        self._max_retries = max_retries
        self._inline_wait_max = inline_wait_max
        self._min_remaining_reads = min_remaining_reads
        self._sleep = sleep
        self._rng = rng
        self._http = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=False,
        )
        self.calls = 0
        self.remaining_reads: int | None = None

    async def __aenter__(self) -> AzureClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("https://", "http://")):
            return path_or_url
        return f"{self._base_url}/{path_or_url.lstrip('/')}"

    async def _headers(self) -> dict[str, str]:
        if self._token_source is None:
            return {}
        return {"Authorization": f"Bearer {await self._token_source.token()}"}

    def _check_reads_floor(self) -> None:
        if (
            self.remaining_reads is not None
            and self.remaining_reads < self._min_remaining_reads
        ):
            raise AzureThrottledError(
                LOW_READS_PAUSE_SECONDS,
                f"ARM read bucket low ({self.remaining_reads} left)",
                code="low_remaining_reads",
            )

    def _note_headers(self, response: httpx.Response) -> None:
        reads = response.headers.get(REMAINING_READS)
        if reads and reads.strip().isdigit():
            self.remaining_reads = int(reads)
        if QPU_CONSUMED in response.headers or QPU_REMAINING in response.headers:
            logger.info(
                "azure_cost_qpu",
                consumed=response.headers.get(QPU_CONSUMED),
                remaining=response.headers.get(QPU_REMAINING),
            )

    async def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        """Send with retries; returns a 2xx response or raises an Azure error."""
        url = self._url(path_or_url)
        refreshed = False
        attempt = 0
        while True:
            self._check_reads_floor()
            if self._budget is not None:
                await self._budget()
            self.calls += 1
            try:
                response = await self._http.request(
                    method, url, params=params, json=json, headers=await self._headers()
                )
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise AzureApiError(
                        "connection_error", type(exc).__name__
                    ) from None
                await self._sleep(backoff_delay(attempt, rng=self._rng))
                attempt += 1
                continue
            self._note_headers(response)
            status = response.status_code
            if response.is_success:
                return response
            if (
                status == httpx.codes.UNAUTHORIZED
                and self._token_source is not None
                and not refreshed
            ):
                # Expired/revoked token: fetch a new one once.
                self._token_source.invalidate()
                refreshed = True
                continue
            if status == httpx.codes.TOO_MANY_REQUESTS:
                wait = parse_retry_after(response.headers)
                wait = DEFAULT_THROTTLE_SECONDS if wait is None else wait
                code, message = error_from_response(response)
                if wait > self._inline_wait_max or attempt >= self._max_retries:
                    raise AzureThrottledError(wait, message, code=code)
                await self._sleep(wait)
                attempt += 1
                continue
            if (
                status >= httpx.codes.INTERNAL_SERVER_ERROR
                and attempt < self._max_retries
            ):
                wait = parse_retry_after(response.headers)
                if wait is None or wait > self._inline_wait_max:
                    wait = backoff_delay(attempt, rng=self._rng)
                await self._sleep(wait)
                attempt += 1
                continue
            code, message = error_from_response(response)
            logger.warning("azure_request_failed", status=status, code=sanitize(code))
            raise AzureApiError(code, message, status_code=status)

    async def get_json(
        self, path_or_url: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = await self.request("GET", path_or_url, params=params)
        return _json_object(response)

    async def post_json(
        self, path_or_url: str, body: Any, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = await self.request("POST", path_or_url, params=params, json=body)
        return _json_object(response)

    async def paged(
        self,
        path_or_url: str,
        params: dict[str, Any] | None = None,
        *,
        items_key: str = "value",
        max_pages: int = MAX_PAGES,
    ) -> AsyncIterator[dict[str, Any]]:
        """Every item across ``nextLink`` / ``NextPageLink`` pages (at most
        ``max_pages``; the next link already carries the query)."""
        url: str | None = path_or_url
        page_params = params
        pages = 0
        while url and pages < max_pages:
            page = await self.get_json(url, page_params)
            pages += 1
            for item in page.get(items_key) or []:
                if isinstance(item, dict):
                    yield item
            url = next((page[k] for k in NEXT_LINK_KEYS if page.get(k)), None)
            page_params = None
        if url:
            logger.warning("azure_pagination_capped", pages=pages)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        raise AzureApiError("invalid_json", "Azure returned a non-JSON body") from None
    if not isinstance(data, dict):
        raise AzureApiError("invalid_json", "Azure returned a non-object body")
    return data
