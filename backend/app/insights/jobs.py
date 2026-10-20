"""Insights job handlers and triggers.

- ``insights.classify``: one per assistant message (dedupe on message id),
  enqueued after the exchange is persisted; never runs in the request path.
- ``insights.rollup``: hourly and incremental (today, yesterday and any day
  that received late labels); re-schedules itself for the next hour slot.
- ``insights.backfill``: labels an explicit chunk of message ids
  (``scripts/backfill_turn_insights.py`` enqueues the chunks).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.logging_config import get_logger
from app.insights.service import InsightsService
from app.insights.triggers import CLASSIFY_JOB
from app.jobs.queue import enqueue, utcnow
from app.jobs.registry import job_handler

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = get_logger(__name__)

ROLLUP_JOB = "insights.rollup"
BACKFILL_JOB = "insights.backfill"

MAX_BACKFILL_CHUNK = 500
MAX_ROLLUP_DAYS = 400
ROLLUP_INTERVAL = timedelta(hours=1)


class ClassifyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: uuid.UUID


class BackfillPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_BACKFILL_CHUNK)


class RollupPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: list[date] | None = Field(default=None, max_length=MAX_ROLLUP_DAYS)


def backfill_dedupe_key(message_ids: list[uuid.UUID]) -> str:
    return f"{BACKFILL_JOB}:{message_ids[0]}:{message_ids[-1]}:{len(message_ids)}"


def next_rollup_slot(now: datetime) -> datetime:
    """Start of the next hour (UTC)."""
    return now.replace(minute=0, second=0, microsecond=0) + ROLLUP_INTERVAL


async def ensure_rollup_scheduled(
    *, now: datetime | None = None, engine: AsyncEngine | None = None
) -> uuid.UUID | None:
    """Make sure one rollup is queued for the next hour slot."""
    slot = next_rollup_slot(now or utcnow())
    return await enqueue(
        ROLLUP_JOB,
        {},
        dedupe_key=f"{ROLLUP_JOB}:{slot:%Y%m%dT%H}",
        run_after=slot,
        engine=engine,
    )


async def enqueue_backfill(
    message_ids: list[uuid.UUID], *, engine: AsyncEngine | None = None
) -> uuid.UUID | None:
    payload = BackfillPayload(message_ids=message_ids)
    return await enqueue(
        BACKFILL_JOB,
        payload.model_dump(mode="json"),
        dedupe_key=backfill_dedupe_key(payload.message_ids),
        engine=engine,
    )


@job_handler(CLASSIFY_JOB)
async def classify_turn_task(payload: dict[str, Any]) -> None:
    data = ClassifyPayload.model_validate(payload)
    await InsightsService().classify_message(data.message_id)
    await ensure_rollup_scheduled()


@job_handler(BACKFILL_JOB)
async def backfill_turn_insights_task(payload: dict[str, Any]) -> None:
    data = BackfillPayload.model_validate(payload)
    labelled = await InsightsService().backfill(data.message_ids)
    logger.info("insights_backfill_chunk_done", labelled=labelled)
    await ensure_rollup_scheduled()


@job_handler(ROLLUP_JOB)
async def rollup_insights_task(payload: dict[str, Any]) -> None:
    data = RollupPayload.model_validate(payload)
    await InsightsService().rollup(data.days)
    await ensure_rollup_scheduled()
