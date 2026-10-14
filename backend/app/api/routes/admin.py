"""Admin endpoints for users and conversation management."""

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.middleware import METRICS
from app.governance.auth import get_current_actor
from app.models.db_models import (
    AuditLog,
    ChatMessageRecord,
    ChatSession,
    LatencyRecord,
    User,
)

router = APIRouter(prefix="/admin", tags=["admin"])


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
    db: AsyncSession = Depends(get_db),  # noqa: B008
    actor: str = Depends(get_current_actor),
):
    """Return users grouped by client IP with activity statistics."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

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

    test_ip_set = {"127.0.0.1", "::1", "localhost"}

    all_users = []
    for row in ip_rows:
        ip = row.ip
        is_test = ip in test_ip_set
        t_type = "Test" if is_test else "Production"

        if user_type and user_type.lower() != t_type.lower():
            continue

        if q and q.strip() and q.strip().lower() not in ip.lower():
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
    db: AsyncSession = Depends(get_db),  # noqa: B008
    actor: str = Depends(get_current_actor),
):
    """Return detailed information about one user."""

    # -----------------------------------------------------------------------
    # Find user
    # -----------------------------------------------------------------------
    result = await db.execute(
        select(User).where(User.id == user_id)
    )

    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # -----------------------------------------------------------------------
    # Get user's conversations
    # -----------------------------------------------------------------------
    conversations_result = await db.execute(
        select(
            ChatSession,
            func.count(ChatMessageRecord.id).label("message_count"),
        )
        .outerjoin(
            ChatMessageRecord,
            ChatMessageRecord.session_id == ChatSession.id,
        )
        .where(
            ChatSession.user_email == user.email
        )
        .group_by(ChatSession.id)
        .order_by(ChatSession.last_activity_at.desc())
    )

    conversation_rows = conversations_result.all()

    # -----------------------------------------------------------------------
    # Total messages / queries
    # -----------------------------------------------------------------------
    query_result = await db.execute(
        select(func.count(ChatMessageRecord.id))
        .join(
            ChatSession,
            ChatMessageRecord.session_id == ChatSession.id,
        )
        .where(
            ChatSession.user_email == user.email
        )
    )

    query_count = query_result.scalar() or 0

    # -----------------------------------------------------------------------
    # Most recent IP
    # -----------------------------------------------------------------------
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

    # -----------------------------------------------------------------------
    # Last activity
    # -----------------------------------------------------------------------
    last_activity = None

    if conversation_rows:
        last_activity = conversation_rows[0][0].last_activity_at

    # -----------------------------------------------------------------------
    # Response
    #
    # IMPORTANT:
    # password_hash is intentionally NOT returned.
    # -----------------------------------------------------------------------
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
        "feedback_count": 0,
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
    db: AsyncSession = Depends(get_db),  # noqa: B008
    actor: str = Depends(get_current_actor),
):
    """Return all conversations for the admin dashboard."""

    result = await db.execute(
        select(
            ChatSession,
            func.count(ChatMessageRecord.id).label("message_count"),
        )
        .outerjoin(
            ChatMessageRecord,
            ChatMessageRecord.session_id == ChatSession.id,
        )
        .group_by(ChatSession.id)
        .order_by(ChatSession.last_activity_at.desc())
    )

    rows = result.all()

    conversations = []

    for session, message_count in rows:
        conversations.append(
            {
                "id": str(session.id),
                "user_email": session.user_email,
                "title": session.title,
                "created_at": session.created_at,
                "last_activity_at": session.last_activity_at,
                "expires_at": session.expires_at,
                "created_ip": session.created_ip,
                "message_count": message_count or 0,
            }
        )

    return conversations


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    actor: str = Depends(get_current_actor),
):
    """Return one conversation and all of its messages."""

    # -----------------------------------------------------------------------
    # Find conversation
    # -----------------------------------------------------------------------
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == conversation_id
        )
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
        .where(
            ChatMessageRecord.session_id == conversation.id
        )
        .order_by(
            ChatMessageRecord.turn_index.asc(),
            ChatMessageRecord.created_at.asc(),
        )
    )

    messages = messages_result.scalars().all()

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
    }

# ============================================================================
# GOVERNANCE MONITORING / TRACES / LOGS
# ============================================================================


def _pctl(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]  # noqa: RUF046


@router.get("/governance/monitoring")
async def governance_monitoring(
    db: AsyncSession = Depends(get_db),  # noqa: B008
    actor: str = Depends(get_current_actor),
):
    since = datetime.utcnow() - timedelta(hours=24)  # noqa: DTZ003
    records = (
        await db.execute(select(LatencyRecord).where(LatencyRecord.created_at >= since))
    ).scalars().all()
    totals = [r.total_ms for r in records]

    def avg(field: str) -> float | None:
        vals = [getattr(r, field) for r in records if getattr(r, field) is not None]
        return sum(vals) / len(vals) if vals else None

    active_users = await db.scalar(
        select(func.count(func.distinct(ChatSession.user_email))).where(
            ChatSession.last_activity_at >= since
        )
    )

    cpu = ram_mb = ram_pct = None
    try:
        import psutil

        proc = psutil.Process()
        cpu = psutil.cpu_percent(interval=0.3)  # system-wide; process-only CPU is ~0 when idle
        ram_mb = proc.memory_info().rss / 1024 / 1024
        ram_pct = proc.memory_percent()
    except Exception:  # noqa: BLE001, S110
        pass

    from app.core.quota import quota_monitor
    from app.governance.online_eval import quality_tracker

    quota_data = quota_monitor.get_summary()
    online_kpis = quality_tracker.get_kpis()

    requests_total = METRICS["requests"]
    return {
        "window_hours": 24,
        "azure_quota_telemetry": quota_data,
        "online_quality_kpis": online_kpis,
        "api_requests": requests_total,
        "queries_24h": len(records),
        "active_users": active_users or 0,
        "active_queries": METRICS["active_queries"],
        "avg_latency_ms": sum(totals) / len(totals) if totals else None,
        "p95_latency_ms": _pctl(totals, 0.95),
        "error_rate": METRICS["errors"] / requests_total if requests_total else 0.0,
        "embedding_latency_ms": avg("embedding_ms"),
        "retrieval_latency_ms": avg("retrieval_ms"),
        "llm_latency_ms": avg("llm_ms"),
        "reranker_latency_ms": avg("reranker_ms"),
        "process_cpu_percent": cpu,
        "process_ram_mb": ram_mb,
        "process_ram_percent": ram_pct,
    }


@router.get("/governance/traces")
async def governance_traces(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    actor: str = Depends(get_current_actor),
):
    records = (
        await db.execute(
            select(LatencyRecord).order_by(LatencyRecord.created_at.desc()).limit(min(limit, 500))
        )
    ).scalars().all()
    ids = [r.request_id for r in records]
    audits = {
        a.request_id: a
        for a in (
            await db.execute(select(AuditLog).where(AuditLog.request_id.in_(ids)))
        ).scalars().all()
    } if ids else {}
    convos = dict(
        (await db.execute(
            select(ChatMessageRecord.request_id, ChatMessageRecord.session_id).where(
                ChatMessageRecord.request_id.in_(ids)
            )
        )).all()
    ) if ids else {}
    return [
        {
            "request_id": r.request_id,
            "trace_id": r.request_id,
            "user_id": audits[r.request_id].actor if r.request_id in audits else None,
            "conversation_id": str(convos[r.request_id]) if r.request_id in convos else None,
            "ip_address": audits[r.request_id].ip_address if r.request_id in audits else None,
            "timestamp": r.created_at.isoformat() + "Z",
            "status": "ok",
            "total_ms": r.total_ms,
        }
        for r in records
    ]


# Guardrail flag -> log severity, from most to least severe. Anything not
# listed here (e.g. a normal, unflagged, grounded query) is INFO.
_CRITICAL_FLAGS = {
    "llm_call_failed",
    "groundedness_check_failed",
    "secret_leak_detected",
}
_ALERT_FLAGS = {
    "unsafe_input_detected",
    "spam_detected",
    "pii_leak_detected",
    "online_unfaithful",
}
_ERROR_FLAGS = {"empty_llm_output", "empty_answer", "no_retrieved_sources", "unsupported_numeric_claim"}
_WARNING_FLAGS = {"low_retrieval_confidence", "possible_hallucination", "query_too_long", "empty_query"}


def _log_level_for(detail: dict | None) -> str:
    """Map a /query audit event's guardrail flags (and groundedness) to a
    log severity, so the Governance Logs page can filter by
    Critical/Alert/Error/Warning/Info instead of everything showing as the
    same level.
    """
    flags = set((detail or {}).get("guardrail_flags") or [])
    if flags & _CRITICAL_FLAGS:
        return "CRITICAL"
    if flags & _ALERT_FLAGS:
        return "ALERT"
    if flags & _ERROR_FLAGS:
        return "ERROR"
    if flags & _WARNING_FLAGS:
        return "WARNING"
    if (detail or {}).get("grounded") is False:
        return "WARNING"
    return "INFO"


@router.get("/governance/logs")
async def governance_logs(
    limit: int = 200,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    actor: str = Depends(get_current_actor),
):
    total = await db.scalar(select(func.count(AuditLog.id)))
    rows = (
        await db.execute(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(limit, 2000))
        )
    ).scalars().all()
    return {
        "total": total or 0,
        "items": [
            {
                "created_at": a.created_at.isoformat() + "Z",
                "level": _log_level_for(a.detail),
                "service": str(getattr(a.action, "value", a.action)),
                "message": json.dumps(a.detail, default=str)[:300] if a.detail else "",
                "user_id": a.actor,
                "ip_address": a.ip_address,
                "request_id": a.request_id,
                "trace_id": a.request_id,
            }
            for a in rows
        ],
    }


# ============================================================================
# AI USAGE
# ============================================================================


def _est_tokens(text: str | None) -> int:
    return max(1, len(text or "") // 4) if text else 0


def _r(v, d=0):
    return round(v, d) if isinstance(v, (int, float)) else None


async def _latency_rows(db: AsyncSession, hours: int = 24 * 30):
    since = datetime.utcnow() - timedelta(hours=hours)  # noqa: DTZ003
    return (
        await db.execute(select(LatencyRecord).where(LatencyRecord.created_at >= since))
    ).scalars().all()


def _avg(rows, field):
    vals = [getattr(r, field) for r in rows if getattr(r, field) is not None]
    return sum(vals) / len(vals) if vals else None


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
    from app.core.config import get_settings
    settings = get_settings()

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
                ChatMessageRecord.ip_address,
                ChatMessageRecord.prompt_tokens,
                ChatMessageRecord.completion_tokens,
                ChatMessageRecord.total_tokens,
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
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
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
            p["prompt_tokens"] = r.prompt_tokens
            p["completion_tokens"] = r.completion_tokens
            p["total_tokens"] = r.total_tokens
            if p["created_at"] is None:
                p["created_at"] = r.created_at

    out = []
    for p in pairs.values():
        has_actual = p["prompt_tokens"] is not None and p["completion_tokens"] is not None
        if has_actual:
            in_tok = p["prompt_tokens"]
            out_tok = p["completion_tokens"]
            is_actual = True
        else:
            # Legacy/no-LLM-call fallback: same heuristic the estimate always
            # used (question text + a fixed context/history budget).
            in_tok = _est_tokens(p["query"]) + settings.llm_context_max_tokens // 2
            out_tok = _est_tokens(p["answer"])
            is_actual = False
        cost = in_tok / 1e6 * 0.25 + out_tok / 1e6 * 2.0
        out.append(
            {
                **p,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": p["total_tokens"] if has_actual else in_tok + out_tok,
                "cost_usd": round(cost, 6),
                "is_actual": is_actual,
            }
        )
    return out


@router.get("/usage/llm")
async def usage_llm(
    db: AsyncSession = Depends(get_db),  # noqa: B008
    actor: str = Depends(get_current_actor),
):
    from app.core.config import get_settings
    settings = get_settings()

    rows = await _latency_rows(db)
    query_rows = await _query_usage_rows(db)
    in_tokens = sum(q["input_tokens"] for q in query_rows)
    out_tokens = sum(q["output_tokens"] for q in query_rows)
    cost = sum(q["cost_usd"] for q in query_rows)
    failed = sum(1 for q in query_rows if q["grounded"] is False)
    actual_count = sum(1 for q in query_rows if q["is_actual"])
    return {
        "provider": "Azure OpenAI",
        "model": settings.azure_deployment_name,
        "requests": len(rows),
        "successful": max(len(rows) - failed, 0),
        "failed": failed,
        "input_tokens": in_tokens,
        "output_tokens": out_tokens,
        "total_tokens": in_tokens + out_tokens,
        "average_latency_ms": _r(_avg(rows, "llm_ms")),
        "cost": f"${cost:.4f}",
        # How many of the queries counted above used the real Azure usage
        # numbers vs. the legacy text-length estimate (pre-migration rows
        # or exchanges with no actual LLM call).
        "token_accounting": {
            "actual": actual_count,
            "estimated": len(query_rows) - actual_count,
        },
    }


@router.get("/usage/embedding")
async def usage_embedding(
    db: AsyncSession = Depends(get_db),  # noqa: B008
    actor: str = Depends(get_current_actor),
):
    from app.core.config import get_settings
    settings = get_settings()
    from app.models.db_models import Chunk, Document

    rows = await _latency_rows(db)
    docs = await db.scalar(select(func.count(Document.id)))
    chunks = await db.scalar(select(func.count(Chunk.id)))
    chunk_chars = await db.scalar(select(func.coalesce(func.sum(func.length(Chunk.content)), 0)))
    q_msgs = (
        await db.execute(select(ChatMessageRecord.content).where(ChatMessageRecord.role == "user"))
    ).scalars().all()
    tokens = int((chunk_chars or 0) // 4) + sum(_est_tokens(c) for c in q_msgs)
    return {
        "provider": "Azure OpenAI",
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dim,
        "documents": docs or 0,
        "chunks": chunks or 0,
        "requests": len(q_msgs) + (chunks or 0),
        "input_tokens": tokens,
        "average_latency_ms": _r(_avg(rows, "embedding_ms")),
        "cost": f"${tokens / 1e6 * 0.02:.4f}",
    }


@router.get("/usage/reranker")
async def usage_reranker(
    db: AsyncSession = Depends(get_db),  # noqa: B008
    actor: str = Depends(get_current_actor),
):
    from app.core.config import get_settings
    settings = get_settings()

    rows = [r for r in await _latency_rows(db) if r.reranker_ms is not None]
    avg_ms = _avg(rows, "reranker_ms")
    pool = max(settings.default_top_k * settings.retrieval_candidate_multiplier,
               settings.retrieval_candidate_min)
    cpu = ram = None
    try:
        import psutil

        cpu = psutil.cpu_percent(interval=0.3)
        ram = round(psutil.Process().memory_info().rss / 1024 / 1024)
    except Exception:  # noqa: BLE001, S110
        pass
    return {
        "provider": "Local cross-encoder" if settings.enable_reranker else "Disabled",
        "model": settings.reranker_model,
        "queries": len(rows),
        "requests": len(rows),
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
async def usage_metadata(actor: str = Depends(get_current_actor)):
    from app.core.config import get_settings
    settings = get_settings()

    return {
        "llm": {"provider": "Azure OpenAI", "model": settings.azure_deployment_name},
        "embedding": {
            "provider": "Azure OpenAI",
            "model": settings.embedding_model,
            "dimensions": settings.embedding_dim,
        },
        "reranker": {
            "provider": "Local cross-encoder" if settings.enable_reranker else "Disabled",
            "model": settings.reranker_model,
        },
        "retrieval": {"top_k": settings.default_top_k, "similarity": "Cosine (pgvector)"},
        "chunking": {
            "strategy": "Structure-aware",
            "chunk_size": "≤ 4000 chars",
            "overlap": "None (hash-based incremental)",
        },
    }


@router.get("/usage/cost-breakdown")
async def usage_cost_breakdown(
    db: AsyncSession = Depends(get_db),  # noqa: B008
    actor: str = Depends(get_current_actor),
):
    """Per-query and per-session token/cost usage, split into two
    independent groupings that are intentionally kept separate (never
    merged together):

    - `by_account`: grouped by the logged-in account (user_email) that ran
      the query — i.e. the tester doing the testing.
    - `by_ip`: grouped by the client IP address the query came from — i.e.
      the user accessing the app from that network/location, regardless of
      which account they're signed in as.

    Token counts are the *actual* Azure OpenAI usage recorded at generation
    time (see app.harness.prompts.call_llm / _query_usage_rows) whenever
    available, falling back to the old length-based estimate only for
    exchanges that predate that tracking or had no real LLM call. Cost uses
    a placeholder rate ($0.25 / 1M input tokens, $2.00 / 1M output tokens)
    — update once real per-token pricing is confirmed.
    """
    rows = await _query_usage_rows(db)

    queries = [
        {
            "session_id": q["session_id"],
            "session_title": q["session_title"],
            "user_email": q["user_email"],
            "ip_address": q["ip_address"],
            "query": (q["query"] or "")[:160],
            "request_id": q["request_id"],
            "created_at": q["created_at"].isoformat() + "Z" if q["created_at"] else None,
            "input_tokens": q["input_tokens"],
            "output_tokens": q["output_tokens"],
            "total_tokens": q["total_tokens"],
            "cost_usd": q["cost_usd"],
            "is_actual": q["is_actual"],
            "_sort_ts": q["created_at"],
        }
        for q in rows
    ]
    queries.sort(key=lambda q: q["_sort_ts"] or datetime.min, reverse=True)  # noqa: DTZ901

    def _group(key_fn):
        groups: dict[str, dict] = {}
        for q in queries:
            key = key_fn(q) or "Unknown"
            g = groups.setdefault(
                key,
                {
                    "sessions": set(),
                    "queries": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            g["sessions"].add(q["session_id"])
            g["queries"] += 1
            g["input_tokens"] += q["input_tokens"]
            g["output_tokens"] += q["output_tokens"]
            g["total_tokens"] += q["total_tokens"]
            g["cost_usd"] += q["cost_usd"]
        out = [
            {
                "key": key,
                "sessions": len(g["sessions"]),
                "queries": g["queries"],
                "input_tokens": g["input_tokens"],
                "output_tokens": g["output_tokens"],
                "total_tokens": g["total_tokens"],
                "cost_usd": round(g["cost_usd"], 4),
            }
            for key, g in groups.items()
        ]
        out.sort(key=lambda g: g["cost_usd"], reverse=True)
        return out

    sessions: dict[str, dict] = {}
    for q in queries:
        s = sessions.setdefault(
            q["session_id"],
            {
                "session_id": q["session_id"],
                "session_title": q["session_title"],
                "user_email": q["user_email"],
                "ip_address": q["ip_address"],
                "queries": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "last_activity_at": None,
            },
        )
        s["queries"] += 1
        s["input_tokens"] += q["input_tokens"]
        s["output_tokens"] += q["output_tokens"]
        s["total_tokens"] += q["total_tokens"]
        s["cost_usd"] += q["cost_usd"]
        if q["_sort_ts"] and (s["last_activity_at"] is None or q["_sort_ts"] > s["last_activity_at"]):
            s["last_activity_at"] = q["_sort_ts"]

    sessions_out = [
        {
            **{k: v for k, v in s.items() if k != "last_activity_at"},
            "cost_usd": round(s["cost_usd"], 4),
            "last_activity_at": s["last_activity_at"].isoformat() + "Z" if s["last_activity_at"] else None,
        }
        for s in sessions.values()
    ]
    sessions_out.sort(key=lambda s: s["last_activity_at"] or "", reverse=True)

    actual_count = sum(1 for q in queries if q["is_actual"])

    return {
        "by_account": _group(lambda q: q["user_email"]),
        "by_ip": _group(lambda q: q["ip_address"]),
        "sessions": sessions_out,
        "queries": [{k: v for k, v in q.items() if k != "_sort_ts"} for q in queries[:500]],
        "token_accounting": {
            "actual": actual_count,
            "estimated": len(queries) - actual_count,
        },
    }


# ============================================================================
# DASHBOARD
# ============================================================================


@router.get("/dashboard")
async def admin_dashboard(
    db: AsyncSession = Depends(get_db),  # noqa: B008
    actor: str = Depends(get_current_actor),
):
    since = datetime.utcnow() - timedelta(hours=24)  # noqa: DTZ003
    users = await db.scalar(select(func.count(User.id)))
    active_users = await db.scalar(
        select(func.count(func.distinct(ChatSession.user_email))).where(
            ChatSession.last_activity_at >= since
        )
    )
    sessions = await db.scalar(select(func.count(ChatSession.id)))
    # Kept equal to `sessions` (a session already *is* one conversation) so
    # the Sessions and Conversations dashboard cards never show two
    # different counts for the same underlying objects.
    conversations = sessions
    queries = await db.scalar(
        select(func.count(ChatMessageRecord.id)).where(ChatMessageRecord.role == "user")
    )
    feedback = await db.scalar(
        select(func.count(AuditLog.id)).where(AuditLog.action.ilike("%feedback%"))
    )
    avg_latency = await db.scalar(select(func.avg(LatencyRecord.total_ms)))
    requests_total = METRICS["requests"]
    return {
        "users": users or 0,
        "active_users": active_users or 0,
        "sessions": sessions or 0,
        "conversations": conversations or 0,
        "queries": queries or 0,
        "feedback": feedback or 0,
        "avg_latency_ms": float(avg_latency or 0),
        "error_rate": METRICS["errors"] * 100 / requests_total if requests_total else 0.0,
        "pipeline": await _pipeline_status(db),
        "governance": await _governance_status(db, since),
        "query_trend": await _query_trend(db),
        "latency_breakdown": await _latency_breakdown(db),
    }


async def _query_trend(db: AsyncSession) -> list[dict]:
    """Daily user-query counts for the last 7 days, oldest first."""
    since = datetime.utcnow() - timedelta(days=7)  # noqa: DTZ003
    rows = (
        await db.execute(
            select(ChatMessageRecord.created_at).where(
                ChatMessageRecord.role == "user",
                ChatMessageRecord.created_at >= since,
            )
        )
    ).scalars().all()

    counts: dict[str, int] = {}
    today = datetime.utcnow().date()  # noqa: DTZ003
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        counts[day.isoformat()] = 0
    for created_at in rows:
        key = created_at.date().isoformat()
        if key in counts:
            counts[key] += 1

    return [{"date": day, "queries": count} for day, count in counts.items()]


async def _latency_breakdown(db: AsyncSession) -> list[dict]:
    """Average ms per pipeline stage over the last 24h, for stacked/bar charts."""
    since = datetime.utcnow() - timedelta(hours=24)  # noqa: DTZ003
    rows = (
        await db.execute(select(LatencyRecord).where(LatencyRecord.created_at >= since))
    ).scalars().all()

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
    from app.core.config import get_settings

    settings = get_settings()
    since = datetime.utcnow() - timedelta(hours=24)  # noqa: DTZ003
    rows = (
        await db.execute(select(LatencyRecord).where(LatencyRecord.created_at >= since))
    ).scalars().all()

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


# ============================================================================
# FEEDBACK
# ============================================================================


@router.get("/feedback")
async def list_feedback(
    rating: int | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    actor: str = Depends(get_current_actor),
):
    """Return feedback records with de-duplication by message_id and pagination."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    query = (
        select(AuditLog)
        .where(AuditLog.action.ilike("%feedback%"))
        .order_by(AuditLog.created_at.desc())
    )

    rows = (await db.execute(query)).scalars().all()

    seen_messages: set[str] = set()
    filtered = []
    for a in rows:
        detail = a.detail if isinstance(a.detail, dict) else {}
        mid = detail.get("message_id")
        if mid:
            if mid in seen_messages:
                continue
            seen_messages.add(mid)

        item_rating = detail.get("rating")
        if rating is not None and item_rating != rating:
            continue

        comment = detail.get("comment") or ""
        conv_id = detail.get("conversation_id") or ""
        ip_addr = a.ip_address or "Unknown"

        if q and q.strip():
            needle = q.strip().lower()
            if not (
                needle in comment.lower()
                or needle in ip_addr.lower()
                or needle in conv_id.lower()
                or needle in (mid or "").lower()
            ):
                continue

        filtered.append(
            {
                "id": str(a.id),
                "rating": item_rating,
                "comment": comment,
                "ip_address": ip_addr,
                "conversation_id": conv_id,
                "message_id": mid,
                "created_at": a.created_at.isoformat() + "Z" if a.created_at else None,
            }
        )

    total_count = len(filtered)
    paged = filtered[offset : offset + limit]

    return {
        "items": paged,
        "total": total_count,
        "limit": limit,
        "offset": offset,
    }
