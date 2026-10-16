"""Admin endpoints for users and conversation management."""

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.admin_entities import router as entities_router
from app.api.routes.admin_feedback import router as feedback_router
from app.api.routes.admin_governance import router as governance_router
from app.api.routes.admin_usage import USAGE_CARD_CACHE_PREFIX
from app.api.routes.admin_usage import router as usage_router
from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.cache import aggregation_cache
from app.core.concurrency import ScopedRunner, get_scoped_runner
from app.core.config import get_settings
from app.core.database import get_db
from app.core.http_cache import cached_response
from app.core.logging_config import get_logger
from app.core.middleware import METRICS
from app.core.request_meta import get_client_ip
from app.dashboard import activity as dashboard_activity
from app.dashboard import service as dashboard_service
from app.dashboard import window as dashboard_window
from app.entities import conversations as conversation_stats
from app.entities import visitors
from app.governance.audience import (
    Audience,
    audience_scope,
    build_audience_filter,
    get_classified_ips,
)
from app.governance.costing import compute_exchange_cost
from app.governance.log_summary import level_for
from app.governance.monitoring import (
    QUERY_ACTION,
)
from app.governance.test_ips import (
    add_or_update_test_ip,
    delete_test_ip,
    get_effective_test_ips,
    get_test_ip_details,
    is_test_ip,
)
from app.guardrails.conversational import (
    CAPABILITY_RESPONSE,
    FAREWELL_RESPONSE,
    GREETING_RESPONSE,
    THANKS_RESPONSE,
    detect_conversational_intent,
)
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    Chunk,
    Document,
    LatencyRecord,
    User,
)
from app.schemas.dashboard import ActivityFeed, DashboardData
from app.schemas.entity_stats import OutcomeName

router = APIRouter(prefix="/admin", tags=["admin"])
logger = get_logger(__name__)
router.include_router(usage_router)
router.include_router(feedback_router)
router.include_router(governance_router)
# Before `/conversations/{conversation_id}` so `/conversations/stats` matches.
router.include_router(entities_router)


class TestIpPayload(BaseModel):
    ip_address: str
    description: str | None = None


# ============================================================================
# USERS
# ============================================================================


@router.get("/users")
async def list_users(
    audience: str = Query("overview"),
    include_test: bool | None = None,
    q: str | None = None,
    status: str | None = None,
    user_type: str | None = Query(None, alias="type"),
    sort: str = "last_active_desc",
    limit: int = 50,
    offset: int = 0,
    weekday: int | None = Query(
        None, ge=0, le=6, description="Heatmap filter: session start weekday (0 = Mon)."
    ),
    hour: int | None = Query(
        None, ge=0, le=23, description="Heatmap filter: session start hour (UTC)."
    ),
    slot_from: str | None = Query(
        None, alias="from", max_length=10, description="Slot filter first day."
    ),
    slot_to: str | None = Query(
        None, alias="to", max_length=10, description="Slot filter last day."
    ),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    """Return users grouped by client IP with activity statistics (Task 2.3.1).

    `weekday` + `hour` (both or neither) keep visitors with a session started
    in that UTC slot within `from`..`to` (days, default the last 7 days).
    """
    if (weekday is None) != (hour is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`weekday` and `hour` must be sent together.",
        )
    slot_ips: set[str] | None = None
    if weekday is not None and hour is not None:
        days = dashboard_window.resolve_days(
            dashboard_window.parse_day(slot_from, "from"),
            dashboard_window.parse_day(slot_to, "to"),
            today=dashboard_window.utcnow().date(),
            max_days=get_settings().usage_max_range_days,
        )
        slot_ips = await visitors.ips_in_slot(db, weekday, hour, days)
    # Audience resolution (preserving include_test backwards-compatibility)
    if include_test is False and audience == "overview":
        aud_scope = "users"
    elif include_test is True:
        aud_scope = "overview"
    else:
        aud_scope = audience_scope(audience)

    test_ips, _, _ = await get_classified_ips(db)

    ip_rows = (
        await db.execute(
            select(
                ChatSession.created_ip.label("ip"),
                func.min(ChatSession.created_at).label("first_seen"),
                func.max(ChatSession.last_activity_at).label("last_active"),
                func.count(ChatSession.id).label("sessions"),
            )
            .where(ChatSession.created_ip.isnot(None))
            .group_by(ChatSession.created_ip)
        )
    ).all()

    queries_by_ip = dict(
        (
            await db.execute(
                select(ChatMessageRecord.ip_address, func.count(ChatMessageRecord.id))
                .where(ChatMessageRecord.ip_address.isnot(None))
                .group_by(ChatMessageRecord.ip_address)
            )
        ).all()
    )

    feedback_counts = dict(
        (
            await db.execute(
                select(AuditLog.ip_address, func.count(AuditLog.id))
                .where(
                    AuditLog.action.ilike("%feedback%"),
                    AuditLog.ip_address.isnot(None),
                )
                .group_by(AuditLog.ip_address)
            )
        ).all()
    )

    all_users = []
    for row in ip_rows:
        ip = row.ip
        is_test = ip in test_ips or ip in ("127.0.0.1", "::1", "localhost", "0.0.0.0")  # nosec B104  # noqa: S104 - IP literal compared, not a bind address
        t_type = "Test" if is_test else "Production"

        if aud_scope == "users" and is_test:
            continue
        if aud_scope == "testing" and not is_test:
            continue

        if user_type and user_type.lower() != t_type.lower():
            continue

        if q and q.strip() and q.strip().lower() not in ip.lower():
            continue

        if slot_ips is not None and ip not in slot_ips:
            continue

        all_users.append(
            {
                "id": f"client-{ip}",
                "ip_address": ip,
                "is_test": is_test,
                "type": t_type,
                "status": "Active",
                "sessions": row.sessions,
                "conversations": row.sessions,
                "queries": queries_by_ip.get(ip, 0),
                "feedback_count": feedback_counts.get(ip, 0),
                "first_seen": row.first_seen.isoformat() if row.first_seen else None,
                "last_active": row.last_active.isoformat() if row.last_active else None,
            }
        )

    reverse = "asc" not in sort
    all_users.sort(key=lambda u: u.get("last_active") or "", reverse=reverse)

    total = len(all_users)
    paged = all_users[offset : offset + limit]

    return {
        "items": paged,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    """Return detailed information about one user."""
    feedback_counts = dict(
        (
            await db.execute(
                select(AuditLog.ip_address, func.count(AuditLog.id))
                .where(
                    AuditLog.action.ilike("%feedback%"),
                    AuditLog.ip_address.is_not(None),
                )
                .group_by(AuditLog.ip_address)
            )
        ).all()
    )

    if user_id.startswith("client-"):
        ip = user_id.removeprefix("client-")
        sessions_res = (
            (
                await db.execute(
                    select(ChatSession)
                    .where(ChatSession.created_ip == ip)
                    .order_by(ChatSession.last_activity_at.desc())
                )
            )
            .scalars()
            .all()
        )

        if not sessions_res:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        query_count = (
            await db.scalar(
                select(func.count(ChatMessageRecord.id)).where(
                    ChatMessageRecord.ip_address == ip
                )
            )
            or 0
        )

        first_seen = min(s.created_at for s in sessions_res)
        last_active = max(s.last_activity_at for s in sessions_res)

        return {
            "id": user_id,
            "email": f"client-{ip}@askdiq.local",
            "full_name": f"Client ({ip})",
            "ip_address": ip,
            "first_seen": first_seen,
            "last_active": last_active,
            "status": "Active",
            "sessions": len(sessions_res),
            "conversations": len(sessions_res),
            "queries": query_count,
            "feedback_count": feedback_counts.get(ip, 0),
            "conversations_data": [
                {
                    "id": str(s.id),
                    "title": s.title,
                    "created_at": s.created_at,
                    "last_activity_at": s.last_activity_at,
                    "message_count": 0,
                }
                for s in sessions_res
            ],
        }

    # Find user by UUID
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    conversations_result = await db.execute(
        select(
            ChatSession,
            func.count(ChatMessageRecord.id).label("message_count"),
        )
        .outerjoin(
            ChatMessageRecord,
            ChatMessageRecord.session_id == ChatSession.id,
        )
        .where(ChatSession.user_email == user.email)
        .group_by(ChatSession.id)
        .order_by(ChatSession.last_activity_at.desc())
    )
    conversation_rows = conversations_result.all()

    query_result = await db.execute(
        select(func.count(ChatMessageRecord.id))
        .join(
            ChatSession,
            ChatMessageRecord.session_id == ChatSession.id,
        )
        .where(ChatSession.user_email == user.email)
    )
    query_count = query_result.scalar() or 0

    ip_result = await db.execute(
        select(ChatSession.created_ip)
        .where(
            ChatSession.user_email == user.email,
            ChatSession.created_ip.is_not(None),
        )
        .order_by(ChatSession.last_activity_at.desc())
        .limit(1)
    )
    ip_address = ip_result.scalar_one_or_none()

    last_activity = None
    if conversation_rows:
        last_activity = conversation_rows[0][0].last_activity_at

    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "ip_address": ip_address,
        "first_seen": user.created_at,
        "last_active": last_activity or user.created_at,
        "status": "Active",
        "sessions": len(conversation_rows),
        "conversations": len(conversation_rows),
        "queries": query_count,
        "feedback_count": feedback_counts.get(ip_address, 0),
        "conversations_data": [
            {
                "id": str(session.id),
                "title": session.title,
                "created_at": session.created_at,
                "last_activity_at": session.last_activity_at,
                "message_count": message_count or 0,
            }
            for session, message_count in conversation_rows
        ],
    }


# ============================================================================
# CONVERSATIONS
# ============================================================================


@router.get("/conversations")
async def list_conversations(
    audience: str = Query("overview"),
    q: str | None = None,
    ip: str | None = None,
    sort: str = "last_activity_desc",
    limit: int = 50,
    offset: int = 0,
    min_turns: int | None = Query(
        None, ge=1, description="Inclusive turn-count floor."
    ),
    max_turns: int | None = Query(None, ge=1, description="Inclusive turn-count cap."),
    outcome: OutcomeName | None = Query(
        None, description="Conversations with at least one turn of this outcome."
    ),
    day: date | None = Query(
        None, description="With `outcome`: only turns on this UTC day (YYYY-MM-DD)."
    ),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    """Return conversations for the admin dashboard (Task 2.3.2: audience-scoped, paginated, no email).

    Rows carry `turn_count` and `outcome_counts` (NULL outcome = answered).
    """
    if min_turns is not None and max_turns is not None and min_turns > max_turns:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`min_turns` must not exceed `max_turns`.",
        )
    if day is not None and outcome is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`day` requires `outcome`.",
        )
    aud_scope = audience_scope(audience)
    turn_count = conversation_stats.turn_count_sql()

    query = (
        select(
            ChatSession,
            func.count(ChatMessageRecord.id).label("message_count"),
            turn_count.label("turn_count"),
        )
        .outerjoin(
            ChatMessageRecord,
            ChatMessageRecord.session_id == ChatSession.id,
        )
        .group_by(ChatSession.id)
    )

    for cond in await conversation_stats.session_conditions(db, aud_scope, ip, q):
        query = query.where(cond)
    if outcome is not None:
        query = query.where(conversation_stats.outcome_exists(outcome.value, day))
    if min_turns is not None:
        query = query.having(turn_count >= min_turns)
    if max_turns is not None:
        query = query.having(turn_count <= max_turns)

    # Sort
    if sort == "last_activity_asc":
        query = query.order_by(ChatSession.last_activity_at.asc())
    else:
        query = query.order_by(ChatSession.last_activity_at.desc())

    # Total count
    count_sub = query.subquery()
    total_count = (
        await db.execute(select(func.count()).select_from(count_sub))
    ).scalar() or 0

    rows = (await db.execute(query.limit(limit).offset(offset))).all()
    outcomes = await conversation_stats.outcome_counts(db, [r[0].id for r in rows])

    items = []
    for session, message_count, turns in rows:
        items.append(
            {
                "id": str(session.id),
                "title": session.title,
                "created_at": session.created_at.isoformat()
                if session.created_at
                else None,
                "last_activity_at": session.last_activity_at.isoformat()
                if session.last_activity_at
                else None,
                "expires_at": session.expires_at.isoformat()
                if session.expires_at
                else None,
                "created_ip": session.created_ip or "Unknown",
                "message_count": message_count or 0,
                "turn_count": turns or 0,
                "outcome_counts": outcomes.get(str(session.id), {}),
            }
        )

    return {
        "items": items,
        "total": total_count,
        "limit": limit,
        "offset": offset,
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    turn: int | None = Query(
        None, ge=0, description="`turn_index` to focus; echoed as `focus_turn`."
    ),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    """Return one conversation, all of its messages and per-turn detail.

    `turns` (one per `turn_index`) adds stage timings, tokens, cost, sources,
    feedback, insight labels and the promoted golden case id.
    """

    # -----------------------------------------------------------------------
    # Find conversation
    # -----------------------------------------------------------------------
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == conversation_id)
    )

    conversation = result.scalar_one_or_none()

    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    # -----------------------------------------------------------------------
    # Load messages
    # -----------------------------------------------------------------------
    messages_result = await db.execute(
        select(ChatMessageRecord)
        .where(ChatMessageRecord.session_id == conversation.id)
        .order_by(
            ChatMessageRecord.turn_index.asc(),
            ChatMessageRecord.created_at.asc(),
        )
    )

    messages = messages_result.scalars().all()
    turns = await conversation_stats.turn_details(db, messages)
    turn_indexes = {t["turn_index"] for t in turns}

    # -----------------------------------------------------------------------
    # Response
    # -----------------------------------------------------------------------
    return {
        "conversation": {
            "id": str(conversation.id),
            "user_email": conversation.user_email,
            "title": conversation.title,
            "created_at": conversation.created_at,
            "last_activity_at": conversation.last_activity_at,
            "expires_at": conversation.expires_at,
            "created_ip": conversation.created_ip,
        },
        "messages": [
            {
                "id": str(message.id),
                "session_id": str(message.session_id),
                "turn_index": message.turn_index,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at,
                "request_id": message.request_id,
                "grounded": message.grounded,
                "guardrail_flags": message.guardrail_flags,
                "ip_address": message.ip_address,
            }
            for message in messages
        ],
        "message_count": len(messages),
        "turns": turns,
        "focus_turn": turn if turn in turn_indexes else None,
    }


# ============================================================================
# GOVERNANCE
# ============================================================================
# Monitoring, traces, logs, overview and the live stream live in
# app/api/routes/admin_governance.py (included below under /admin).

# Backward-compatible alias (tests and callers import it from here).
_log_level_for = level_for


# ============================================================================
# AI USAGE
# ============================================================================


def _est_tokens(text: str | None) -> int:
    return max(1, len(text or "") // 4) if text else 0


def _r(v, d=0):
    return round(v, d) if isinstance(v, (int, float)) else None


def _float_or_none(value: Any) -> float | None:
    return None if value is None else float(value)


async def _latency_stats(db: AsyncSession, hours: int = 24 * 30) -> dict[str, Any]:
    """Counts and stage averages over ``hours``, aggregated in SQL (one row)
    instead of loading every ``latency_metrics`` row into Python."""
    since = datetime.utcnow() - timedelta(hours=hours)
    row = (
        await db.execute(
            select(
                func.count(LatencyRecord.id),
                func.avg(LatencyRecord.llm_ms),
                func.avg(LatencyRecord.embedding_ms),
                func.count(LatencyRecord.reranker_ms),
                func.avg(LatencyRecord.reranker_ms),
            ).where(LatencyRecord.created_at >= since)
        )
    ).one()
    return {
        "requests": int(row[0]),
        "llm_ms": _float_or_none(row[1]),
        "embedding_ms": _float_or_none(row[2]),
        "reranker_requests": int(row[3]),
        "reranker_ms": _float_or_none(row[4]),
    }


def _usage_card_key(name: str) -> str:
    """Aggregation-cache key for a usage card; the permission scope is part of it."""
    return f"{USAGE_CARD_CACHE_PREFIX}{Permission.DASHBOARD_READ.value}:{name}"


async def _query_usage_rows(db: AsyncSession) -> list[dict]:
    """One row per query (user+assistant message pair), with token/cost
    accounting shared by every usage/cost page so they all agree.

    Uses the *actual* Azure OpenAI usage captured at generation time
    (ChatMessageRecord.prompt_tokens/completion_tokens — see
    app.harness.prompts.call_llm) whenever it was recorded. Only falls back
    to the old length-based estimate for exchanges that predate that
    column (no LLM call usage was ever persisted) or had no real LLM call
    (guardrail-blocked/fallback/conversational replies).
    """

    get_settings()

    rows = (
        await db.execute(
            select(
                ChatMessageRecord.session_id,
                ChatMessageRecord.turn_index,
                ChatMessageRecord.role,
                ChatMessageRecord.content,
                ChatMessageRecord.created_at,
                ChatMessageRecord.request_id,
                ChatMessageRecord.grounded,
                ChatMessageRecord.guardrail_flags,
                ChatMessageRecord.ip_address,
                ChatMessageRecord.prompt_tokens,
                ChatMessageRecord.completion_tokens,
                ChatMessageRecord.total_tokens,
                ChatMessageRecord.reasoning_tokens,
                ChatMessageRecord.embedding_tokens,
                ChatMessageRecord.guardrail_tokens,
                ChatMessageRecord.cost_usd,
                ChatMessageRecord.token_details,
                ChatSession.user_email,
                ChatSession.title,
                ChatSession.created_ip,
            )
            .join(ChatSession, ChatMessageRecord.session_id == ChatSession.id)
            .order_by(ChatSession.id, ChatMessageRecord.turn_index)
        )
    ).all()

    pairs: dict[tuple, dict] = {}
    for r in rows:
        key = (r.session_id, r.turn_index)
        p = pairs.setdefault(
            key,
            {
                "session_id": str(r.session_id),
                "session_title": r.title,
                "user_email": r.user_email,
                "ip_address": r.ip_address or r.created_ip or "Unknown",
                "query": "",
                "answer": "",
                "created_at": None,
                "request_id": None,
                "grounded": None,
                "guardrail_flags": [],
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "reasoning_tokens": 0,
                "embedding_tokens": 0,
                "guardrail_tokens": 0,
                "cost_usd": None,
                "token_details": None,  # nosec B105
            },
        )
        if r.role == "user":
            p["query"] = r.content
            p["ip_address"] = r.ip_address or p["ip_address"]
            p["created_at"] = r.created_at
        elif r.role == "assistant":
            p["answer"] = r.content
            p["request_id"] = r.request_id
            p["grounded"] = r.grounded
            p["guardrail_flags"] = r.guardrail_flags or []
            p["prompt_tokens"] = r.prompt_tokens
            p["completion_tokens"] = r.completion_tokens
            p["total_tokens"] = r.total_tokens
            p["reasoning_tokens"] = r.reasoning_tokens or 0
            p["embedding_tokens"] = r.embedding_tokens or 0
            p["guardrail_tokens"] = r.guardrail_tokens or 0
            p["cost_usd"] = r.cost_usd
            p["token_details"] = r.token_details
            if p["created_at"] is None:
                p["created_at"] = r.created_at

    out = []
    for p in pairs.values():
        details = p["token_details"] or {}
        outcome_hint = details.get("outcome")
        flags = p["guardrail_flags"] or []

        is_conversational = (
            outcome_hint == "conversational"
            or (detect_conversational_intent(p["query"]) is not None)
            or (
                p["answer"]
                in (
                    GREETING_RESPONSE,
                    THANKS_RESPONSE,
                    FAREWELL_RESPONSE,
                    CAPABILITY_RESPONSE,
                )
            )
        )
        has_guardrail_block = (
            outcome_hint in ("blocked", "llm_failed", "empty_llm_output")
            or bool(flags)
            or (
                p["grounded"] is False
                and (p["prompt_tokens"] is None or p["prompt_tokens"] == 0)
            )
        )

        if is_conversational:
            # Prefixed small talk / conversational canned response: zero LLM tokens and zero cost
            in_tok = 0
            out_tok = 0
            tot_tok = 0
            reasoning_tok = 0
            visible_tok = 0
            q_embed_tok = 0
            g_embed_tok = 0
            cost = 0.0
            cost_query_embed = 0.0
            cost_guardrail_embed = 0.0
            cost_prompt = 0.0
            cost_reasoning = 0.0
            cost_visible = 0.0
            outcome = "conversational"
            is_actual = True
        elif has_guardrail_block and (
            p["prompt_tokens"] is None or p["prompt_tokens"] == 0
        ):
            # Guardrail blocked response without an LLM call: zero LLM tokens and zero LLM cost
            in_tok = 0
            out_tok = 0
            tot_tok = 0
            reasoning_tok = 0
            visible_tok = 0
            g_embed_tok = 0
            if any(
                f in flags
                for f in ("unclear_input", "spam_detected", "unsafe_input_detected")
            ):
                q_embed_tok = 0
                cost = 0.0
            else:
                q_embed_tok = p["embedding_tokens"] or _est_tokens(p["query"])
                cost = (q_embed_tok * 0.02) / 1e6
            cost_query_embed = cost
            cost_guardrail_embed = 0.0
            cost_prompt = 0.0
            cost_reasoning = 0.0
            cost_visible = 0.0
            outcome = outcome_hint or "blocked"
            is_actual = True
        elif p["cost_usd"] is not None and (
            p["prompt_tokens"] is not None or p["completion_tokens"] is not None
        ):
            cost = float(p["cost_usd"])
            in_tok = p["prompt_tokens"] or 0
            out_tok = p["completion_tokens"] or 0
            tot_tok = p["total_tokens"] or (in_tok + out_tok)
            reasoning_tok = p["reasoning_tokens"] or 0
            visible_tok = max(0, out_tok - reasoning_tok)
            q_embed_tok = p["embedding_tokens"] or 0
            g_embed_tok = p["guardrail_tokens"] or 0
            is_actual = True
            costs_map = details.get("costs_usd", {})
            cost_query_embed = costs_map.get("query_embed", (q_embed_tok * 0.02) / 1e6)
            cost_guardrail_embed = costs_map.get(
                "guardrail_embed", (g_embed_tok * 0.02) / 1e6
            )
            cost_prompt = costs_map.get("prompt", (in_tok * 0.25) / 1e6)
            cost_reasoning = costs_map.get("reasoning", (reasoning_tok * 2.0) / 1e6)
            cost_visible = costs_map.get("visible", (visible_tok * 2.0) / 1e6)
            outcome = outcome_hint or ("answered" if p["grounded"] else "fallback")
        elif p["prompt_tokens"] is not None and p["completion_tokens"] is not None:
            in_tok = p["prompt_tokens"]
            out_tok = p["completion_tokens"]
            tot_tok = p["total_tokens"] or (in_tok + out_tok)
            reasoning_tok = p["reasoning_tokens"] or 0
            visible_tok = max(0, out_tok - reasoning_tok)
            q_embed_tok = p["embedding_tokens"] or 0
            g_embed_tok = p["guardrail_tokens"] or 0
            cost, details = compute_exchange_cost(
                prompt_tokens=in_tok,
                completion_tokens=out_tok,
                reasoning_tokens=reasoning_tok,
                query_embed_tokens=q_embed_tok,
                guardrail_embed_tokens=g_embed_tok,
            )
            is_actual = True
            costs_map = details.get("costs_usd", {})
            cost_query_embed = costs_map.get("query_embed", 0.0)
            cost_guardrail_embed = costs_map.get("guardrail_embed", 0.0)
            cost_prompt = costs_map.get("prompt", 0.0)
            cost_reasoning = costs_map.get("reasoning", 0.0)
            cost_visible = costs_map.get("visible", 0.0)
            outcome = outcome_hint or ("answered" if p["grounded"] else "fallback")
        else:
            in_tok = _est_tokens(p["query"])
            out_tok = _est_tokens(p["answer"])
            tot_tok = in_tok + out_tok
            reasoning_tok = 0
            visible_tok = out_tok
            q_embed_tok = _est_tokens(p["query"])
            g_embed_tok = 0
            cost, details = compute_exchange_cost(
                prompt_tokens=in_tok,
                completion_tokens=out_tok,
                query_embed_tokens=q_embed_tok,
            )
            is_actual = False
            costs_map = details.get("costs_usd", {})
            cost_query_embed = costs_map.get("query_embed", 0.0)
            cost_guardrail_embed = 0.0
            cost_prompt = costs_map.get("prompt", 0.0)
            cost_reasoning = 0.0
            cost_visible = costs_map.get("visible", 0.0)
            outcome = outcome_hint or ("answered" if p["grounded"] else "fallback")

        out.append(
            {
                **p,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": tot_tok,
                "reasoning_tokens": reasoning_tok,
                "visible_tokens": visible_tok,
                "query_embed_tokens": q_embed_tok,
                "guardrail_embed_tokens": g_embed_tok,
                "cost_usd": round(cost, 6),
                "cost_query_embed": float(cost_query_embed),
                "cost_guardrail_embed": float(cost_guardrail_embed),
                "cost_prompt": float(cost_prompt),
                "cost_reasoning": float(cost_reasoning),
                "cost_visible": float(cost_visible),
                "outcome": outcome,
                "is_actual": is_actual,
            }
        )
    return out


@router.get("/usage/llm")
async def usage_llm(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    settings = get_settings()

    async def compute() -> dict:
        lat = await _latency_stats(db)
        query_rows = await _query_usage_rows(db)
        in_tokens = sum(q["input_tokens"] for q in query_rows)
        out_tokens = sum(q["output_tokens"] for q in query_rows)
        cost = sum(q["cost_usd"] for q in query_rows)
        failed = sum(1 for q in query_rows if q["grounded"] is False)
        actual_count = sum(1 for q in query_rows if q["is_actual"])
        requests = lat["requests"]
        return {
            "provider": "Azure OpenAI",
            "model": settings.azure_deployment_name,
            "requests": requests,
            "successful": max(requests - failed, 0),
            "failed": failed,
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "total_tokens": in_tokens + out_tokens,
            "average_latency_ms": _r(lat["llm_ms"]),
            "cost": f"${cost:.4f}",
            # How many of the queries counted above used the real Azure usage
            # numbers vs. the legacy text-length estimate (pre-migration rows
            # or exchanges with no actual LLM call).
            "token_accounting": {
                "actual": actual_count,
                "estimated": len(query_rows) - actual_count,
            },
        }

    # Per-exchange cost accounting runs in Python over every exchange; the
    # all-time totals are cached briefly and concurrent callers share one run.
    return await aggregation_cache.get_or_compute(
        _usage_card_key("llm"),
        compute,
        ttl=settings.usage_summary_cache_ttl_seconds,
    )


@router.get("/usage/embedding")
async def usage_embedding(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    settings = get_settings()

    lat = await _latency_stats(db)
    docs = await db.scalar(select(func.count(Document.id)))
    chunks = await db.scalar(select(func.count(Chunk.id)))
    chunk_chars = await db.scalar(
        select(func.coalesce(func.sum(func.length(Chunk.content)), 0))
    )
    # SQL mirror of sum(_est_tokens(content)) over user turns: 0 for empty,
    # else max(1, chars // 4). No message bodies leave the database.
    content = ChatMessageRecord.content
    q_count, q_tokens = (
        await db.execute(
            select(
                func.count(ChatMessageRecord.id),
                func.coalesce(
                    func.sum(
                        case(
                            (or_(content.is_(None), content == ""), 0),
                            else_=func.greatest(1, func.length(content) / 4),
                        )
                    ),
                    0,
                ),
            ).where(ChatMessageRecord.role == "user")
        )
    ).one()
    tokens = int((chunk_chars or 0) // 4) + int(q_tokens)
    return {
        "provider": "Azure OpenAI",
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dim,
        "documents": docs or 0,
        "chunks": chunks or 0,
        "requests": int(q_count) + (chunks or 0),
        "input_tokens": tokens,
        "average_latency_ms": _r(lat["embedding_ms"]),
        "cost": f"${tokens / 1e6 * 0.02:.4f}",
    }


@router.get("/usage/reranker")
async def usage_reranker(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    settings = get_settings()

    lat = await _latency_stats(db)
    avg_ms = lat["reranker_ms"]
    pool = max(
        settings.default_top_k * settings.retrieval_candidate_multiplier,
        settings.retrieval_candidate_min,
    )
    cpu = ram = None
    try:
        import psutil  # noqa: PLC0415 - optional at runtime; import failure is tolerated

        def _sample() -> tuple[float, int]:
            rss = psutil.Process().memory_info().rss
            return psutil.cpu_percent(None), round(rss / 1024 / 1024)

        cpu, ram = await asyncio.to_thread(_sample)
    except Exception:
        logger.debug("psutil_metrics_unavailable", exc_info=True)
    return {
        "provider": "Local cross-encoder" if settings.enable_reranker else "Disabled",
        "model": settings.reranker_model,
        "queries": lat["reranker_requests"],
        "requests": lat["reranker_requests"],
        "documents_per_query": pool,
        "top_n": settings.default_top_k,
        "average_latency_ms": _r(avg_ms),
        "throughput": f"{pool * 1000 / avg_ms:.1f} docs/s" if avg_ms else None,
        "cpu_percent": cpu,
        "ram_mb": ram,
        "active_requests": METRICS["active_queries"],
        "errors": METRICS["errors"],
    }


@router.get("/usage/metadata")
async def usage_metadata(
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    settings = get_settings()

    return {
        "llm": {"provider": "Azure OpenAI", "model": settings.azure_deployment_name},
        "embedding": {
            "provider": "Azure OpenAI",
            "model": settings.embedding_model,
            "dimensions": settings.embedding_dim,
        },
        "reranker": {
            "provider": "Local cross-encoder"
            if settings.enable_reranker
            else "Disabled",
            "model": settings.reranker_model,
        },
        "retrieval": {
            "top_k": settings.default_top_k,
            "similarity": "Cosine (pgvector)",
        },
        "chunking": {
            "strategy": "Structure-aware",
            "chunk_size": "≤ 4000 chars",
            "overlap": "None (hash-based incremental)",
        },
    }


@router.get("/usage/test-ips")
async def get_test_ips_endpoint(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    """Return all configured test/development IPs and overrides."""
    return await get_test_ip_details(db)


@router.post("/usage/test-ips")
async def add_test_ip_endpoint(
    payload: TestIpPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.TESTIP_WRITE)),
):
    """Add a new test/development IP override (Task 2.2)."""
    return await add_or_update_test_ip(
        db,
        payload.ip_address,
        description=payload.description,
        actor=user.email,
        request_id=getattr(request.state, "request_id", None),
        client_ip=get_client_ip(request),
    )


@router.delete("/usage/test-ips/{ip_address:path}")
async def delete_test_ip_endpoint(
    ip_address: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.TESTIP_WRITE)),
):
    """Remove a test/development IP override (Task 2.2)."""
    deleted = await delete_test_ip(
        db,
        ip_address,
        actor=user.email,
        request_id=getattr(request.state, "request_id", None),
        client_ip=get_client_ip(request),
    )
    return {"deleted": deleted, "ip_address": ip_address}


@router.get("/usage/cost-breakdown")
async def usage_cost_breakdown(
    from_date: str | None = None,
    to_date: str | None = None,
    ip: str | None = None,
    session_id: str | None = None,
    q: str | None = None,
    outcome: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    """Multi-tier token and cost breakdown across 4 visibility levels (Task 2.5).

    Level 1: Overall platform metrics by stage + ingestion overhead
    Level 2: All Users (grouped by client IP) with granular stage tokens and costs
    Level 3: All Sessions with stage tokens and cost
    Level 4: All Queries with filters (?ip=, ?session_id=, ?q=, ?outcome=) and pagination
    """
    effective_test_ips = await get_effective_test_ips(db)
    rows = await _query_usage_rows(db)

    # Convert and prepare query records
    all_queries = []
    for r in rows:
        created_dt = r["created_at"]
        all_queries.append(
            {
                "session_id": r["session_id"],
                "session_title": r["session_title"],
                "user_email": r["user_email"],
                "ip_address": r["ip_address"],
                "is_test": is_test_ip(r["ip_address"], effective_test_ips),
                "query": (r["query"] or "")[:160],
                "full_query": r["query"] or "",
                "answer": (r["answer"] or "")[:200],
                "request_id": r["request_id"],
                "created_at": created_dt.isoformat() + "Z" if created_dt else None,
                "created_dt": created_dt,
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "total_tokens": r["total_tokens"],
                "reasoning_tokens": r.get("reasoning_tokens", 0),
                "visible_tokens": r.get("visible_tokens", 0),
                "query_embed_tokens": r.get("query_embed_tokens", 0),
                "guardrail_embed_tokens": r.get("guardrail_embed_tokens", 0),
                "cost_usd": r["cost_usd"],
                "cost_query_embed": r.get("cost_query_embed", 0.0),
                "cost_guardrail_embed": r.get("cost_guardrail_embed", 0.0),
                "cost_prompt": r.get("cost_prompt", 0.0),
                "cost_reasoning": r.get("cost_reasoning", 0.0),
                "cost_visible": r.get("cost_visible", 0.0),
                "outcome": r.get("outcome", "answered"),
                "grounded": r["grounded"],
                "guardrail_flags": r.get("guardrail_flags", []),
                "token_details": r.get("token_details"),
                "is_actual": r["is_actual"],
            }
        )

    # Filter queries by date, ip, session_id, q, outcome
    filtered = all_queries
    if from_date:
        try:
            f_dt = datetime.fromisoformat(from_date.replace("Z", ""))
            filtered = [
                item
                for item in filtered
                if item["created_dt"] and item["created_dt"] >= f_dt
            ]
        except ValueError:
            pass
    if to_date:
        try:
            t_dt = datetime.fromisoformat(to_date.replace("Z", ""))
            filtered = [
                item
                for item in filtered
                if item["created_dt"] and item["created_dt"] <= t_dt
            ]
        except ValueError:
            pass
    if ip:
        filtered = [item for item in filtered if item["ip_address"] == ip]
    if session_id:
        filtered = [item for item in filtered if item["session_id"] == session_id]
    if outcome:
        filtered = [item for item in filtered if item["outcome"] == outcome]
    if q:
        q_lower = q.lower()
        filtered = [
            item
            for item in filtered
            if q_lower in item["full_query"].lower()
            or q_lower in item["answer"].lower()
        ]

    filtered.sort(key=lambda item: item["created_dt"] or datetime.min, reverse=True)

    # Ingestion overhead from audit_log
    ingest_audit = (
        (
            await db.execute(
                select(AuditLog.detail).where(
                    AuditLog.action.in_(["ingest", "document_ingest"])
                )
            )
        )
        .scalars()
        .all()
    )
    platform_ingest_tokens = 0
    platform_ingest_cost = 0.0
    for d in ingest_audit:
        if isinstance(d, dict):
            platform_ingest_tokens += int(d.get("embedding_tokens", 0) or 0)
            platform_ingest_cost += float(d.get("embedding_cost_usd", 0.0) or 0.0)

    # Level 1: Overall
    overall_total_cost = sum(item["cost_usd"] for item in filtered)
    overall_queries = len(filtered)
    overall = {
        "total_queries": overall_queries,
        "total_cost_usd": round(overall_total_cost, 6),
        "avg_cost_per_query_usd": round(overall_total_cost / overall_queries, 6)
        if overall_queries
        else 0.0,
        "stages_tokens": {
            "query_embed": sum(item["query_embed_tokens"] for item in filtered),
            "guardrail_embed": sum(item["guardrail_embed_tokens"] for item in filtered),
            "total_embed": sum(
                item["query_embed_tokens"] + item["guardrail_embed_tokens"]
                for item in filtered
            ),
            "prompt": sum(item["input_tokens"] for item in filtered),
            "reasoning": sum(item["reasoning_tokens"] for item in filtered),
            "visible": sum(item["visible_tokens"] for item in filtered),
            "completion": sum(item["output_tokens"] for item in filtered),
            "total_llm": sum(item["total_tokens"] for item in filtered),
            "total_billed": sum(
                item["query_embed_tokens"]
                + item["guardrail_embed_tokens"]
                + item["total_tokens"]
                for item in filtered
            ),
        },
        "stages_cost_usd": {
            "query_embed": round(sum(item["cost_query_embed"] for item in filtered), 6),
            "guardrail_embed": round(
                sum(item["cost_guardrail_embed"] for item in filtered), 6
            ),
            "prompt": round(sum(item["cost_prompt"] for item in filtered), 6),
            "reasoning": round(sum(item["cost_reasoning"] for item in filtered), 6),
            "visible": round(sum(item["cost_visible"] for item in filtered), 6),
            "total": round(overall_total_cost, 6),
        },
        "platform_overhead": {
            "ingest_embedding_tokens": platform_ingest_tokens,
            "ingest_embedding_cost_usd": round(platform_ingest_cost, 6),
        },
    }

    # Level 2: All Users (by_ip, by_test_ip, and by_account)
    def _group(key_fn, is_ip: bool = False, test_filter: str = "all"):
        groups: dict[str, dict] = {}
        for item in filtered:
            if test_filter == "user_only" and item.get("is_test"):
                continue
            if test_filter == "test_only" and not item.get("is_test"):
                continue
            key = key_fn(item) or "Unknown"
            g = groups.setdefault(
                key,
                {
                    "key": key,
                    "ip_address": key if is_ip else item["ip_address"],
                    "is_test": item.get("is_test", False) if is_ip else False,
                    "sessions": set(),
                    "queries": 0,
                    "query_embed_tokens": 0,
                    "guardrail_embed_tokens": 0,
                    "prompt_tokens": 0,
                    "reasoning_tokens": 0,
                    "visible_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "cost_query_embed": 0.0,
                    "cost_guardrail_embed": 0.0,
                    "cost_prompt": 0.0,
                    "cost_reasoning": 0.0,
                    "cost_visible": 0.0,
                },
            )
            g["sessions"].add(item["session_id"])
            g["queries"] += 1
            g["query_embed_tokens"] += item["query_embed_tokens"]
            g["guardrail_embed_tokens"] += item["guardrail_embed_tokens"]
            g["prompt_tokens"] += item["input_tokens"]
            g["reasoning_tokens"] += item["reasoning_tokens"]
            g["visible_tokens"] += item["visible_tokens"]
            g["output_tokens"] += item["output_tokens"]
            g["total_tokens"] += item["total_tokens"]
            g["cost_usd"] += item["cost_usd"]
            g["cost_query_embed"] += item["cost_query_embed"]
            g["cost_guardrail_embed"] += item["cost_guardrail_embed"]
            g["cost_prompt"] += item["cost_prompt"]
            g["cost_reasoning"] += item["cost_reasoning"]
            g["cost_visible"] += item["cost_visible"]

        out = []
        for key, g in groups.items():
            res = {
                "key": key,
                "ip_address": g["ip_address"],
                "is_test": g.get("is_test", False),
                "sessions": len(g["sessions"]),
                "queries": g["queries"],
                "input_tokens": g["prompt_tokens"],
                "prompt_tokens": g["prompt_tokens"],
                "reasoning_tokens": g["reasoning_tokens"],
                "visible_tokens": g["visible_tokens"],
                "output_tokens": g["output_tokens"],
                "query_embed_tokens": g["query_embed_tokens"],
                "guardrail_embed_tokens": g["guardrail_embed_tokens"],
                "total_tokens": g["total_tokens"],
                "cost_usd": round(g["cost_usd"], 4),
                "stages_cost_usd": {
                    "query_embed": round(g["cost_query_embed"], 6),
                    "guardrail_embed": round(g["cost_guardrail_embed"], 6),
                    "prompt": round(g["cost_prompt"], 6),
                    "reasoning": round(g["cost_reasoning"], 6),
                    "visible": round(g["cost_visible"], 6),
                },
            }
            out.append(res)
        out.sort(key=lambda x: x["cost_usd"], reverse=True)
        return out

    # Level 3: All Sessions
    sessions_map: dict[str, dict] = {}
    for item in filtered:
        sid = item["session_id"]
        s = sessions_map.setdefault(
            sid,
            {
                "session_id": sid,
                "session_title": item["session_title"],
                "user_email": item["user_email"],
                "ip_address": item["ip_address"],
                "is_test": item.get("is_test", False),
                "queries": 0,
                "query_embed_tokens": 0,
                "guardrail_embed_tokens": 0,
                "input_tokens": 0,
                "reasoning_tokens": 0,
                "visible_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "last_activity_at": None,
            },
        )
        s["queries"] += 1
        s["query_embed_tokens"] += item["query_embed_tokens"]
        s["guardrail_embed_tokens"] += item["guardrail_embed_tokens"]
        s["input_tokens"] += item["input_tokens"]
        s["reasoning_tokens"] += item["reasoning_tokens"]
        s["visible_tokens"] += item["visible_tokens"]
        s["output_tokens"] += item["output_tokens"]
        s["total_tokens"] += item["total_tokens"]
        s["cost_usd"] += item["cost_usd"]
        if item["created_dt"] and (
            s["last_activity_at"] is None or item["created_dt"] > s["last_activity_at"]
        ):
            s["last_activity_at"] = item["created_dt"]

    sessions_out = [
        {
            **{k: v for k, v in s.items() if k != "last_activity_at"},
            "cost_usd": round(s["cost_usd"], 4),
            "last_activity_at": s["last_activity_at"].isoformat() + "Z"
            if s["last_activity_at"]
            else None,
        }
        for s in sessions_map.values()
    ]
    sessions_out.sort(key=lambda s: s["last_activity_at"] or "", reverse=True)

    # Level 4: All Queries (paginated)
    safe_limit = max(1, min(limit, 200))
    safe_offset = max(0, offset)
    paginated_queries = [
        {k: v for k, v in item.items() if k not in ("created_dt", "full_query")}
        for item in filtered[safe_offset : safe_offset + safe_limit]
    ]

    actual_count = sum(1 for item in filtered if item["is_actual"])

    return {
        "overall": overall,
        "by_account": _group(
            lambda item: item["user_email"], is_ip=False, test_filter="all"
        ),
        "by_ip": _group(
            lambda item: item["ip_address"], is_ip=True, test_filter="user_only"
        ),
        "by_test_ip": _group(
            lambda item: item["ip_address"], is_ip=True, test_filter="test_only"
        ),
        "test_ips": sorted(effective_test_ips),
        "sessions": sessions_out,
        "queries": paginated_queries,
        "total_queries": len(filtered),
        "limit": safe_limit,
        "offset": safe_offset,
        "token_accounting": {
            "actual": actual_count,
            "estimated": len(filtered) - actual_count,
        },
    }


@router.get("/usage/queries/{request_id}")
async def get_query_usage_detail(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
):
    """Subtask 2.5.5: Return full single-query record with stage tokens, pricing snapshot, and latency."""
    # 1. Fetch assistant message and session
    result = await db.execute(
        select(ChatMessageRecord, ChatSession)
        .join(ChatSession, ChatMessageRecord.session_id == ChatSession.id)
        .where(
            ChatMessageRecord.request_id == request_id,
            ChatMessageRecord.role == "assistant",
        )
    )
    row = result.first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Query with request_id '{request_id}' not found",
        )
    assistant_msg, session = row

    # 2. Fetch corresponding user message in the same turn
    user_res = await db.execute(
        select(ChatMessageRecord).where(
            ChatMessageRecord.session_id == session.id,
            ChatMessageRecord.turn_index == assistant_msg.turn_index,
            ChatMessageRecord.role == "user",
        )
    )
    user_msg = user_res.scalar_one_or_none()

    # 3. Fetch latency metrics
    lat_res = await db.execute(
        select(LatencyRecord).where(LatencyRecord.request_id == request_id)
    )
    lat_record = lat_res.scalar_one_or_none()
    latency_dict = (
        {
            "total_ms": lat_record.total_ms,
            "embedding_ms": lat_record.embedding_ms,
            "retrieval_ms": lat_record.retrieval_ms,
            "guardrail_pre_ms": lat_record.guardrail_pre_ms,
            "llm_ms": lat_record.llm_ms,
            "guardrail_post_ms": lat_record.guardrail_post_ms,
            "reranker_ms": lat_record.reranker_ms,
        }
        if lat_record
        else None
    )

    in_tok = assistant_msg.prompt_tokens or 0
    out_tok = assistant_msg.completion_tokens or 0
    reasoning_tok = assistant_msg.reasoning_tokens or 0
    visible_tok = max(0, out_tok - reasoning_tok)
    q_embed_tok = assistant_msg.embedding_tokens or 0
    g_embed_tok = assistant_msg.guardrail_tokens or 0

    return {
        "request_id": request_id,
        "session_id": str(session.id),
        "session_title": session.title,
        "user_email": session.user_email,
        "ip_address": (user_msg.ip_address if user_msg else None)
        or session.created_ip
        or "Unknown",
        "query": user_msg.content if user_msg else "",
        "answer": assistant_msg.content,
        "grounded": assistant_msg.grounded,
        "guardrail_flags": assistant_msg.guardrail_flags or [],
        "created_at": assistant_msg.created_at.isoformat() + "Z"
        if assistant_msg.created_at
        else None,
        "tokens": {
            "query_embed": q_embed_tok,
            "guardrail_embed": g_embed_tok,
            "total_embed": q_embed_tok + g_embed_tok,
            "prompt": in_tok,
            "reasoning": reasoning_tok,
            "visible": visible_tok,
            "completion": out_tok,
            "total_llm": assistant_msg.total_tokens or (in_tok + out_tok),
            "total_billed": q_embed_tok
            + g_embed_tok
            + (assistant_msg.total_tokens or (in_tok + out_tok)),
        },
        "cost_usd": assistant_msg.cost_usd,
        "token_details": assistant_msg.token_details or {},
        "latency": latency_dict,
    }


# ============================================================================
# DASHBOARD
# ============================================================================


_DASHBOARD_ERRORS: dict[int | str, dict[str, Any]] = {
    304: {"description": "ETag matched (`If-None-Match`)."},
    401: {"description": "Not authenticated."},
    403: {"description": "Missing `dashboard:read`."},
    422: {"description": "Invalid audience, window or limit."},
}


@router.get(
    "/dashboard",
    summary="Dashboard KPIs, sparklines and chart series",
    responses={200: {"model": DashboardData}, **_DASHBOARD_ERRORS},
)
async def admin_dashboard(
    request: Request,
    audience: Audience = Depends(audience_scope),
    window_from: datetime | None = Query(
        None,
        alias="from",
        description="Window start, ISO 8601 (UTC when no offset), inclusive.",
    ),
    window_to: datetime | None = Query(
        None,
        alias="to",
        description="Window end, ISO 8601 (UTC when no offset), exclusive.",
    ),
    db: AsyncSession = Depends(get_db),
    runner: ScopedRunner = Depends(get_scoped_runner),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> Response:
    """Dashboard KPIs; weak ETag + `Cache-Control: private` (304 on match).

    Without `from`/`to` the KPIs keep their legacy windows (all time, last
    24 h, since process start). With a window the KPI totals are computed for
    it and the response adds `window`, `sparklines`, `deltas` (vs the
    equal-length previous window), `cost_usd`, `queries_by_outcome`,
    `latency_series`, `cost` (current UTC month), `satisfaction` and
    `top_intents`. `to <= from` or a span above `USAGE_MAX_RANGE_DAYS` is 422.
    """
    now = dashboard_window.utcnow()
    window = dashboard_window.resolve_window(
        window_from,
        window_to,
        now=now,
        max_days=get_settings().usage_max_range_days,
    )
    if window is None:
        return cached_response(request, await _dashboard_payload(db, audience))
    payload = {
        **await _dashboard_panels(db, audience),
        **await dashboard_service.window_payload(runner, audience, window, now),
    }
    return cached_response(request, payload)


@router.get(
    "/dashboard/activity",
    summary="Live activity feed (newest audit events)",
    responses={200: {"model": ActivityFeed}, **_DASHBOARD_ERRORS},
)
async def admin_dashboard_activity(
    request: Request,
    audience: Audience = Depends(audience_scope),
    limit: int = Query(
        dashboard_activity.DEFAULT_LIMIT, ge=1, le=dashboard_activity.MAX_LIMIT
    ),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> Response:
    """Newest query, feedback, error, guardrail, document and admin events.

    Summaries are PII-free (no emails, IPs or query text); auth events are
    never listed. Weak ETag, so an unchanged feed polls as a 304.
    """
    items = await dashboard_activity.recent_activity(db, audience, limit)
    feed = ActivityFeed.model_validate(
        {
            "items": items,
            "generated_at": dashboard_window.iso_z(dashboard_window.utcnow()),
        }
    )
    return cached_response(request, feed.model_dump(mode="json"))


async def _dashboard_panels(db: AsyncSession, audience: str) -> dict:
    """The non-KPI dashboard fields (pipeline, governance text, trends)."""
    since = datetime.utcnow() - timedelta(hours=24)
    return {
        "pipeline": await _pipeline_status(db),
        "governance": await _governance_status(db, since),
        "query_trend": await _query_trend(db, audience),
        "latency_breakdown": await _latency_breakdown(db, audience),
    }


async def _dashboard_payload(db: AsyncSession, audience: str) -> dict:
    since = datetime.utcnow() - timedelta(hours=24)
    aud_sess = await build_audience_filter(db, ChatSession.created_ip, audience)

    # 1. Total users: distinct IPs from ChatSession
    q_users = select(func.count(func.distinct(ChatSession.created_ip))).where(
        ChatSession.created_ip.isnot(None)
    )
    if aud_sess is not None:
        q_users = q_users.where(aud_sess)
    users = await db.scalar(q_users)

    # 2. Active users: distinct IPs active in last 24h
    q_active = select(func.count(func.distinct(ChatSession.created_ip))).where(
        ChatSession.created_ip.isnot(None),
        ChatSession.last_activity_at >= since,
    )
    if aud_sess is not None:
        q_active = q_active.where(aud_sess)
    active_users = await db.scalar(q_active)

    # 3. Sessions & Conversations
    q_sessions = select(func.count(ChatSession.id))
    if aud_sess is not None:
        q_sessions = q_sessions.where(aud_sess)
    sessions = await db.scalar(q_sessions)
    conversations = sessions

    # 4. User queries
    client_ip_col = func.coalesce(ChatMessageRecord.ip_address, ChatSession.created_ip)
    aud_msg = await build_audience_filter(db, client_ip_col, audience)
    q_queries = (
        select(func.count(ChatMessageRecord.id))
        .select_from(ChatMessageRecord)
        .outerjoin(ChatSession, ChatMessageRecord.session_id == ChatSession.id)
        .where(ChatMessageRecord.role == "user")
    )
    if aud_msg is not None:
        q_queries = q_queries.where(aud_msg)
    queries = await db.scalar(q_queries)

    # 5. Feedback
    aud_aud = await build_audience_filter(db, AuditLog.ip_address, audience)
    q_feedback = select(func.count(AuditLog.id)).where(
        AuditLog.action.ilike("%feedback%")
    )
    if aud_aud is not None:
        q_feedback = q_feedback.where(aud_aud)
    feedback = await db.scalar(q_feedback)

    # 6. Latency
    q_lat = select(func.avg(LatencyRecord.total_ms))
    if audience != "overview":
        aud_filter = await build_audience_filter(db, AuditLog.ip_address, audience)
        if aud_filter is not None:
            q_lat = (
                select(func.avg(LatencyRecord.total_ms))
                .select_from(LatencyRecord)
                .join(
                    AuditLog,
                    (LatencyRecord.request_id == AuditLog.request_id)
                    & (AuditLog.action == QUERY_ACTION),
                )
                .where(aud_filter)
            )
    avg_latency = await db.scalar(q_lat)
    requests_total = METRICS["requests"]

    return {
        "users": users or 0,
        "active_users": active_users or 0,
        "sessions": sessions or 0,
        "conversations": conversations or 0,
        "queries": queries or 0,
        "feedback": feedback or 0,
        "avg_latency_ms": float(avg_latency or 0),
        "error_rate": METRICS["errors"] * 100 / requests_total
        if requests_total
        else 0.0,
        **await _dashboard_panels(db, audience),
    }


async def _query_trend(db: AsyncSession, audience: str = "overview") -> list[dict]:
    """Daily user-query counts for the last 7 days, oldest first."""
    since = datetime.utcnow() - timedelta(days=7)
    client_ip_col = func.coalesce(ChatMessageRecord.ip_address, ChatSession.created_ip)
    aud_filter = await build_audience_filter(db, client_ip_col, audience)
    q = (
        select(ChatMessageRecord.created_at)
        .select_from(ChatMessageRecord)
        .outerjoin(ChatSession, ChatMessageRecord.session_id == ChatSession.id)
        .where(
            ChatMessageRecord.role == "user",
            ChatMessageRecord.created_at >= since,
        )
    )
    if aud_filter is not None:
        q = q.where(aud_filter)
    rows = (await db.execute(q)).scalars().all()

    counts: dict[str, int] = {}
    today = datetime.utcnow().date()
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        counts[day.isoformat()] = 0
    for created_at in rows:
        key = created_at.date().isoformat()
        if key in counts:
            counts[key] += 1

    return [{"date": day, "queries": count} for day, count in counts.items()]


async def _latency_breakdown(
    db: AsyncSession, audience: str = "overview"
) -> list[dict]:
    """Average ms per pipeline stage over the last 24h, for stacked/bar charts."""
    since = datetime.utcnow() - timedelta(hours=24)
    q = select(LatencyRecord).where(LatencyRecord.created_at >= since)
    if audience != "overview":
        aud_filter = await build_audience_filter(db, AuditLog.ip_address, audience)
        if aud_filter is not None:
            q = (
                select(LatencyRecord)
                .join(
                    AuditLog,
                    (LatencyRecord.request_id == AuditLog.request_id)
                    & (AuditLog.action == QUERY_ACTION),
                )
                .where(LatencyRecord.created_at >= since, aud_filter)
            )
    rows = (await db.execute(q)).scalars().all()

    def avg(field: str) -> float:
        vals = [getattr(r, field) for r in rows if getattr(r, field) is not None]
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    return [
        {"stage": "Embedding", "ms": avg("embedding_ms")},
        {"stage": "Retrieval", "ms": avg("retrieval_ms")},
        {"stage": "Reranker", "ms": avg("reranker_ms")},
        {"stage": "LLM", "ms": avg("llm_ms")},
    ]


async def _pipeline_status(db: AsyncSession) -> dict:

    settings = get_settings()
    since = datetime.utcnow() - timedelta(hours=24)
    rows = (
        (
            await db.execute(
                select(LatencyRecord).where(LatencyRecord.created_at >= since)
            )
        )
        .scalars()
        .all()
    )

    def avg_ms(field: str) -> float | None:
        vals = [getattr(r, field) for r in rows if getattr(r, field) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    # Raw numbers only — no model/provider names, no pre-formatted text — so
    # the frontend renders the unit (seconds) consistently and never leaks
    # which model is behind each stage.
    return {
        "embedding_ms": avg_ms("embedding_ms"),
        "retrieval_ms": avg_ms("retrieval_ms"),
        "reranker_enabled": settings.enable_reranker,
        "reranker_ms": avg_ms("reranker_ms"),
        "llm_ms": avg_ms("llm_ms"),
    }


async def _governance_status(db: AsyncSession, since: datetime) -> dict:
    latency_24h = await db.scalar(
        select(func.count(LatencyRecord.id)).where(LatencyRecord.created_at >= since)
    )
    audit_total = await db.scalar(select(func.count(AuditLog.id)))
    flagged = await db.scalar(
        select(func.count(ChatMessageRecord.id)).where(
            ChatMessageRecord.guardrail_flags.is_not(None),
            ChatMessageRecord.created_at >= since,
        )
    )
    return {
        "monitoring": f"{METRICS['requests']:,} requests since start",
        "tracing": f"{latency_24h or 0:,} traces (24h)",
        "audit_logs": f"{audit_total or 0:,} entries",
        "guardrails": f"{flagged or 0:,} responses with flags (24h)",
    }
