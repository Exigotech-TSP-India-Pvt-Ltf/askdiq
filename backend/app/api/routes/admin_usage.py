"""SQL Usage & Cost Aggregation API (Phase 2 & Phase 3).

High-performance, database-aggregated endpoints for:
- Audience counts badge: GET /admin/usage/audience-counts (Task 2.4)
- Summary: GET /admin/usage/summary (Task 3.2, separated cost & tokens)
- Users: GET /admin/usage/users (Task 3.3.1, paginated + intelligence header)
- Sessions: GET /admin/usage/sessions (Task 3.3.2, paginated + intelligence header)
- Queries: GET /admin/usage/queries (Task 3.3.3, paginated + intelligence header)
- Query Inspector Detail: GET /admin/usage/queries/{request_id} (Task 3.5.3)
- Streaming CSV Export: GET /admin/usage/export (Task 3.4, formula injection protected)
"""

import asyncio
import csv
import io
import logging
import time
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import (
    String,
    and_,
    asc,
    case,
    cast,
    desc,
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.cache import aggregation_cache
from app.core.concurrency import ScopedRunner, get_scoped_runner, single_flight
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.http_cache import cached_response
from app.core.serialization import sanitize_csv_cell
from app.dashboard import usage_series
from app.dashboard.queries import cached_tokens_sql
from app.dashboard.window import utcnow
from app.governance.audience import (
    Audience,
    audience_scope,
    build_audience_filter,
    get_classified_ips,
)
from app.governance.audit import record_audit_event
from app.governance.outcomes import GENERATION_OUTCOMES
from app.governance.usage_queries import (
    apply_usage_filters,
    escape_ilike_pattern,
)
from app.model_settings.service import current_model_settings
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    LatencyRecord,
)

# A single IP above this many queries likely fronts a shared gateway/NAT.
_SHARED_GATEWAY_QUERY_THRESHOLD = 50

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/usage", tags=["usage"])

__all__ = ["router"]


def billed_rate_card(details: dict[str, Any]) -> dict[str, float]:
    """Rates an exchange was billed at (its pricing snapshot), else today's rates."""
    snapshot = details.get("pricing")
    billed = snapshot if isinstance(snapshot, dict) else {}
    live = current_model_settings()

    def rate(key: str, fallback: float) -> float:
        value = billed.get(key)
        return float(value) if isinstance(value, int | float) else fallback

    return {
        "price_embed_per_million": rate("embed_per_m", live.price_embed_per_million),
        "price_input_per_million": rate("prompt_per_m", live.price_prompt_per_million),
        "price_output_per_million": rate("output_per_m", live.price_output_per_million),
        "price_cached_per_million": rate("cached_per_m", live.price_cached_per_million),
    }


# Concurrency semaphore for heavy aggregations (Task 5.1.2)
_agg_semaphore = asyncio.Semaphore(max(1, get_settings().usage_agg_max_concurrency))
_summary_cache: dict[tuple, tuple[float, dict]] = {}
# Summary payloads are the same for every caller holding `dashboard:read`
# (checked before the cache); the scope is part of the key all the same.
_SUMMARY_CACHE_SCOPE = Permission.DASHBOARD_READ.value
_counts_cache: dict[str, tuple[float, dict]] = {}
_counts_cache_lock = asyncio.Lock()


# Aggregation-cache prefix of the Monitoring usage cards (/admin/usage/llm ...).
USAGE_CARD_CACHE_PREFIX = "usage-card:"


def clear_usage_caches() -> None:
    """Clear all usage summary, audience counts and usage card caches (Task 5.1.1)."""
    _summary_cache.clear()
    _counts_cache.clear()
    aggregation_cache.invalidate_prefix(USAGE_CARD_CACHE_PREFIX)


# ============================================================================
# AUDIENCE COUNTS (Task 2.4 & Task 5.1)
# ============================================================================


@router.get("/audience-counts")
async def get_audience_counts(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    """Return badge counts (queries, sessions, distinct IPs) for Overview, Users, and Testing with short-TTL cache."""
    settings = get_settings()
    ttl = settings.usage_summary_cache_ttl_seconds
    now_ts = time.time()
    if "counts" in _counts_cache:
        cached_ts, cached_val = _counts_cache["counts"]
        if now_ts - cached_ts < ttl:
            return cached_val

    async with _counts_cache_lock:
        now_ts = time.time()
        if "counts" in _counts_cache:
            cached_ts, cached_val = _counts_cache["counts"]
            if now_ts - cached_ts < ttl:
                return cached_val

        test_ips, _user_ips, _ = await get_classified_ips(db)
        test_ip_list = list(test_ips)

        user_msg = aliased(ChatMessageRecord, name="user_turn")
        asst_msg = aliased(ChatMessageRecord, name="asst_turn")
        client_ip_col = func.coalesce(user_msg.ip_address, ChatSession.created_ip)

        # 1. Total counts (Overview)
        q_total = (
            await db.execute(
                select(func.count(asst_msg.id))
                .select_from(asst_msg)
                .join(
                    user_msg,
                    and_(
                        asst_msg.session_id == user_msg.session_id,
                        asst_msg.turn_index == user_msg.turn_index,
                        user_msg.role == "user",
                    ),
                )
                .where(asst_msg.role == "assistant")
            )
        ).scalar() or 0

        s_total = (await db.execute(select(func.count(ChatSession.id)))).scalar() or 0

        # Distinct IPs across sessions and messages
        q_all_ips = (
            select(ChatMessageRecord.ip_address)
            .where(ChatMessageRecord.ip_address.isnot(None))
            .union(
                select(ChatSession.created_ip).where(ChatSession.created_ip.isnot(None))
            )
        )
        all_distinct_ips = set((await db.execute(q_all_ips)).scalars().all())

        # 2. Testing counts (IP is NULL/empty OR in test_ip_list)
        if test_ip_list:
            test_cond_msgs = or_(
                client_ip_col.is_(None),
                client_ip_col.in_(test_ip_list),
            )
            test_cond_sess = or_(
                ChatSession.created_ip.is_(None),
                ChatSession.created_ip.in_(test_ip_list),
            )
        else:
            test_cond_msgs = client_ip_col.is_(None)
            test_cond_sess = ChatSession.created_ip.is_(None)

        q_testing = (
            await db.execute(
                select(func.count(asst_msg.id))
                .select_from(asst_msg)
                .join(
                    user_msg,
                    and_(
                        asst_msg.session_id == user_msg.session_id,
                        asst_msg.turn_index == user_msg.turn_index,
                        user_msg.role == "user",
                    ),
                )
                .join(ChatSession, asst_msg.session_id == ChatSession.id)
                .where(
                    asst_msg.role == "assistant",
                    test_cond_msgs,
                )
            )
        ).scalar() or 0

        s_testing = (
            await db.execute(select(func.count(ChatSession.id)).where(test_cond_sess))
        ).scalar() or 0

        # 3. User counts: Overview minus Testing guarantees exact reconciliation
        q_users = max(0, q_total - q_testing)
        s_users = max(0, s_total - s_testing)

        distinct_test_count = sum(1 for ip in all_distinct_ips if ip in test_ips)
        distinct_user_count = max(0, len(all_distinct_ips) - distinct_test_count)

        result = {
            "overview": {
                "queries": q_total,
                "sessions": s_total,
                "ips": len(all_distinct_ips),
            },
            "users": {
                "queries": q_users,
                "sessions": s_users,
                "ips": distinct_user_count,
            },
            "testing": {
                "queries": q_testing,
                "sessions": s_testing,
                "ips": distinct_test_count,
            },
        }
        _counts_cache["counts"] = (time.time(), result)
        return result


# ============================================================================
# USAGE SUMMARY (Task 3.2)
# ============================================================================


@router.get("/summary")
async def get_usage_summary(
    request: Request,
    audience: Audience = Depends(audience_scope),
    from_date: str | None = None,
    to_date: str | None = None,
    runner: ScopedRunner = Depends(get_scoped_runner),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> Response:
    """Overall Stage Economics summary separating Financial Cost from Token Volume (Task 3.2).

    Sends a weak ETag and `Cache-Control: private`; a matching
    `If-None-Match` gets `304 Not Modified`.
    """
    payload = await _usage_summary_payload(audience, from_date, to_date, runner)
    return cached_response(request, payload)


async def _usage_summary_payload(
    audience: Audience,
    from_date: str | None,
    to_date: str | None,
    runner: ScopedRunner,
) -> dict:
    settings = get_settings()

    # Parse and validate dates
    from_dt: datetime | None = None
    to_dt: datetime | None = None
    if from_date:
        try:
            from_dt = datetime.fromisoformat(from_date.replace("Z", ""))
        except ValueError:
            raise HTTPException(
                status_code=422, detail="Invalid from_date format. Use ISO 8601."
            ) from None
    if to_date:
        try:
            to_dt = datetime.fromisoformat(to_date.replace("Z", ""))
        except ValueError:
            raise HTTPException(
                status_code=422, detail="Invalid to_date format. Use ISO 8601."
            ) from None

    if from_dt and to_dt and (to_dt - from_dt).days > settings.usage_max_range_days:
        raise HTTPException(
            status_code=422,
            detail=f"Requested date range exceeds maximum allowed limit of {settings.usage_max_range_days} days.",
        )

    cache_key = (_SUMMARY_CACHE_SCOPE, audience, from_date, to_date)
    ttl = settings.usage_summary_cache_ttl_seconds
    cached = _fresh_summary(cache_key, ttl)
    if cached is not None:
        return cached

    async def compute() -> dict:
        again = _fresh_summary(cache_key, ttl)
        if again is not None:
            return again
        async with _agg_semaphore:
            payload = await _compute_usage_summary(
                runner, audience, from_dt, to_dt, settings
            )
        _summary_cache[cache_key] = (time.time(), payload)
        return payload

    # Identical concurrent requests share one computation; different
    # audiences / ranges no longer queue behind one global lock.
    return await single_flight.run(f"usage-summary:{cache_key!r}", compute)


def _fresh_summary(cache_key: tuple, ttl: float) -> dict | None:
    entry = _summary_cache.get(cache_key)
    if entry is not None and time.time() - entry[0] < ttl:
        return entry[1]
    return None


def _turn_aliases() -> tuple[Any, Any]:
    return (
        aliased(ChatMessageRecord, name="user_turn"),
        aliased(ChatMessageRecord, name="asst_turn"),
    )


def _join_user_turn(stmt: Any, asst_msg: Any, user_msg: Any) -> Any:
    return stmt.join(
        user_msg,
        and_(
            asst_msg.session_id == user_msg.session_id,
            asst_msg.turn_index == user_msg.turn_index,
            user_msg.role == "user",
        ),
    ).join(ChatSession, asst_msg.session_id == ChatSession.id)


async def _scope_turns(
    db: AsyncSession,
    stmt: Any,
    user_msg: Any,
    audience: Audience,
    from_dt: datetime | None,
    to_dt: datetime | None,
) -> Any:
    """Audience and user-turn time filters shared by every summary query."""
    client_ip_col = func.coalesce(user_msg.ip_address, ChatSession.created_ip)
    aud_cond = await build_audience_filter(db, client_ip_col, audience)
    if aud_cond is not None:
        stmt = stmt.where(aud_cond)
    if from_dt:
        stmt = stmt.where(user_msg.created_at >= from_dt)
    if to_dt:
        stmt = stmt.where(user_msg.created_at <= to_dt)
    return stmt


async def _summary_totals(
    db: AsyncSession,
    audience: Audience,
    from_dt: datetime | None,
    to_dt: datetime | None,
) -> Any:
    """Token, cost and accounting sums over every scoped exchange (one row)."""
    user_msg, asst_msg = _turn_aliases()
    stmt = select(
        func.count(asst_msg.id).label("total_turns"),
        func.coalesce(func.sum(asst_msg.prompt_tokens), 0).label("sum_prompt"),
        func.coalesce(func.sum(asst_msg.completion_tokens), 0).label("sum_completion"),
        func.coalesce(func.sum(asst_msg.total_tokens), 0).label("sum_total_tokens"),
        func.coalesce(func.sum(asst_msg.reasoning_tokens), 0).label("sum_reasoning"),
        func.coalesce(func.sum(asst_msg.embedding_tokens), 0).label("sum_query_embed"),
        func.coalesce(func.sum(asst_msg.guardrail_tokens), 0).label(
            "sum_guardrail_embed"
        ),
        func.coalesce(func.sum(asst_msg.cost_usd), 0.0).label("sum_cost"),
        func.coalesce(func.sum(cached_tokens_sql(asst_msg)), 0.0).label("sum_cached"),
        # Count actual vs estimated turns (safely handle JSON boolean / string)
        func.count(
            case(
                (asst_msg.token_details["is_actual"].as_string() == "false", 1),
                else_=None,
            )
        ).label("estimated_turns"),
    ).select_from(asst_msg)
    stmt = _join_user_turn(stmt, asst_msg, user_msg).where(asst_msg.role == "assistant")
    stmt = await _scope_turns(db, stmt, user_msg, audience, from_dt, to_dt)
    return (await db.execute(stmt)).one()


async def _ingest_overhead(
    db: AsyncSession,
    audience: Audience,
    from_dt: datetime | None,
    to_dt: datetime | None,
) -> tuple[int, float]:
    """Ingestion embedding tokens and cost from AuditLog (Task 3.2.3)."""
    stmt = select(AuditLog.detail).where(AuditLog.action == "ingest")
    aud_cond = await build_audience_filter(db, AuditLog.ip_address, audience)
    if aud_cond is not None:
        stmt = stmt.where(aud_cond)
    if from_dt:
        stmt = stmt.where(AuditLog.created_at >= from_dt)
    if to_dt:
        stmt = stmt.where(AuditLog.created_at <= to_dt)
    tokens = 0
    cost = 0.0
    for detail in (await db.execute(stmt)).scalars().all():
        if isinstance(detail, dict):
            tokens += int(detail.get("embedding_tokens") or 0)
            cost += float(detail.get("cost_usd") or 0.0)
    return tokens, cost


async def _outcome_counts(
    db: AsyncSession,
    audience: Audience,
    from_dt: datetime | None,
    to_dt: datetime | None,
) -> dict:
    user_msg, asst_msg = _turn_aliases()
    stmt = select(asst_msg.outcome, func.count(asst_msg.id)).select_from(asst_msg)
    stmt = _join_user_turn(stmt, asst_msg, user_msg).where(asst_msg.role == "assistant")
    stmt = await _scope_turns(db, stmt, user_msg, audience, from_dt, to_dt)
    return dict((await db.execute(stmt.group_by(asst_msg.outcome))).all())


async def _latency_stats(
    db: AsyncSession,
    audience: Audience,
    from_dt: datetime | None,
    to_dt: datetime | None,
) -> Any:
    user_msg, asst_msg = _turn_aliases()
    stmt = (
        select(
            func.coalesce(func.avg(LatencyRecord.total_ms), 0.0).label("avg_lat"),
            func.coalesce(
                func.percentile_cont(0.50).within_group(LatencyRecord.total_ms), 0.0
            ).label("p50_lat"),
            func.coalesce(
                func.percentile_cont(0.95).within_group(LatencyRecord.total_ms), 0.0
            ).label("p95_lat"),
        )
        .select_from(asst_msg)
        .join(LatencyRecord, asst_msg.request_id == LatencyRecord.request_id)
    )
    stmt = _join_user_turn(stmt, asst_msg, user_msg)
    stmt = await _scope_turns(db, stmt, user_msg, audience, from_dt, to_dt)
    return (await db.execute(stmt)).one()


async def _compute_usage_summary(
    runner: ScopedRunner,
    audience: Audience,
    from_dt: datetime | None,
    to_dt: datetime | None,
    settings: Settings,
) -> dict:
    """Independent aggregates run in parallel, one short-lived session each."""
    now = utcnow()
    (
        row,
        (ingest_embed_tokens, ingest_cost),
        outcome_rows,
        lat_row,
        stage_series,
        forecast,
    ) = await runner.gather(
        lambda s: _summary_totals(s, audience, from_dt, to_dt),
        lambda s: _ingest_overhead(s, audience, from_dt, to_dt),
        lambda s: _outcome_counts(s, audience, from_dt, to_dt),
        lambda s: _latency_stats(s, audience, from_dt, to_dt),
        lambda s: usage_series.cost_by_stage_series(
            s, audience, from_dt, to_dt, now, settings
        ),
        lambda s: usage_series.month_cost_forecast(s, audience, now, settings),
    )
    total_turns = row.total_turns
    sum_prompt = int(row.sum_prompt)
    sum_completion = int(row.sum_completion)
    sum_total_tokens = int(row.sum_total_tokens)
    sum_reasoning = int(row.sum_reasoning)
    sum_query_embed = int(row.sum_query_embed)
    sum_guardrail_embed = int(row.sum_guardrail_embed)
    sum_cost = float(row.sum_cost)
    sum_cached = min(int(row.sum_cached), sum_prompt)
    estimated_turns = int(row.estimated_turns)
    actual_turns = max(0, total_turns - estimated_turns)

    outcomes_dist = {
        "answered": outcome_rows.get("answered", 0),
        "ungrounded": outcome_rows.get("ungrounded", 0),
        "blocked": outcome_rows.get("blocked", 0),
        "conversational": outcome_rows.get("conversational", 0),
        "llm_failed": outcome_rows.get("llm_failed", 0),
    }

    # Groundedness pass rate over GENERATION_OUTCOMES only
    gen_turns = sum(outcomes_dist[k] for k in GENERATION_OUTCOMES)
    answered_turns = outcomes_dist["answered"]
    grounded_pct = (
        round((answered_turns / gen_turns * 100), 1) if gen_turns > 0 else 100.0
    )

    # Rates & Savings calculation
    rates = current_model_settings()
    cached_rate = rates.price_cached_per_million
    prompt_rate = rates.price_prompt_per_million
    output_rate = rates.price_output_per_million
    embed_rate = rates.price_embed_per_million

    # Visible vs reasoning tokens
    visible_tokens = max(0, sum_completion - sum_reasoning)
    # Cached prompt tokens are billed at the cached rate (as recorded per
    # exchange by compute_exchange_cost); the rest at the prompt rate.
    prompt_cost = ((sum_prompt - sum_cached) * prompt_rate) / 1e6
    cached_prompt_cost = (sum_cached * cached_rate) / 1e6
    cached_savings = (sum_cached * max(0.0, prompt_rate - cached_rate)) / 1e6
    reasoning_cost = (sum_reasoning * output_rate) / 1e6
    visible_cost = (visible_tokens * output_rate) / 1e6
    query_embed_cost = (sum_query_embed * embed_rate) / 1e6
    guardrail_embed_cost = (sum_guardrail_embed * embed_rate) / 1e6
    llm_cost = prompt_cost + cached_prompt_cost + reasoning_cost + visible_cost
    embeddings_cost = query_embed_cost + guardrail_embed_cost + ingest_cost

    total_platform_cost = sum_cost + ingest_cost
    avg_cost_per_query = (total_platform_cost / total_turns) if total_turns > 0 else 0.0
    actual_pct = (
        round((actual_turns / total_turns * 100), 1) if total_turns > 0 else 100.0
    )

    return {
        "audience": audience,
        "as_of": datetime.now(UTC).isoformat(),
        "financial": {
            "total_cost_usd": round(total_platform_cost, 6),
            "llm_cost_usd": round(llm_cost, 6),
            "embeddings_cost_usd": round(embeddings_cost, 6),
            "avg_cost_per_query_usd": round(avg_cost_per_query, 6),
            "stage_costs": {
                "query_embed_cost": round(query_embed_cost, 6),
                "guardrail_embed_cost": round(guardrail_embed_cost, 6),
                "prompt_uncached_cost": round(prompt_cost, 6),
                "cached_prompt_cost": round(cached_prompt_cost, 6),
                "cached_savings_usd": round(cached_savings, 6),
                "reasoning_cost": round(reasoning_cost, 6),
                "visible_cost": round(visible_cost, 6),
                "ingestion_overhead_cost": round(ingest_cost, 6),
            },
        },
        "tokens": {
            "total_billed_tokens": sum_total_tokens
            + sum_query_embed
            + sum_guardrail_embed
            + ingest_embed_tokens,
            "llm_tokens": {
                "prompt_context_tokens": sum_prompt,
                "cached_prompt_tokens": sum_cached,
                "reasoning_tokens": sum_reasoning,
                "visible_output_tokens": visible_tokens,
                "total_llm_tokens": sum_total_tokens,
            },
            "embeddings_tokens": {
                "query_embed_tokens": sum_query_embed,
                "guardrail_embed_tokens": sum_guardrail_embed,
                "ingestion_embed_tokens": ingest_embed_tokens,
                "total_embeddings_tokens": sum_query_embed
                + sum_guardrail_embed
                + ingest_embed_tokens,
            },
        },
        "pricing": {
            "price_embed_per_million": embed_rate,
            "price_prompt_per_million": prompt_rate,
            "price_output_per_million": output_rate,
            "price_cached_per_million": cached_rate,
        },
        "accounting": {
            "total_turns": total_turns,
            "actual_turns": actual_turns,
            "estimated_turns": estimated_turns,
            "actual_pct": actual_pct,
        },
        "quality": {
            "total_queries": total_turns,
            "outcomes": outcomes_dist,
            "groundedness_pass_rate": grounded_pct,
            "avg_billed_tokens_per_turn": round(sum_total_tokens / total_turns, 1)
            if total_turns > 0
            else 0,
            "latency": {
                "avg_ms": round(float(lat_row.avg_lat), 1),
                "p50_ms": round(float(lat_row.p50_lat), 1),
                "p95_ms": round(float(lat_row.p95_lat), 1),
            },
        },
        # B7.4 chart additions (contract: dashboard-aggregations.md §4).
        "cost_by_stage_series": stage_series,
        "forecast": forecast,
        "cache": usage_series.cache_stats(sum_cached, sum_prompt),
    }


# ============================================================================
# USERS TAB ENDPOINT (Task 3.3.1)
# ============================================================================


_PAGE_MAX_LIMIT = get_settings().usage_page_max_limit
_PAGE_MAX_OFFSET = get_settings().usage_max_offset

_ALLOWED_USER_SORTS = {
    "cost_desc",
    "cost_asc",
    "queries_desc",
    "queries_asc",
    "tokens_desc",
    "tokens_asc",
    "sessions_desc",
    "sessions_asc",
    "last_active_desc",
    "last_active_asc",
}


@router.get("/users")
async def get_usage_users(
    audience: Audience = Depends(audience_scope),
    q: str | None = None,
    user_type: str | None = Query(None, alias="type"),
    sort: str = "cost_desc",
    limit: int = Query(10, ge=1, le=_PAGE_MAX_LIMIT),
    offset: int = Query(0, ge=0, le=_PAGE_MAX_OFFSET),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    return await _usage_users_page(
        db,
        audience=audience,
        q=q,
        user_type=user_type,
        sort=sort,
        limit=limit,
        offset=offset,
    )


async def _usage_users_page(
    db: AsyncSession,
    *,
    audience: Audience,
    q: str | None,
    user_type: str | None,
    sort: str,
    limit: int,
    offset: int,
) -> dict:
    """Users rows for the tab (page-capped) and the CSV export (export-capped)."""
    if sort not in _ALLOWED_USER_SORTS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid sort parameter '{sort}'. Allowed: {sorted(_ALLOWED_USER_SORTS)}",
        )

    test_ips, _, _ = await get_classified_ips(db)

    user_msg = aliased(ChatMessageRecord, name="user_turn")
    asst_msg = aliased(ChatMessageRecord, name="asst_turn")

    # Base grouping by client IP
    ip_col = func.coalesce(user_msg.ip_address, "Unknown")
    query = (
        select(
            ip_col.label("ip_address"),
            func.count(func.distinct(user_msg.session_id)).label("sessions"),
            func.count(asst_msg.id).label("queries"),
            func.coalesce(func.sum(asst_msg.embedding_tokens), 0).label("embed_tokens"),
            func.coalesce(func.sum(asst_msg.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(asst_msg.reasoning_tokens), 0).label(
                "reasoning_tokens"
            ),
            func.coalesce(
                func.sum(
                    func.greatest(
                        0, asst_msg.completion_tokens - asst_msg.reasoning_tokens
                    )
                ),
                0,
            ).label("output_tokens"),
            func.coalesce(func.sum(asst_msg.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(asst_msg.cost_usd), 0.0).label("cost_usd"),
            func.max(user_msg.created_at).label("last_active"),
        )
        .select_from(user_msg)
        .join(
            asst_msg,
            and_(
                user_msg.session_id == asst_msg.session_id,
                user_msg.turn_index == asst_msg.turn_index,
                asst_msg.role == "assistant",
            ),
        )
        .where(user_msg.role == "user")
        .group_by(ip_col)
    )

    aud_cond = await build_audience_filter(db, user_msg.ip_address, audience)
    if aud_cond is not None:
        query = query.where(aud_cond)

    if q and q.strip():
        escaped = escape_ilike_pattern(q.strip())
        query = query.where(
            cast(user_msg.ip_address, String).ilike(f"%{escaped}%", escape="\\")
        )

    all_rows = (await db.execute(query)).all()
    items = []
    total_tokens_all = sum(r.total_tokens for r in all_rows) or 1

    for r in all_rows:
        is_test = r.ip_address in test_ips or r.ip_address == "Unknown"
        t_type = "Test" if is_test else "Production"
        if user_type and user_type.lower() != t_type.lower():
            continue

        items.append(
            {
                "ip_address": r.ip_address,
                "is_test": is_test,
                "type": t_type,
                "sessions": r.sessions,
                "queries": r.queries,
                "embed_tokens": int(r.embed_tokens),
                "prompt_tokens": int(r.prompt_tokens),
                "reasoning_tokens": int(r.reasoning_tokens),
                "output_tokens": int(r.output_tokens),
                "total_tokens": int(r.total_tokens),
                "cost_usd": round(float(r.cost_usd), 6),
                "last_active": r.last_active.isoformat() + "Z"
                if r.last_active
                else None,
            }
        )

    # Sort
    reverse = "asc" not in sort
    sort_key = "cost_usd"
    if sort.startswith("last_active"):
        # Same-format ISO-8601 strings sort chronologically.
        items.sort(key=lambda x: x["last_active"] or "", reverse=reverse)
        sort_key = ""
    elif "queries" in sort:
        sort_key = "queries"
    elif "sessions" in sort:
        sort_key = "sessions"
    elif "tokens" in sort:
        sort_key = "total_tokens"
    if sort_key:
        items.sort(
            key=lambda x: x.get(sort_key) if x.get(sort_key) is not None else 0,
            reverse=reverse,
        )

    # Compute header intelligence stats
    top_3 = items[:3]
    top_consumer_tokens = sum(x["total_tokens"] for x in top_3)
    top_consumer_share_pct = round((top_consumer_tokens / total_tokens_all * 100), 1)

    top_cards = [
        {
            "ip_address": x["ip_address"],
            "queries": x["queries"],
            "tokens": x["total_tokens"],
            "cost_usd": x["cost_usd"],
            "token_share_pct": round(x["total_tokens"] / total_tokens_all * 100, 1),
        }
        for x in top_3
    ]

    total_count = len(items)
    paged_items = items[offset : offset + limit]

    return {
        "items": paged_items,
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "stats": {
            "unique_users": total_count,
            "active_sessions": sum(x["sessions"] for x in items),
            "queries_driven": sum(x["queries"] for x in items),
            "top_consumer_share_pct": top_consumer_share_pct,
            "top_users": top_cards,
            "shared_gateway_hint": any(
                x["queries"] > _SHARED_GATEWAY_QUERY_THRESHOLD for x in items
            ),
        },
    }


# ============================================================================
# SESSIONS TAB ENDPOINT (Task 3.3.2)
# ============================================================================


_ALLOWED_SESSION_SORTS = {
    "cost_desc",
    "cost_asc",
    "queries_desc",
    "queries_asc",
    "tokens_desc",
    "tokens_asc",
    "last_active_desc",
    "last_active_asc",
}


@router.get("/sessions")
async def get_usage_sessions(
    audience: Audience = Depends(audience_scope),
    q: str | None = None,
    ip: str | None = None,
    sort: str = "last_active_desc",
    limit: int = Query(10, ge=1),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    """Paginated chat sessions list with turn counts, stage tokens, and stats (Task 3.3.2)."""
    settings = get_settings()
    if limit > settings.usage_page_max_limit or offset > settings.usage_max_offset:
        raise HTTPException(status_code=422, detail="Pagination limits exceeded.")
    return await _usage_sessions_page(
        db, audience=audience, q=q, ip=ip, sort=sort, limit=limit, offset=offset
    )


async def _usage_sessions_page(
    db: AsyncSession,
    *,
    audience: Audience,
    q: str | None,
    ip: str | None,
    sort: str,
    limit: int,
    offset: int,
) -> dict:
    """Sessions rows for the tab (page-capped) and the CSV export (export-capped)."""
    if sort not in _ALLOWED_SESSION_SORTS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid sort parameter '{sort}'. Allowed: {sorted(_ALLOWED_SESSION_SORTS)}",
        )

    user_msg = aliased(ChatMessageRecord, name="user_turn")
    asst_msg = aliased(ChatMessageRecord, name="asst_turn")

    query = (
        select(
            ChatSession.id.label("id"),
            ChatSession.title.label("title"),
            ChatSession.created_ip.label("ip_address"),
            ChatSession.last_activity_at.label("last_active"),
            func.count(asst_msg.id).label("queries"),
            func.coalesce(func.sum(asst_msg.embedding_tokens), 0).label("embed_tokens"),
            func.coalesce(func.sum(asst_msg.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(asst_msg.reasoning_tokens), 0).label(
                "reasoning_tokens"
            ),
            func.coalesce(
                func.sum(
                    func.greatest(
                        0, asst_msg.completion_tokens - asst_msg.reasoning_tokens
                    )
                ),
                0,
            ).label("output_tokens"),
            func.coalesce(func.sum(asst_msg.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(asst_msg.cost_usd), 0.0).label("cost_usd"),
        )
        .select_from(ChatSession)
        .join(
            user_msg,
            and_(
                ChatSession.id == user_msg.session_id,
                user_msg.role == "user",
            ),
        )
        .join(
            asst_msg,
            and_(
                user_msg.session_id == asst_msg.session_id,
                user_msg.turn_index == asst_msg.turn_index,
                asst_msg.role == "assistant",
            ),
        )
        .group_by(
            ChatSession.id,
            ChatSession.title,
            ChatSession.created_ip,
            ChatSession.last_activity_at,
        )
    )

    aud_cond = await build_audience_filter(db, ChatSession.created_ip, audience)
    if aud_cond is not None:
        query = query.where(aud_cond)

    if ip and ip.strip():
        query = query.where(ChatSession.created_ip == ip.strip())

    if q and q.strip():
        escaped = escape_ilike_pattern(q.strip())
        pattern = f"%{escaped}%"
        query = query.where(
            or_(
                ChatSession.title.ilike(pattern, escape="\\"),
                cast(ChatSession.id, String).ilike(pattern, escape="\\"),
                cast(ChatSession.created_ip, String).ilike(pattern, escape="\\"),
            )
        )

    all_rows = (await db.execute(query)).all()
    items = [
        {
            "id": str(r.id),
            "title": r.title,
            "ip_address": r.ip_address or "Unknown",
            "queries": r.queries,
            "embed_tokens": int(r.embed_tokens),
            "prompt_tokens": int(r.prompt_tokens),
            "reasoning_tokens": int(r.reasoning_tokens),
            "output_tokens": int(r.output_tokens),
            "total_tokens": int(r.total_tokens),
            "cost_usd": round(float(r.cost_usd), 6),
            "last_active": r.last_active.isoformat() + "Z" if r.last_active else None,
        }
        for r in all_rows
    ]

    reverse = "asc" not in sort
    if "cost" in sort:
        items.sort(
            key=lambda x: x.get("cost_usd") if x.get("cost_usd") is not None else 0.0,
            reverse=reverse,
        )
    elif "queries" in sort:
        items.sort(
            key=lambda x: x.get("queries") if x.get("queries") is not None else 0,
            reverse=reverse,
        )
    elif "tokens" in sort:
        items.sort(key=lambda x: x.get("total_tokens") or 0, reverse=reverse)
    else:
        items.sort(key=lambda x: x.get("last_active") or "", reverse=reverse)

    top_3 = sorted(items, key=lambda x: x["cost_usd"], reverse=True)[:3]
    top_cards = [
        {
            "id": x["id"],
            "title": x["title"],
            "ip_address": x["ip_address"],
            "queries": x["queries"],
            "tokens": x["total_tokens"],
            "cost_usd": x["cost_usd"],
        }
        for x in top_3
    ]

    total_count = len(items)
    avg_turns = (
        round(sum(x["queries"] for x in items) / total_count, 1)
        if total_count > 0
        else 0
    )
    avg_cost = (
        round(sum(x["cost_usd"] for x in items) / total_count, 6)
        if total_count > 0
        else 0.0
    )
    max_depth = max((x["queries"] for x in items), default=0)

    paged_items = items[offset : offset + limit]

    return {
        "items": paged_items,
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "stats": {
            "total_sessions": total_count,
            "avg_turns_per_session": avg_turns,
            "avg_cost_per_session": avg_cost,
            "max_depth": max_depth,
            "top_sessions": top_cards,
        },
    }


# ============================================================================
# QUERIES TAB ENDPOINT & INSPECTOR (Task 3.3.3 & 3.5.3)
# ============================================================================


_ALLOWED_QUERY_SORTS = {
    "created_at_desc",
    "created_at_asc",
    "cost_desc",
    "cost_asc",
    "tokens_desc",
    "tokens_asc",
    "latency_desc",
    "latency_asc",
}
_DATE_ONLY_LEN = len("2026-01-31")


def parse_usage_bound(
    value: str | None, name: str, *, end: bool = False
) -> datetime | None:
    """ISO-8601 bound for the usage lists, as naive UTC (the stored form).

    A date-only value (``2026-10-14``, what the dashboard's day links send)
    covers that whole day: as an upper bound it ends just before midnight.
    Invalid input is a 422, never a silently ignored filter.
    """
    if not value:
        return None
    raw = value.strip()
    try:
        if len(raw) == _DATE_ONLY_LEN:
            day = date.fromisoformat(raw)
            start = datetime(day.year, day.month, day.day)
            return start + timedelta(days=1, microseconds=-1) if end else start
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"Invalid {name} format. Use ISO 8601."
        ) from None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def usage_date_range(
    from_date: str | None, to_date: str | None
) -> tuple[datetime | None, datetime | None]:
    """Validated ``(from, to)`` pair for the usage lists and export."""
    lo = parse_usage_bound(from_date, "from_date")
    hi = parse_usage_bound(to_date, "to_date", end=True)
    if lo is not None and hi is not None and lo > hi:
        raise HTTPException(status_code=422, detail="from_date must be before to_date.")
    return lo, hi


@router.get("/queries")
async def get_usage_queries(
    audience: Audience = Depends(audience_scope),
    q: str | None = None,
    ip: str | None = None,
    session_id: str | None = None,
    outcome: str | None = None,
    grounded: bool | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    sort: str = "created_at_desc",
    limit: int = 10,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    """Paginated turns with filters, sort and header stats for the Queries tab.

    ``from_date`` / ``to_date`` take ISO-8601 datetimes or plain dates (a date
    covers the whole UTC day).
    """
    if sort not in _ALLOWED_QUERY_SORTS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid sort parameter '{sort}'. Allowed: {sorted(_ALLOWED_QUERY_SORTS)}",
        )
    settings = get_settings()
    if limit > settings.usage_page_max_limit or offset > settings.usage_max_offset:
        raise HTTPException(status_code=422, detail="Pagination limits exceeded.")
    lo, hi = usage_date_range(from_date, to_date)

    user_msg = aliased(ChatMessageRecord, name="user_turn")
    asst_msg = aliased(ChatMessageRecord, name="asst_turn")

    query = (
        select(
            asst_msg.request_id.label("request_id"),
            user_msg.created_at.label("created_at"),
            user_msg.content.label("query"),
            asst_msg.content.label("answer"),
            func.coalesce(user_msg.ip_address, ChatSession.created_ip, "Unknown").label(
                "ip_address"
            ),
            ChatSession.id.label("session_id"),
            ChatSession.title.label("session_title"),
            func.coalesce(asst_msg.prompt_tokens, 0).label("prompt_tokens"),
            func.coalesce(asst_msg.reasoning_tokens, 0).label("reasoning_tokens"),
            func.coalesce(
                func.greatest(
                    0, asst_msg.completion_tokens - asst_msg.reasoning_tokens
                ),
                0,
            ).label("output_tokens"),
            func.coalesce(asst_msg.embedding_tokens, 0).label("embed_tokens"),
            func.coalesce(asst_msg.cost_usd, 0.0).label("cost_usd"),
            asst_msg.outcome.label("outcome"),
            asst_msg.grounded.label("grounded"),
            LatencyRecord.total_ms.label("latency_ms"),
        )
        .select_from(user_msg)
        .join(
            asst_msg,
            and_(
                user_msg.session_id == asst_msg.session_id,
                user_msg.turn_index == asst_msg.turn_index,
                asst_msg.role == "assistant",
            ),
        )
        .join(ChatSession, user_msg.session_id == ChatSession.id)
        .outerjoin(LatencyRecord, asst_msg.request_id == LatencyRecord.request_id)
        .where(user_msg.role == "user")
    )

    query = await apply_usage_filters(
        query=query,
        db=db,
        user_alias=user_msg,
        asst_alias=asst_msg,
        audience=audience,
        from_date=lo,
        to_date=hi,
        ip=ip,
        session_id=session_id,
        outcome=outcome,
        grounded=grounded,
        search=q,
    )

    stats = await _query_stats(db, query)
    total_count = stats["total_queries"]

    sort_column = {
        "cost": asst_msg.cost_usd,
        "tokens": asst_msg.total_tokens,
        "latency": LatencyRecord.total_ms,
        "created_at": user_msg.created_at,
    }[sort.rsplit("_", 1)[0]]
    direction = asc if sort.endswith("_asc") else desc
    query = query.order_by(
        direction(sort_column).nulls_last(), desc(user_msg.created_at)
    )

    paged_rows = (await db.execute(query.limit(limit).offset(offset))).all()

    items = [
        {
            "request_id": r.request_id or "unknown",
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
            "query": (r.query or "")[:160],
            "answer": (r.answer or "")[:200],
            "ip_address": r.ip_address,
            "session_id": str(r.session_id),
            "session_title": r.session_title,
            "prompt_tokens": int(r.prompt_tokens),
            "reasoning_tokens": int(r.reasoning_tokens),
            "output_tokens": int(r.output_tokens),
            "embed_tokens": int(r.embed_tokens),
            "cost_usd": round(float(r.cost_usd), 6),
            "outcome": r.outcome or "answered",
            "grounded": r.grounded,
            "latency_ms": round(float(r.latency_ms), 1) if r.latency_ms else None,
        }
        for r in paged_rows
    ]

    return {
        "items": items,
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "stats": stats,
    }


async def _query_stats(db: AsyncSession, query: Any) -> dict[str, Any]:
    """Header stats of the Queries tab over every row matching the filters."""
    sub = query.subquery()
    outcome = func.coalesce(sub.c.outcome, "answered")
    generation = outcome.in_(GENERATION_OUTCOMES)
    agg = (
        await db.execute(
            select(
                func.count().label("total"),
                func.count().filter(generation).label("evaluated"),
                func.count()
                .filter(generation & sub.c.grounded.is_(True))
                .label("passed"),
                func.avg(sub.c.latency_ms).label("avg_latency_ms"),
                func.avg(
                    sub.c.prompt_tokens + sub.c.reasoning_tokens + sub.c.output_tokens
                ).label("avg_tokens"),
            )
        )
    ).one()
    by_outcome = (
        await db.execute(select(outcome, func.count()).group_by(outcome))
    ).all()
    evaluated = int(agg.evaluated or 0)
    passed = int(agg.passed or 0)
    stats: dict[str, Any] = {
        "total_queries": int(agg.total or 0),
        "outcomes": {str(k): int(v) for k, v in by_outcome},
        "groundedness_evaluated_turns": evaluated,
        "groundedness_passed_turns": passed,
        "avg_latency_ms": _round_or_none(agg.avg_latency_ms),
        "avg_tokens_per_query": _round_or_none(agg.avg_tokens),
    }
    if evaluated:
        stats["groundedness_pass_rate"] = round(100 * passed / evaluated, 1)
    return stats


def _round_or_none(value: Any) -> float | None:
    return None if value is None else round(float(value), 1)


@router.get("/queries/{request_id}")
async def get_query_detail(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    """Single-turn inspector detail exposing rates and cached prompt details (Task 3.5.3)."""
    user_msg = aliased(ChatMessageRecord, name="user_turn")
    asst_msg = aliased(ChatMessageRecord, name="asst_turn")

    row = (
        await db.execute(
            select(
                asst_msg,
                user_msg.content.label("query"),
                user_msg.ip_address.label("ip_address"),
                ChatSession.title.label("session_title"),
                LatencyRecord.total_ms.label("latency_ms"),
                LatencyRecord.embedding_ms.label("embedding_ms"),
                LatencyRecord.retrieval_ms.label("retrieval_ms"),
                LatencyRecord.llm_ms.label("llm_ms"),
                LatencyRecord.reranker_ms.label("reranker_ms"),
            )
            .select_from(asst_msg)
            .join(
                user_msg,
                and_(
                    asst_msg.session_id == user_msg.session_id,
                    asst_msg.turn_index == user_msg.turn_index,
                    user_msg.role == "user",
                ),
            )
            .join(ChatSession, asst_msg.session_id == ChatSession.id)
            .outerjoin(LatencyRecord, asst_msg.request_id == LatencyRecord.request_id)
            .where(asst_msg.request_id == request_id)
        )
    ).first()

    if not row:
        raise HTTPException(status_code=404, detail="Query record not found.")

    (
        asst,
        query_text,
        client_ip,
        session_title,
        lat_total,
        lat_embed,
        lat_ret,
        lat_llm,
        lat_rerank,
    ) = row
    details = asst.token_details or {}
    rate_card = billed_rate_card(details)

    return {
        "request_id": asst.request_id,
        "session_id": str(asst.session_id),
        "session_title": session_title,
        "query": query_text,
        "answer": asst.content,
        "created_at": asst.created_at.isoformat() + "Z" if asst.created_at else None,
        "ip_address": client_ip or "Unknown",
        "outcome": asst.outcome or "answered",
        "grounded": asst.grounded,
        "guardrail_flags": asst.guardrail_flags or [],
        "cost_usd": asst.cost_usd or 0.0,
        "tokens": {
            "prompt": asst.prompt_tokens or 0,
            "prompt_tokens": asst.prompt_tokens or 0,
            "completion": asst.completion_tokens or 0,
            "completion_tokens": asst.completion_tokens or 0,
            "reasoning": asst.reasoning_tokens or 0,
            "reasoning_tokens": asst.reasoning_tokens or 0,
            "visible": max(
                0, (asst.completion_tokens or 0) - (asst.reasoning_tokens or 0)
            ),
            "cached_prompt": details.get("cached_tokens", 0),
            "cached_tokens": details.get("cached_tokens", 0),
            "query_embed": asst.embedding_tokens or 0,
            "query_embed_tokens": asst.embedding_tokens or 0,
            "guardrail_embed": asst.guardrail_tokens or 0,
            "guardrail_embed_tokens": asst.guardrail_tokens or 0,
            "total_tokens": asst.total_tokens or 0,
            "total_billed": (asst.embedding_tokens or 0)
            + (asst.guardrail_tokens or 0)
            + (asst.total_tokens or 0),
        },
        "rates": {
            **rate_card,
            "price_prompt_per_million": rate_card["price_input_per_million"],
        },
        "pricing_rate_card": rate_card,
        "latencies": {
            "total_ms": lat_total,
            "embedding_ms": lat_embed,
            "retrieval_ms": lat_ret,
            "llm_ms": lat_llm,
            "reranker_ms": lat_rerank,
        },
    }


# ============================================================================
# STREAMING CSV EXPORT (Task 3.4)
# ============================================================================


@router.get("/export")
async def export_usage_csv(
    request: Request,
    tab: str = Query("queries", pattern="^(users|sessions|queries)$"),
    audience: Audience = Depends(audience_scope),
    q: str | None = None,
    ip: str | None = None,
    session_id: str | None = None,
    outcome: str | None = None,
    grounded: bool | None = None,
    user_type: str | None = Query(None, alias="type"),
    from_date: str | None = None,
    to_date: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.USAGE_EXPORT)),
):
    """Stream CSV export with formula injection defense and cap truncation (Task 3.4)."""
    settings = get_settings()
    lo, hi = usage_date_range(from_date, to_date)
    brand_slug = settings.brand_name.lower().replace(" ", "-")
    now_str = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"{brand_slug}-{tab}-{audience}-{now_str}.csv"

    # Audit export
    await record_audit_event(
        db,
        request_id=getattr(request.state, "request_id", "admin-export"),
        actor=user.email,
        action="usage_export",
        detail={"tab": tab, "audience": audience},
        commit=True,
    )

    async def csv_generator() -> AsyncGenerator[str, None]:
        # Yield UTF-8 BOM for Microsoft Excel compatibility
        yield "\ufeff"

        out = io.StringIO()
        writer = csv.writer(out)

        if tab == "queries":
            headers = [
                "Request ID",
                "Created At",
                "User (IP)",
                "Session Title",
                "Query",
                "Prompt Tokens",
                "Reasoning Tokens",
                "Output Tokens",
                "Embed Tokens",
                "Cost ($)",
                "Status",
                "Grounded",
            ]
            writer.writerow(headers)
            yield out.getvalue()
            out.seek(0)
            out.truncate(0)

            # Query data
            user_msg = aliased(ChatMessageRecord, name="user_turn")
            asst_msg = aliased(ChatMessageRecord, name="asst_turn")
            stmt = (
                select(
                    asst_msg.request_id,
                    user_msg.created_at,
                    user_msg.ip_address,
                    ChatSession.title,
                    user_msg.content,
                    asst_msg.prompt_tokens,
                    asst_msg.reasoning_tokens,
                    func.greatest(
                        0, asst_msg.completion_tokens - asst_msg.reasoning_tokens
                    ),
                    asst_msg.embedding_tokens,
                    asst_msg.cost_usd,
                    asst_msg.outcome,
                    asst_msg.grounded,
                )
                .select_from(user_msg)
                .join(
                    asst_msg,
                    and_(
                        user_msg.session_id == asst_msg.session_id,
                        user_msg.turn_index == asst_msg.turn_index,
                        asst_msg.role == "assistant",
                    ),
                )
                .join(ChatSession, user_msg.session_id == ChatSession.id)
                .where(user_msg.role == "user")
            )
            stmt = await apply_usage_filters(
                query=stmt,
                db=db,
                user_alias=user_msg,
                asst_alias=asst_msg,
                audience=audience,
                from_date=lo,
                to_date=hi,
                ip=ip,
                session_id=session_id,
                outcome=outcome,
                grounded=grounded,
                search=q,
            )
            stmt = stmt.order_by(desc(user_msg.created_at)).limit(
                settings.usage_export_max_rows
            )

            rows = (await db.execute(stmt)).all()
            for r in rows:
                row_cells = [
                    sanitize_csv_cell(r[0]),
                    sanitize_csv_cell(r[1].isoformat() if r[1] else ""),
                    sanitize_csv_cell(r[2] or "Unknown"),
                    sanitize_csv_cell(r[3]),
                    sanitize_csv_cell(r[4]),
                    sanitize_csv_cell(r[5] or 0),
                    sanitize_csv_cell(r[6] or 0),
                    sanitize_csv_cell(r[7] or 0),
                    sanitize_csv_cell(r[8] or 0),
                    sanitize_csv_cell(
                        f"{r[9]:.6f}" if r[9] is not None else "0.000000"
                    ),
                    sanitize_csv_cell(r[10] or "answered"),
                    sanitize_csv_cell("Yes" if r[11] else "No"),
                ]
                writer.writerow(row_cells)
                yield out.getvalue()
                out.seek(0)
                out.truncate(0)

        elif tab == "users":
            headers = [
                "User (IP)",
                "Type",
                "Sessions",
                "Queries",
                "Embed Tokens",
                "Prompt Tokens",
                "Reasoning Tokens",
                "Output Tokens",
                "Total Tokens",
                "Cost ($)",
            ]
            writer.writerow(headers)
            yield out.getvalue()
            out.seek(0)
            out.truncate(0)

            user_res = await _usage_users_page(
                db,
                audience=audience,
                q=q,
                user_type=user_type,
                sort="cost_desc",
                limit=settings.usage_export_max_rows,
                offset=0,
            )
            for item in user_res["items"]:
                writer.writerow(
                    [
                        sanitize_csv_cell(item["ip_address"]),
                        sanitize_csv_cell(item["type"]),
                        sanitize_csv_cell(item["sessions"]),
                        sanitize_csv_cell(item["queries"]),
                        sanitize_csv_cell(item["embed_tokens"]),
                        sanitize_csv_cell(item["prompt_tokens"]),
                        sanitize_csv_cell(item["reasoning_tokens"]),
                        sanitize_csv_cell(item["output_tokens"]),
                        sanitize_csv_cell(item["total_tokens"]),
                        sanitize_csv_cell(f"{item['cost_usd']:.6f}"),
                    ]
                )
                yield out.getvalue()
                out.seek(0)
                out.truncate(0)

        elif tab == "sessions":
            headers = [
                "Session ID",
                "Session Title",
                "User (IP)",
                "Queries",
                "Total Tokens",
                "Cost ($)",
                "Last Active",
            ]
            writer.writerow(headers)
            yield out.getvalue()
            out.seek(0)
            out.truncate(0)

            # Not the page route: its 200-row page cap would reject the
            # export size with a 422 after the CSV header was already sent.
            sess_res = await _usage_sessions_page(
                db,
                audience=audience,
                q=q,
                ip=ip,
                sort="cost_desc",
                limit=settings.usage_export_max_rows,
                offset=0,
            )
            for item in sess_res["items"]:
                writer.writerow(
                    [
                        sanitize_csv_cell(item["id"]),
                        sanitize_csv_cell(item["title"]),
                        sanitize_csv_cell(item["ip_address"]),
                        sanitize_csv_cell(item["queries"]),
                        sanitize_csv_cell(item["total_tokens"]),
                        sanitize_csv_cell(f"{item['cost_usd']:.6f}"),
                        sanitize_csv_cell(item["last_active"] or ""),
                    ]
                )
                yield out.getvalue()
                out.seek(0)
                out.truncate(0)

    return StreamingResponse(
        csv_generator(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
