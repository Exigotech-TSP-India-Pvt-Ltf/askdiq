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

Caveat: these limits are per-process. If the app is ever run with multiple
worker processes/replicas, they become per-worker, not truly global — fine
for the current single-worker deployment, but worth flagging before
scaling out.
"""

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_settings
from app.core.request_meta import get_client_ip
from app.governance.auth import get_current_actor

settings = get_settings()

_WINDOW_SECONDS = 60.0

# key (actor email, or IP) -> monotonic timestamps of requests in the window.
_user_request_log: dict[str, deque[float]] = defaultdict(deque)
_ip_request_log: dict[str, deque[float]] = defaultdict(deque)


def _check_and_record(
    log_store: dict[str, deque[float]], key: str, limit: int, label: str
) -> None:
    now = time.monotonic()
    log = log_store[key]

    while log and now - log[0] > _WINDOW_SECONDS:
        log.popleft()

    if len(log) >= limit:
        retry_after = int(_WINDOW_SECONDS - (now - log[0])) + 1
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
