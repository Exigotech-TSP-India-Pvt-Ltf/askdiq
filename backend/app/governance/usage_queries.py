"""Shared SQL query builders and sanitization helpers for Usage & Cost Analytics (Task 3.1).

Builds index-optimized selectables pairing user questions and assistant answers
by (session_id, turn_index), joined to chat_sessions and latency_metrics.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Select,
    String,
    and_,
    cast,
    func,
    or_,
    select,
)

from app.governance.audience import Audience, build_audience_filter
from app.models.db_models import (
    ChatSession,
    LatencyRecord,
)

# Allow-lists for SQL sorting to prevent arbitrary order injection
USAGE_SORT_COLUMNS = {
    "created_at_desc": ("created_at", False),
    "created_at_asc": ("created_at", True),
    "cost_desc": ("cost_usd", False),
    "cost_asc": ("cost_usd", True),
    "tokens_desc": ("total_tokens", False),
    "tokens_asc": ("total_tokens", True),
    "queries_desc": ("queries", False),
    "queries_asc": ("queries", True),
    "last_active_desc": ("last_active", False),
    "last_active_asc": ("last_active", True),
}


def escape_ilike_pattern(text: str) -> str:
    """Escape special characters in user search inputs for SQL ILIKE queries."""
    if not text:
        return ""
    if "\x00" in text:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422, detail="Search query contains prohibited null characters."
        )
    # Escape backslash first, then wildcard percent and underscore
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_turns_base_query(
    user_alias: Any,
    asst_alias: Any,
) -> Select:
    """Build the canonical pair selectable joining user and assistant messages for each turn."""
    return (
        select(
            user_alias.session_id.label("session_id"),
            user_alias.turn_index.label("turn_index"),
            user_alias.content.label("query"),
            asst_alias.content.label("answer"),
            user_alias.created_at.label("created_at"),
            func.coalesce(
                user_alias.ip_address, ChatSession.created_ip, "Unknown"
            ).label("ip_address"),
            ChatSession.title.label("session_title"),
            asst_alias.id.label("assistant_message_id"),
            asst_alias.request_id.label("request_id"),
            asst_alias.outcome.label("outcome"),
            asst_alias.grounded.label("grounded"),
            asst_alias.guardrail_flags.label("guardrail_flags"),
            func.coalesce(asst_alias.prompt_tokens, 0).label("prompt_tokens"),
            func.coalesce(asst_alias.completion_tokens, 0).label("completion_tokens"),
            func.coalesce(asst_alias.total_tokens, 0).label("total_tokens"),
            func.coalesce(asst_alias.reasoning_tokens, 0).label("reasoning_tokens"),
            func.coalesce(asst_alias.embedding_tokens, 0).label("query_embed_tokens"),
            func.coalesce(asst_alias.guardrail_tokens, 0).label(
                "guardrail_embed_tokens"
            ),
            func.coalesce(asst_alias.cost_usd, 0.0).label("cost_usd"),
            asst_alias.token_details.label("token_details"),
            LatencyRecord.total_ms.label("latency_ms"),
        )
        .select_from(user_alias)
        .join(
            asst_alias,
            and_(
                user_alias.session_id == asst_alias.session_id,
                user_alias.turn_index == asst_alias.turn_index,
                asst_alias.role == "assistant",
            ),
        )
        .join(ChatSession, user_alias.session_id == ChatSession.id)
        .outerjoin(LatencyRecord, asst_alias.request_id == LatencyRecord.request_id)
        .where(user_alias.role == "user")
    )


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

    # 3. Exact filters
    if ip:
        query = query.where(user_alias.ip_address == ip.strip())
    if session_id:
        try:
            import uuid

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
