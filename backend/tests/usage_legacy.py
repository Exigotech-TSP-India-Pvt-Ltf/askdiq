"""The Usage & Cost list builders as they were before ``usage_turns``
(self-join of ``chat_messages`` per request): the parity reference of
``test_usage_ledger``. Test-only; never imported by the application."""

# ruff: noqa
# pylint: skip-file
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Select, String, and_, asc, cast, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.governance.audience import Audience, build_audience_filter, get_classified_ips
from app.governance.outcomes import GENERATION_OUTCOMES
from app.governance.usage_queries import escape_ilike_pattern
from app.models.db_models import ChatMessageRecord, ChatSession, LatencyRecord
import uuid

_SHARED_GATEWAY_QUERY_THRESHOLD = 50
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
_ALLOWED_SESSION_SORTS = _ALLOWED_USER_SORTS - {"sessions_desc", "sessions_asc"}


def _turn_time_filter(query, user_msg, lo, hi):
    if lo is not None:
        query = query.where(user_msg.created_at >= lo)
    if hi is not None:
        query = query.where(user_msg.created_at <= hi)
    return query


def _round_or_none(value):
    return None if value is None else round(float(value), 1)


@dataclass(frozen=True, slots=True)
class UsageQueryArgs:
    audience: str
    q: str | None
    ip: str | None
    session_id: str | None
    outcome: str | None
    grounded: bool | None
    lo: datetime | None
    hi: datetime | None
    sort: str
    limit: int
    offset: int


async def _usage_users_page(
    db: AsyncSession,
    *,
    audience: Audience,
    q: str | None,
    user_type: str | None,
    sort: str,
    limit: int,
    offset: int,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
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
    query = _turn_time_filter(query, user_msg, from_dt, to_dt)

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


async def _usage_sessions_page(
    db: AsyncSession,
    *,
    audience: Audience,
    q: str | None,
    ip: str | None,
    sort: str,
    limit: int,
    offset: int,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
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
    query = _turn_time_filter(query, user_msg, from_dt, to_dt)

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


async def _usage_queries_page(db: AsyncSession, args: UsageQueryArgs) -> dict:
    audience, q, ip, session_id = args.audience, args.q, args.ip, args.session_id
    outcome, grounded, lo, hi = args.outcome, args.grounded, args.lo, args.hi
    sort, limit, offset = args.sort, args.limit, args.offset
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


async def apply_usage_filters(
    query: Select,
    db: Any,
    user_alias: Any,
    asst_alias: Any,
    audience: Audience,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    ip: str | None = None,
    session_id: str | None = None,
    outcome: str | None = None,
    grounded: bool | None = None,
    search: str | None = None,
) -> Select:
    """Apply composable SQL filters to the usage selectable."""
    # 1. Audience filter on IP address (coalesce user message IP with session created IP)
    client_ip_col = func.coalesce(user_alias.ip_address, ChatSession.created_ip)
    aud_filter = await build_audience_filter(db, client_ip_col, audience)
    if aud_filter is not None:
        query = query.where(aud_filter)

    # 2. Date range
    if from_date is not None:
        query = query.where(user_alias.created_at >= from_date)
    if to_date is not None:
        query = query.where(user_alias.created_at <= to_date)

    # 3. Exact filters (IP matches the displayed coalesce of message and session IP)
    if ip:
        query = query.where(client_ip_col == ip.strip())
    if session_id:
        try:
            s_uuid = uuid.UUID(session_id.strip())
            query = query.where(user_alias.session_id == s_uuid)
        except ValueError:
            pass
    if outcome:
        query = query.where(asst_alias.outcome == outcome.strip().lower())
    if grounded is not None:
        query = query.where(asst_alias.grounded == grounded)

    # 4. Search query (escaped ILIKE matching across query, answer, session_title, ip, request_id)
    if search and search.strip():
        escaped = escape_ilike_pattern(search.strip())
        pattern = f"%{escaped}%"
        query = query.where(
            or_(
                user_alias.content.ilike(pattern, escape="\\"),
                asst_alias.content.ilike(pattern, escape="\\"),
                ChatSession.title.ilike(pattern, escape="\\"),
                cast(user_alias.ip_address, String).ilike(pattern, escape="\\"),
                cast(asst_alias.request_id, String).ilike(pattern, escape="\\"),
            )
        )

    return query
