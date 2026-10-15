"""Sliding-window rate limiter for /query, feedback and heavy admin routes.

Two independent limits are enforced:
  - per user (JWT actor/email) — the original limit.
  - per client IP — a looser, separate cap so abuse spread across multiple
    accounts from the same source still gets throttled.

Request paths use the async ``aenforce_*`` functions and the FastAPI
dependencies, which go through the configured shared-state backend
(``app.core.state``): Postgres buckets by default, so every uvicorn worker and
replica shares one limit (one ~1 ms upsert per check). With the in-memory
backend (``ENV=test`` / ``STATE_BACKEND=memory``) they fall back to the
in-process sliding window below, which the sync ``enforce_*`` functions keep
using directly.
"""

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.request_meta import get_client_ip
from app.core.state import MemoryState, get_state_backend, sub_window_seconds
from app.governance.auth import get_current_actor

logger = get_logger(__name__)
settings = get_settings()

_WINDOW_SECONDS = 60.0
_SWEEP_EVERY = 500

# key (actor email, or IP) -> monotonic timestamps of requests in the window.
_user_request_log: dict[str, deque[float]] = defaultdict(deque)
_ip_request_log: dict[str, deque[float]] = defaultdict(deque)
_feedback_user_log: dict[str, deque[float]] = defaultdict(deque)
_feedback_ip_log: dict[str, deque[float]] = defaultdict(deque)
_admin_user_log: dict[str, deque[float]] = defaultdict(deque)
_admin_ip_log: dict[str, deque[float]] = defaultdict(deque)
_calls_since_sweep = 0


def _sweep(now: float) -> None:
    """Drop keys with no request in the window so rotating client ids can't grow memory."""
    for store in (
        _user_request_log,
        _ip_request_log,
        _feedback_user_log,
        _feedback_ip_log,
        _admin_user_log,
        _admin_ip_log,
    ):
        idle = [
            k for k, log in store.items() if not log or now - log[-1] > _WINDOW_SECONDS
        ]
        for key in idle:
            del store[key]


def _check_and_record(
    log_store: dict[str, deque[float]], key: str, limit: int, label: str
) -> None:
    global _calls_since_sweep  # noqa: PLW0603 - process-wide sweep counter
    now = time.monotonic()

    _calls_since_sweep += 1
    if _calls_since_sweep >= _SWEEP_EVERY:
        _calls_since_sweep = 0
        _sweep(now)

    log = log_store[key]

    while log and now - log[0] > _WINDOW_SECONDS:
        log.popleft()

    if len(log) >= limit:
        retry_after = int(_WINDOW_SECONDS - (now - log[0])) + 1
        _reject(key, limit, label, retry_after, len(log))

    log.append(now)


def _reject(key: str, limit: int, label: str, retry_after: int, count: int) -> None:
    logger.warning(
        "rate_limit_exceeded",
        key=key,
        limit=limit,
        label=label,
        retry_after_s=retry_after,
        window_count=count,
    )
    raise HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            f"Rate limit exceeded ({label}): max {limit} questions per "
            f"minute. Try again in {retry_after}s."
        ),
        headers={"Retry-After": str(retry_after)},
    )


async def _enforce(
    log_store: dict[str, deque[float]], key: str, limit: int, label: str
) -> None:
    """Check one limit through the shared-state backend."""
    backend = get_state_backend()
    if isinstance(backend, MemoryState):
        _check_and_record(log_store, key, limit, label)
        return
    window = int(_WINDOW_SECONDS)
    count = await backend.incr_window(f"rl:{label}:{key}", window)
    if count > limit:
        _reject(key, limit, label, sub_window_seconds(window), count)


def _is_exempt(actor: str) -> bool:
    return actor in settings.rate_limit_exempt_actors


def enforce_rate_limit(actor: str) -> None:
    """Raise HTTP 429 if `actor` has exceeded the per-user per-minute budget."""
    if _is_exempt(actor):
        return
    _check_and_record(
        _user_request_log, actor, settings.rate_limit_per_minute, "per user"
    )


def enforce_ip_rate_limit(ip: str) -> None:
    """Raise HTTP 429 if `ip` has exceeded the per-IP per-minute budget."""
    _check_and_record(
        _ip_request_log, ip, settings.rate_limit_per_minute_per_ip, "per IP"
    )


async def aenforce_rate_limit(actor: str) -> None:
    """Shared-state (multi-worker) variant of `enforce_rate_limit`."""
    if _is_exempt(actor):
        return
    await _enforce(_user_request_log, actor, settings.rate_limit_per_minute, "per user")


async def aenforce_ip_rate_limit(ip: str) -> None:
    """Shared-state (multi-worker) variant of `enforce_ip_rate_limit`."""
    await _enforce(_ip_request_log, ip, settings.rate_limit_per_minute_per_ip, "per IP")


async def rate_limited_actor(
    request: Request, actor: str = Depends(get_current_actor)
) -> str:
    """Drop-in replacement for `get_current_actor` that also enforces the
    per-user AND per-IP rate limits — use as the auth dependency on
    throttled routes."""
    await aenforce_rate_limit(actor)
    await aenforce_ip_rate_limit(get_client_ip(request))
    return actor


def enforce_feedback_rate_limit(
    actor: str, ip: str | None = None, limit: int = 30
) -> None:
    """Enforce Subtask 1.3.2 rate limit (default 30/min per actor and per IP)."""
    _check_and_record(_feedback_user_log, actor, limit, "feedback per user")
    if ip:
        _check_and_record(_feedback_ip_log, ip, limit, "feedback per IP")


def enforce_admin_heavy_rate_limit(
    actor: str, ip: str | None = None, limit: int = 60
) -> None:
    """Enforce sliding-window rate limit for heavy admin analytics/aggregations (default 60/min per actor and per IP)."""
    _check_and_record(_admin_user_log, actor, limit, "admin analytics per user")
    if ip:
        _check_and_record(_admin_ip_log, ip, limit, "admin analytics per IP")


async def aenforce_feedback_rate_limit(
    actor: str, ip: str | None = None, limit: int = 30
) -> None:
    """Shared-state (multi-worker) variant of `enforce_feedback_rate_limit`."""
    await _enforce(_feedback_user_log, actor, limit, "feedback per user")
    if ip:
        await _enforce(_feedback_ip_log, ip, limit, "feedback per IP")


async def aenforce_admin_heavy_rate_limit(
    actor: str, ip: str | None = None, limit: int = 60
) -> None:
    """Shared-state (multi-worker) variant of `enforce_admin_heavy_rate_limit`."""
    await _enforce(_admin_user_log, actor, limit, "admin analytics per user")
    if ip:
        await _enforce(_admin_ip_log, ip, limit, "admin analytics per IP")


async def rate_limited_admin_analytics(
    request: Request, actor: str = Depends(get_current_actor)
) -> str:
    """FastAPI dependency enforcing rate limits on heavy admin aggregation/export routes (OWASP A04/A07)."""
    ip = get_client_ip(request)
    await aenforce_admin_heavy_rate_limit(actor, ip)
    return actor
