"""In-memory sliding-window rate limiter for the /query endpoint.

Two independent limits are enforced:
  - per user (JWT actor/email) — the original limit.
  - per client IP — a looser, separate cap so abuse spread across multiple
    accounts from the same source still gets throttled.

Kept in-process (not DB-backed) on purpose: with ~20 users behind a single
uvicorn worker (see backend/Dockerfile — no --workers flag), this adds zero
extra DB round-trips to the request path. That matters because /query is
already slow (a real LLM call), and holding a DB connection an extra beat
per request to check a counter is exactly the kind of overhead that would
make concurrent users queue behind each other.

Caveat: these limits are per-process, so the app must run a single uvicorn
worker (Dockerfile.prod does) for them to be global.
"""

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.request_meta import get_client_ip
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
    global _calls_since_sweep
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
        logger.warning(
            "rate_limit_exceeded",
            key=key,
            limit=limit,
            label=label,
            retry_after_s=retry_after,
            window_count=len(log),
        )
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded ({label}): max {limit} questions per "
                f"minute. Try again in {retry_after}s."
            ),
            headers={"Retry-After": str(retry_after)},
        )

    log.append(now)


def enforce_rate_limit(actor: str) -> None:
    """Raise HTTP 429 if `actor` has exceeded the per-user per-minute budget."""
    _check_and_record(
        _user_request_log, actor, settings.rate_limit_per_minute, "per user"
    )


def enforce_ip_rate_limit(ip: str) -> None:
    """Raise HTTP 429 if `ip` has exceeded the per-IP per-minute budget."""
    _check_and_record(
        _ip_request_log, ip, settings.rate_limit_per_minute_per_ip, "per IP"
    )


async def rate_limited_actor(
    request: Request, actor: str = Depends(get_current_actor)
) -> str:
    """Drop-in replacement for `get_current_actor` that also enforces the
    per-user AND per-IP rate limits — use as the auth dependency on
    throttled routes."""
    enforce_rate_limit(actor)
    enforce_ip_rate_limit(get_client_ip(request))
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


async def rate_limited_admin_analytics(
    request: Request, actor: str = Depends(get_current_actor)
) -> str:
    """FastAPI dependency enforcing rate limits on heavy admin aggregation/export routes (OWASP A04/A07)."""
    ip = get_client_ip(request)
    enforce_admin_heavy_rate_limit(actor, ip)
    return actor
