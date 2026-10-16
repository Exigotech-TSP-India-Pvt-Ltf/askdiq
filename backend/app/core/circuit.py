"""Circuit breaker for outbound dependencies (OWASP A10, fail closed).

The breaker opens after ``failure_threshold`` transient failures inside a
rolling ``window_seconds``. While open, calls fail fast with
``CircuitOpenError`` (callers turn this into their existing graceful path,
e.g. the ``llm_failed`` outcome). After ``reset_timeout_seconds`` a single
half-open probe is let through: success closes the breaker, failure re-opens
it.

State changes happen synchronously between awaits, so no lock is needed on
the single-threaded event loop.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from enum import StrEnum
from functools import lru_cache
from http import HTTPStatus

import httpx
import openai

from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

LLM_BREAKER_NAME = "azure_openai_chat"


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised instead of calling the dependency while the breaker is open."""

    def __init__(self, name: str, retry_after: float) -> None:
        super().__init__(f"circuit '{name}' is open")
        self.name = name
        self.retry_after = retry_after


def is_transient_failure(exc: BaseException) -> bool:
    """True for errors that signal an unhealthy dependency.

    Timeouts, connection errors, throttling (429) and 5xx count; client
    errors such as 400 (bad request, content filter) do not.
    """
    if isinstance(
        exc,
        TimeoutError | httpx.TimeoutException | httpx.TransportError,
    ):
        return True
    if isinstance(exc, openai.APIConnectionError):  # includes APITimeoutError
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (
        status == HTTPStatus.TOO_MANY_REQUESTS
        or status >= HTTPStatus.INTERNAL_SERVER_ERROR
    )


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int,
        window_seconds: float,
        reset_timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.window_seconds = window_seconds
        self.reset_timeout_seconds = reset_timeout_seconds
        self._clock = clock
        self._failures: deque[float] = deque()
        self._state = CircuitState.CLOSED
        self._opened_at = 0.0
        self._probe_in_flight = False

    @property
    def state(self) -> CircuitState:
        if (
            self._state is CircuitState.OPEN
            and self._clock() - self._opened_at >= self.reset_timeout_seconds
        ):
            self._state = CircuitState.HALF_OPEN
            self._probe_in_flight = False
            logger.info("circuit_half_open", circuit=self.name)
        return self._state

    def _acquire(self) -> None:
        state = self.state
        if state is CircuitState.CLOSED:
            return
        if state is CircuitState.HALF_OPEN and not self._probe_in_flight:
            self._probe_in_flight = True
            return
        retry_after = max(
            0.0, self.reset_timeout_seconds - (self._clock() - self._opened_at)
        )
        raise CircuitOpenError(self.name, retry_after)

    def record_success(self) -> None:
        if self._state is not CircuitState.CLOSED:
            logger.info("circuit_closed", circuit=self.name)
        self._state = CircuitState.CLOSED
        self._failures.clear()
        self._probe_in_flight = False

    def record_failure(self) -> None:
        now = self._clock()
        if self._state is CircuitState.HALF_OPEN:
            self._open(now)
            return
        self._failures.append(now)
        while self._failures and now - self._failures[0] > self.window_seconds:
            self._failures.popleft()
        if len(self._failures) >= self.failure_threshold:
            self._open(now)

    def _open(self, now: float) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = now
        self._probe_in_flight = False
        self._failures.clear()
        logger.warning(
            "circuit_opened",
            circuit=self.name,
            reset_after_s=self.reset_timeout_seconds,
        )

    def reset(self) -> None:
        self._state = CircuitState.CLOSED
        self._failures.clear()
        self._probe_in_flight = False

    @asynccontextmanager
    async def guard(
        self,
        is_failure: Callable[[BaseException], bool] = is_transient_failure,
    ) -> AsyncIterator[None]:
        """Run the wrapped block through the breaker.

        Raises ``CircuitOpenError`` without running the block when open.
        """
        self._acquire()
        try:
            yield
        except Exception as exc:
            if is_failure(exc):
                self.record_failure()
            else:
                # The dependency answered (e.g. a 400), so it is reachable.
                self.record_success()
            raise
        except BaseException:
            # Cancellation: no verdict on the dependency; free the probe slot.
            self._probe_in_flight = False
            raise
        self.record_success()


@lru_cache
def get_llm_breaker() -> CircuitBreaker:
    """Process-wide breaker shared by every chat-completion call."""
    settings = get_settings()
    return CircuitBreaker(
        LLM_BREAKER_NAME,
        failure_threshold=settings.llm_breaker_failure_threshold,
        window_seconds=settings.llm_breaker_window_seconds,
        reset_timeout_seconds=settings.llm_breaker_reset_seconds,
    )
