"""Lightweight enqueue trigger used on the write path (no classifier imports)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.jobs.queue import enqueue, enqueue_in

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

logger = get_logger(__name__)

CLASSIFY_JOB = "insights.classify"


def classify_dedupe_key(message_id: uuid.UUID | str) -> str:
    return f"{CLASSIFY_JOB}:{message_id}"


def _payload(message_id: uuid.UUID | str) -> dict[str, str]:
    return {"message_id": str(message_id)}


async def enqueue_classification(
    message_id: uuid.UUID | str, *, engine: AsyncEngine | None = None
) -> uuid.UUID | None:
    """Queue ``insights.classify`` for an assistant message (deduped)."""
    return await enqueue(
        CLASSIFY_JOB,
        _payload(message_id),
        dedupe_key=classify_dedupe_key(message_id),
        engine=engine,
    )


async def enqueue_classification_in_transaction(
    db: AsyncSession, message_id: uuid.UUID
) -> None:
    """Queue classification in the same transaction that writes the message.

    Runs inside a SAVEPOINT, so a failure here (e.g. the queue is unavailable)
    never loses the exchange itself; the backfill picks such turns up later.
    """
    if not get_settings().insights_classify_on_write:
        return
    try:
        async with db.begin_nested():
            await enqueue_in(
                db,
                CLASSIFY_JOB,
                _payload(message_id),
                dedupe_key=classify_dedupe_key(message_id),
            )
    except Exception as exc:
        logger.warning(
            "insights_enqueue_failed",
            message_id=str(message_id),
            error_type=type(exc).__name__,
        )
