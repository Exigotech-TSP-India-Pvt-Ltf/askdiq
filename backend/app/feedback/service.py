"""Feedback triage state machine, promotion, bulk actions and export."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.feedback import repository as repo
from app.governance.audit import record_audit_event
from app.governance.pii import redact_pii
from app.models.db_models import ChatMessageRecord, MessageFeedback
from app.schemas.feedback import (
    BulkAction,
    FeedbackBulkRequest,
    FeedbackBulkResponse,
    FeedbackItem,
    FeedbackSource,
    FeedbackTriageRequest,
    PromoteResponse,
    TriageStatus,
)

# new -> reviewed | promoted | dismissed; reviewed -> promoted | dismissed;
# dismissed -> new (reopen); promoted is terminal.
TRANSITIONS: dict[TriageStatus, frozenset[TriageStatus]] = {
    TriageStatus.NEW: frozenset(
        {TriageStatus.REVIEWED, TriageStatus.PROMOTED, TriageStatus.DISMISSED}
    ),
    TriageStatus.REVIEWED: frozenset({TriageStatus.PROMOTED, TriageStatus.DISMISSED}),
    TriageStatus.DISMISSED: frozenset({TriageStatus.NEW}),
    TriageStatus.PROMOTED: frozenset(),
}

DETAIL_STALE = "This feedback was changed by someone else. Reload and retry."
DETAIL_NOT_FOUND = "Feedback not found"


def can_transition(current: str, target: TriageStatus) -> bool:
    return target in TRANSITIONS.get(TriageStatus(current), frozenset())


def _mask(text: str | None) -> str | None:
    return redact_pii(text)[0] if text else text


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _sources(raw: Any) -> list[FeedbackSource]:
    if not isinstance(raw, list):
        return []
    return [
        FeedbackSource(
            source_name=s.get("source_name"),
            section_heading=s.get("section_heading"),
            score=s.get("score"),
            chunk_id=str(s.get("chunk_id") or "") or None,
        )
        for s in raw
        if isinstance(s, dict)
    ]


def to_item(r: Any) -> FeedbackItem:
    """Map one enriched row; keeps the legacy fields the current UI reads."""
    return FeedbackItem(
        id=str(r.id),
        rating=r.rating,
        comment=r.comment or "",
        ip_address=r.ip_address or "Unknown",
        conversation_id=str(r.session_id) if r.session_id else "",
        message_id=str(r.message_id) if r.message_id else "",
        created_at=_iso(r.created_at),
        updated_at=_iso(r.updated_at),
        actor=r.actor,
        user_query=r.user_query,
        answer=r.answer,
        outcome=r.outcome or ("answered" if r.answer is not None else None),
        grounded=r.grounded,
        guardrail_flags=list(r.guardrail_flags or []),
        sources=_sources(r.retrieved_chunks),
        confidence=r.confidence,
        latency_ms=r.latency_ms,
        cost_usd=r.cost_usd,
        reason_tags=list(r.reason_tags or []),
        corrected_answer=r.corrected_answer,
        triage_status=TriageStatus(r.triage_status),
        reviewed_by=r.reviewed_by,
        reviewed_at=_iso(r.reviewed_at),
        review_note=r.review_note,
        promoted_case_id=r.promoted_case_id,
        version=r.version,
    )


async def _audit(
    db: AsyncSession, actor: str, action: str, detail: dict[str, Any]
) -> None:
    await record_audit_event(
        db,
        request_id=f"feedback-{uuid.uuid4().hex[:12]}",
        actor=actor,
        action=action,
        detail=detail,
        commit=False,
    )


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


async def _promote_message(
    db: AsyncSession,
    message_id: uuid.UUID,
    actor: str,
    note: str | None,
    now: datetime,
) -> tuple[str, str, bool, list[uuid.UUID]]:
    """Create (or reuse) the golden case for a message; flush only, no commit.

    Returns (case_id, query, created, promoted feedback ids).
    """
    asst = await db.get(ChatMessageRecord, message_id)
    if asst is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assistant message not found")
    user_msg = await db.scalar(
        select(ChatMessageRecord.content).where(
            ChatMessageRecord.session_id == asst.session_id,
            ChatMessageRecord.turn_index == asst.turn_index,
            ChatMessageRecord.role == "user",
        )
    )
    query_text = user_msg or "Unknown user query"
    best = await db.scalar(
        select(MessageFeedback)
        .where(MessageFeedback.message_id == message_id)
        .order_by(
            MessageFeedback.corrected_answer.is_(None),
            MessageFeedback.updated_at.desc(),
        )
        .limit(1)
        .execution_options(populate_existing=True)
    )
    case_id = f"promoted_{message_id.hex}"
    created = await repo.insert_promoted_case(
        db,
        {
            "id": case_id,
            "query": query_text,
            "case_type": "robustness" if asst.grounded else "negative",
            "expect_grounded": bool(asst.grounded),
            "expected_outcome": asst.outcome or "answered",
            "source_message_id": message_id,
            "feedback_id": best.id if best else None,
            "reference_answer": best.corrected_answer if best else None,
            "reason_tags": list(best.reason_tags or []) if best else [],
            "promoted_by": actor,
            "promoted_at": now,
        },
    )
    if not created:
        existing = await repo.case_id_for_message(db, message_id)
        if existing is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A golden case with this id already exists"
            )
        case_id = existing
    promoted_ids = await repo.mark_message_promoted(
        db, message_id, case_id, actor, now, note
    )
    return case_id, query_text, created, promoted_ids


async def promote_message(
    db: AsyncSession, message_id: uuid.UUID, actor: str, note: str | None = None
) -> PromoteResponse:
    """``POST /admin/feedback/{message_id}/promote-to-test`` (DB-backed, F4/F5)."""
    now = datetime.utcnow()
    try:
        case_id, query_text, created, ids = await _promote_message(
            db, message_id, actor, note, now
        )
        await _audit(
            db,
            actor,
            "feedback_promoted_to_eval_case",
            {
                "message_id": str(message_id),
                "case_id": case_id,
                "query": _mask(query_text),
                "created": created,
                "feedback_ids": [str(i) for i in ids],
            },
        )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    return PromoteResponse(
        status="promoted",
        case_id=case_id,
        query=query_text,
        created=created,
        feedback_ids=[str(i) for i in ids],
    )


# ---------------------------------------------------------------------------
# Single-row triage
# ---------------------------------------------------------------------------


async def triage(
    db: AsyncSession, feedback_id: uuid.UUID, body: FeedbackTriageRequest, actor: str
) -> FeedbackItem:
    row = await db.get(MessageFeedback, feedback_id, populate_existing=True)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, DETAIL_NOT_FOUND)
    if row.version != body.version:
        raise HTTPException(status.HTTP_409_CONFLICT, DETAIL_STALE)
    current = row.triage_status
    target = body.triage_status
    sent = body.model_fields_set
    if target is None and not sent & {"review_note", "corrected_answer"}:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Provide triage_status, review_note and/or corrected_answer",
        )
    if target is not None and not can_transition(current, target):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Invalid transition: {current} -> {target.value}",
        )

    now = datetime.utcnow()
    message_id = row.message_id
    values: dict[str, Any] = {"reviewed_by": actor, "reviewed_at": now}
    if "review_note" in sent:
        values["review_note"] = body.review_note or None
    if "corrected_answer" in sent:
        values["corrected_answer"] = _mask(body.corrected_answer) or None
    case_id: str | None = None
    try:
        if target is TriageStatus.PROMOTED:
            # Claim the row first (version check), then create the case.
            values["triage_status"] = TriageStatus.REVIEWED.value
        elif target is not None:
            values["triage_status"] = target.value
            if target is TriageStatus.NEW:
                values["reviewed_by"] = None
                values["reviewed_at"] = None
        if not await repo.update_status_if_version(
            db, feedback_id, body.version, values
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, DETAIL_STALE)
        if target is TriageStatus.PROMOTED:
            case_id, _q, _created, _ids = await _promote_message(
                db, message_id, actor, body.review_note, now
            )
        await _audit(
            db,
            actor,
            "feedback_triaged",
            {
                "feedback_id": str(feedback_id),
                "message_id": str(message_id),
                "from": current,
                "to": target.value if target else current,
                "note": _mask(body.review_note),
                "case_id": case_id,
            },
        )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    item = await repo.get_feedback_item(db, feedback_id)
    if item is None:  # deleted concurrently with its chat session
        raise HTTPException(status.HTTP_404_NOT_FOUND, DETAIL_NOT_FOUND)
    return to_item(item)


# ---------------------------------------------------------------------------
# Bulk (one transaction, all-or-nothing)
# ---------------------------------------------------------------------------


async def bulk_triage(
    db: AsyncSession, body: FeedbackBulkRequest, actor: str
) -> FeedbackBulkResponse:
    target = TriageStatus(body.action.value)
    now = datetime.utcnow()
    try:
        rows = await repo.lock_feedback_rows(db, body.ids)
        found = {r.id for r in rows}
        missing = [str(i) for i in body.ids if i not in found]
        if missing:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                {"message": "Some feedback ids were not found", "ids": missing},
            )
        invalid = [
            str(r.id) for r in rows if not can_transition(r.triage_status, target)
        ]
        if invalid:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "message": f"Invalid transition to '{target.value}'; nothing was changed",
                    "ids": invalid,
                },
            )

        previous = {r.id: r.triage_status for r in rows}
        case_ids: list[str] = []
        if body.action is BulkAction.PROMOTED:
            for message_id in dict.fromkeys(r.message_id for r in rows):
                case_id, _q, _created, _ids = await _promote_message(
                    db, message_id, actor, body.review_note, now
                )
                case_ids.append(case_id)
        else:
            for r in rows:
                r.triage_status = target.value
                r.reviewed_by = actor
                r.reviewed_at = now
                if body.review_note is not None:
                    r.review_note = body.review_note
                r.version = r.version + 1
        for r in rows:
            await _audit(
                db,
                actor,
                "feedback_triaged",
                {
                    "feedback_id": str(r.id),
                    "message_id": str(r.message_id),
                    "from": previous[r.id],
                    "to": target.value,
                    "note": _mask(body.review_note),
                    "bulk": True,
                },
            )
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    return FeedbackBulkResponse(
        action=body.action,
        updated=len(rows),
        ids=[str(i) for i in body.ids],
        promoted_case_ids=case_ids,
    )


# ---------------------------------------------------------------------------
# Preference-pair export (JSONL, streamed)
# ---------------------------------------------------------------------------


def build_pair(row: Any) -> dict[str, Any]:
    """prompt/chosen/rejected for DPO-style training; flags incomplete pairs."""
    answer = _mask(row.answer)
    corrected = row.corrected_answer  # already masked at write time
    if corrected:
        chosen, rejected = corrected, answer
    elif row.rating >= 4:  # noqa: PLR2004 - 4-5 stars = positive
        chosen, rejected = answer, None
    else:
        chosen, rejected = None, answer
    prompt = _mask(row.prompt)
    missing = [
        name
        for name, value in (
            ("prompt", prompt),
            ("chosen", chosen),
            ("rejected", rejected),
        )
        if not value
    ]
    chunks = row.retrieved_chunks if isinstance(row.retrieved_chunks, list) else []
    return {
        "feedback_id": str(row.id),
        "message_id": str(row.message_id),
        "prompt": prompt,
        "chosen": chosen,
        "rejected": rejected,
        "rating": row.rating,
        "reason_tags": list(row.reason_tags or []),
        "source_ids": [
            str(c.get("chunk_id"))
            for c in chunks
            if isinstance(c, dict) and c.get("chunk_id")
        ],
        "triage_status": row.triage_status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "complete": not missing,
        "missing": missing,
    }


async def export_pairs_jsonl(
    db: AsyncSession,
    *,
    triage_status: TriageStatus | None,
    include_dismissed: bool,
    complete_only: bool,
    batch_size: int,
    window: tuple[datetime, datetime] | None = None,
) -> AsyncIterator[str]:
    """One JSON line per pair; rows come off a server-side cursor in batches."""
    async for row in repo.stream_pair_rows(
        db,
        triage_status=triage_status,
        include_dismissed=include_dismissed,
        batch_size=batch_size,
        window=window,
    ):
        pair = build_pair(row)
        if complete_only and not pair["complete"]:
            continue
        yield json.dumps(pair, ensure_ascii=False) + "\n"
