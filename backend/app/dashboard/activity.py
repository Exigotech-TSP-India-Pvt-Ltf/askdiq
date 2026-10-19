"""Live activity feed for the dashboard (B7.1): the newest audit events.

One statement reads the newest ``audit_log`` rows of an allow-listed set of
actions (auth events are never shown), joined to ``latency_metrics`` for the
query duration. Summaries are built here from typed fields only (outcome,
rating, status code, counts), never from free text, so they carry no email,
IP address or query text.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard.window import iso_z
from app.governance.monitoring import BLOCKED_FLAGS, FAILED_FLAGS, QUERY_ACTION
from app.governance.outcomes import LEGACY_OUTCOME_MAP
from app.governance.timeseries import scope_audit
from app.models.db_models import AuditLog, LatencyRecord

MAX_LIMIT = 50
DEFAULT_LIMIT = 20
MS_PER_SECOND = 1000.0

_FEEDBACK = ("user_feedback", "negative_feedback_spike")
_ERROR = ("server_error", "llm_call_failed")
_DOCUMENT = ("ingest", "document_delete")
_GUARDRAIL = ("online_eval",)
_ADMIN = (
    "test_ip_added",
    "test_ip_updated",
    "test_ip_removed",
    "feedback_triaged",
    "feedback_promoted_to_eval_case",
    "feedback_exported",
    "golden_case_run_requested",
    "usage_export",
    "governance_logs_export",
    "branding_updated",
    "branding_asset_uploaded",
    "insights_taxonomy_created",
    "insights_taxonomy_updated",
    "insights_taxonomy_archived",
)
FEED_ACTIONS: tuple[str, ...] = (
    QUERY_ACTION,
    *_FEEDBACK,
    *_ERROR,
    *_DOCUMENT,
    *_GUARDRAIL,
    *_ADMIN,
)

_OUTCOME_TEXT = {
    "answered": "answered",
    "ungrounded": "answered without grounding",
    "blocked": "blocked",
    "conversational": "handled as small talk",
    "llm_failed": "failed",
    "clarify": "answered with a clarifying question",
}


def _uuid_text(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _flags(detail: Mapping[str, Any]) -> set[str]:
    raw = detail.get("guardrail_flags")
    return {str(f) for f in raw} if isinstance(raw, list) else set()


def _outcome(detail: Mapping[str, Any]) -> str | None:
    raw = detail.get("outcome")
    return LEGACY_OUTCOME_MAP.get(str(raw).lower()) if raw else None


def classify(action: str, detail: Mapping[str, Any]) -> str:
    """Feed type of an audit row."""
    if action == QUERY_ACTION:
        flags, outcome = _flags(detail), _outcome(detail)
        if outcome == "llm_failed" or flags & FAILED_FLAGS:
            return "error"
        if outcome == "blocked" or flags & BLOCKED_FLAGS:
            return "guardrail"
        return "query"
    if action in _FEEDBACK:
        return "feedback"
    if action in _ERROR:
        return "error"
    if action in _DOCUMENT:
        return "document"
    if action in _GUARDRAIL:
        return "guardrail"
    return "admin"


def summarize(action: str, detail: Mapping[str, Any], latency_ms: float | None) -> str:
    """One PII-free line from typed fields only."""
    if action == QUERY_ACTION:
        outcome = _outcome(detail)
        text = f"Query {_OUTCOME_TEXT.get(outcome or '', 'received')}"
        if latency_ms is not None:
            text += f" in {latency_ms / MS_PER_SECOND:.1f} s"
        return text
    if action == "user_feedback":
        rating = detail.get("rating")
        text = (
            f"Feedback received: {int(rating)}/5"
            if isinstance(rating, int | float)
            else "Feedback received"
        )
        return text + (" with a comment" if detail.get("comment") else "")
    if action == "negative_feedback_spike":
        return "Negative feedback spike detected"
    if action == "server_error":
        code = detail.get("status_code")
        return f"Server error {int(code)}" if isinstance(code, int) else "Server error"
    if action == "llm_call_failed":
        return "LLM call failed"
    if action == "ingest":
        return "Document ingested"
    if action == "document_delete":
        return "Document deleted"
    if action == "online_eval":
        count = len(_flags(detail))
        return f"Online evaluation flagged {count} issue{'s' if count != 1 else ''}"
    return "Admin action: " + action.replace("_", " ")


def to_event(row: Any) -> dict[str, Any]:
    audit: AuditLog = row[0]
    detail = audit.detail if isinstance(audit.detail, dict) else {}
    kind = classify(audit.action, detail)
    traced = kind in {"query", "error", "guardrail"} and audit.action != "server_error"
    return {
        "id": str(audit.id),
        "type": kind,
        "at": iso_z(audit.created_at),
        "summary": summarize(audit.action, detail, row[1]),
        "request_id": audit.request_id if traced else None,
        "conversation_id": _uuid_text(
            detail.get("session_id") or detail.get("conversation_id")
        ),
        "message_id": _uuid_text(detail.get("message_id")),
        "document_id": _uuid_text(detail.get("document_id")),
    }


async def recent_activity(
    db: AsyncSession, audience: str, limit: int
) -> list[dict[str, Any]]:
    stmt = (
        select(AuditLog, LatencyRecord.total_ms)
        .outerjoin(
            LatencyRecord,
            and_(
                AuditLog.action == QUERY_ACTION,
                LatencyRecord.request_id == AuditLog.request_id,
            ),
        )
        .where(AuditLog.action.in_(FEED_ACTIONS))
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
    )
    stmt = await scope_audit(db, audience, stmt)
    return [to_event(row) for row in (await db.execute(stmt)).all()]
