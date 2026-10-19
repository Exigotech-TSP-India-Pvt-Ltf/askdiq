"""Audience Scope Engine (Task 2.1).

Provides:
- Audience literal type ('overview' | 'users' | 'testing')
- FastAPI dependency for query parameter validation (422 on invalid)
- Dynamic distinct-IP classification against test IP rules (CIDR, loopback, prefixes)
- In-process short-TTL caching with single-flight stampede protection
- SQL filter clause generator (handling arrays and NULL/unknown IPs)
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException, Query
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import dashboard_cache
from app.core.config import get_settings
from app.dashboard.snapshots import schedule_drop_snapshots
from app.governance.test_ips import get_effective_test_ips, is_test_ip
from app.models.db_models import AuditLog, ChatMessageRecord, ChatSession

logger = logging.getLogger(__name__)

Audience = Literal["overview", "users", "testing"]
VALID_AUDIENCES: set[str] = {"overview", "users", "testing"}

# In-process cache: (test_ips_set, users_ips_set, timestamp)
_cache_lock = asyncio.Lock()
_cached_classification: dict[str, Any] = {
    "test_ips": set(),
    "user_ips": set(),
    "as_of": None,
}


def audience_scope(audience: str = Query("overview")) -> Audience:
    """FastAPI query dependency validating ?audience= overview|users|testing."""
    aud = audience.strip().lower()
    if aud not in VALID_AUDIENCES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid audience '{audience}'. Must be one of: overview, users, testing.",
        )
    return aud  # type: ignore[return-value]


def invalidate_audience_cache() -> None:
    """Invalidate the in-process IP classification cache on test-IP configuration changes."""
    global _cached_classification  # noqa: PLW0603 - process-local TTL cache
    _cached_classification = {
        "test_ips": set(),
        "user_ips": set(),
        "as_of": None,
    }
    from app.governance.test_ips import invalidate_test_ips_cache  # noqa: PLC0415 - audience<->test_ips import cycle

    invalidate_test_ips_cache()
    # Every audience-scoped dashboard aggregate depends on the classification.
    dashboard_cache.clear()
    schedule_drop_snapshots()
    try:
        from app.api.routes.admin_usage import clear_usage_caches  # noqa: PLC0415 - governance must not import routes at load time

        clear_usage_caches()
    except Exception:
        logger.warning("usage_cache_clear_failed", exc_info=True)
    logger.info("Audience cache and usage summary caches explicitly invalidated.")


async def get_classified_ips(db: AsyncSession) -> tuple[set[str], set[str], datetime]:
    """Return (test_ips, user_ips, as_of_dt) with short-TTL cache and single-flight lock."""
    global _cached_classification  # noqa: PLW0603 - process-local TTL cache
    settings = get_settings()
    ttl = settings.audience_cache_ttl_seconds
    now = datetime.now(UTC)

    # Check cache freshness
    cached_time = _cached_classification.get("as_of")
    if (
        cached_time is not None
        and (now - cached_time).total_seconds() < ttl
        and _cached_classification.get("test_ips") is not None
    ):
        return (
            _cached_classification["test_ips"],
            _cached_classification["user_ips"],
            cached_time,
        )

    # Single-flight refresh
    async with _cache_lock:
        # Re-check inside lock
        cached_time = _cached_classification.get("as_of")
        if (
            cached_time is not None
            and (now - cached_time).total_seconds() < ttl
            and _cached_classification.get("test_ips") is not None
        ):
            return (
                _cached_classification["test_ips"],
                _cached_classification["user_ips"],
                cached_time,
            )

        effective_rules = await get_effective_test_ips(db)

        # Distinct IPs across all tables via UNION query
        q_msgs = select(ChatMessageRecord.ip_address).where(
            ChatMessageRecord.ip_address.isnot(None)
        )
        q_sess = select(ChatSession.created_ip).where(
            ChatSession.created_ip.isnot(None)
        )
        q_aud = select(AuditLog.ip_address).where(AuditLog.ip_address.isnot(None))

        union_q = q_msgs.union(q_sess, q_aud)
        raw_ips = (await db.execute(union_q)).scalars().all()

        test_ips: set[str] = set()
        user_ips: set[str] = set()

        for raw_ip in raw_ips:
            if not raw_ip:
                continue
            ip_str = str(raw_ip).strip()
            if is_test_ip(ip_str, effective_rules):
                test_ips.add(ip_str)
            else:
                user_ips.add(ip_str)

        _cached_classification = {
            "test_ips": test_ips,
            "user_ips": user_ips,
            "as_of": now,
        }
        return test_ips, user_ips, now


async def build_audience_filter(
    db: AsyncSession,
    col: Any,
    audience: Audience,
) -> ColumnElement[bool] | None:
    """Build a SQL filter condition for a given column (e.g. ChatMessageRecord.ip_address).

    Rules:
    - overview -> None (matches all data)
    - testing  -> col is NULL OR col IN (:test_ips)
    - users    -> col is NOT NULL AND col NOT IN (:test_ips)
    """
    if audience == "overview":
        return None

    test_ips, _, _ = await get_classified_ips(db)
    test_ip_list = list(test_ips)

    if audience == "testing":
        if test_ip_list:
            return or_(col.is_(None), col.in_(test_ip_list))
        return col.is_(None)

    # audience == "users"
    if test_ip_list:
        return and_(col.isnot(None), col.not_in(test_ip_list))
    return col.isnot(None)
