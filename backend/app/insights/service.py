"""Insights classification and rollup service (runs only inside job handlers).

- Rule-labelled turns are written immediately (no lock, no spend).
- LLM batches run under one Postgres advisory lock across all workers and
  replicas, so concurrent ``insights.classify`` jobs never pay twice for the
  same turns: a job that cannot take the lock is deferred, and by the time it
  runs again its turn has usually been labelled by the lock holder's batch.
- Spend is capped by ``INSIGHTS_DAILY_TOKEN_BUDGET`` per local day
  (``INSIGHTS_TIMEZONE``); over budget, jobs are deferred to the next day.
- No database connection is held in a transaction during an LLM call.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.circuit import CircuitOpenError
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.governance.audience import get_classified_ips
from app.insights.classifier import ClassificationResult, classify_turns, split_by_rule
from app.insights.llm import ClassifierLLM
from app.insights.repository import CLASSIFIER_LOCK_KEY, InsightsRepository
from app.insights.types import TurnInput
from app.jobs.queue import JobDeferredError, utcnow

logger = get_logger(__name__)

LOCK_BUSY_RETRY = timedelta(seconds=15)
_MIN_CIRCUIT_RETRY = timedelta(seconds=5)


class InsightsService:
    def __init__(
        self,
        *,
        engine: AsyncEngine | None = None,
        llm: ClassifierLLM | None = None,
    ) -> None:
        if engine is None:
            from app.core.database import engine as app_engine  # noqa: PLC0415

            engine = app_engine
        self.engine = engine
        self.sessions = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        self.llm = llm
        self.settings = get_settings()

    # -- locking ----------------------------------------------------------

    @asynccontextmanager
    async def _classifier_lock(self) -> AsyncIterator[bool]:
        """Session-level advisory lock on an autocommit connection (no open txn)."""
        async with self.engine.connect() as raw:
            conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
            acquired = bool(
                await conn.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": CLASSIFIER_LOCK_KEY},
                )
            )
            try:
                yield acquired
            finally:
                if acquired:
                    await conn.execute(
                        text("SELECT pg_advisory_unlock(:key)"),
                        {"key": CLASSIFIER_LOCK_KEY},
                    )

    # -- classification ---------------------------------------------------

    async def _write_rule_labels(self, turns: Sequence[TurnInput]) -> list[TurnInput]:
        """Persist rule labels for ``turns``; return the turns that need the LLM."""
        async with self.sessions() as session:
            repo = InsightsRepository(session)
            taxonomy = await repo.load_taxonomy()
            labels, pending = split_by_rule(turns, taxonomy)
            await repo.insert_labels(labels)
            await session.commit()
        return pending

    async def _classify_with_llm(
        self, turns: Sequence[TurnInput]
    ) -> ClassificationResult:
        """One budget-checked pass over ``turns``; persists labels and usage."""
        tz = self.settings.insights_timezone
        async with self.sessions() as session:
            repo = InsightsRepository(session)
            taxonomy = await repo.load_taxonomy()
            used = await repo.tokens_used_today(tz)
        remaining = max(0, self.settings.insights_daily_token_budget - used)

        try:
            result = await classify_turns(
                turns,
                taxonomy,
                batch_size=self.settings.insights_batch_size,
                budget_remaining=remaining,
                llm=self.llm,
            )
        except CircuitOpenError as exc:
            retry = max(timedelta(seconds=exc.retry_after), _MIN_CIRCUIT_RETRY)
            raise JobDeferredError(utcnow() + retry, "llm circuit open") from exc

        async with self.sessions() as session:
            repo = InsightsRepository(session)
            await repo.record_usage(result.usage)
            await repo.insert_labels(result.labels)
            await session.commit()
        return result

    async def _defer_for_budget(self) -> JobDeferredError:
        async with self.sessions() as session:
            reset = await InsightsRepository(session).next_budget_reset(
                self.settings.insights_timezone
            )
        return JobDeferredError(reset, "insights daily token budget exhausted")

    async def classify_message(self, message_id: uuid.UUID) -> None:
        """Label one exchange, batching it with other unlabelled turns."""
        async with self.sessions() as session:
            seed = await InsightsRepository(session).pending_turns(
                message_ids=[message_id]
            )
        if not seed:
            return  # already labelled (idempotent) or the message was deleted
        if not await self._write_rule_labels(seed):
            return

        async with self._classifier_lock() as acquired:
            if not acquired:
                raise JobDeferredError(utcnow() + LOCK_BUSY_RETRY, "classifier busy")
            batch_size = self.settings.insights_batch_size
            async with self.sessions() as session:
                repo = InsightsRepository(session)
                seed = await repo.pending_turns(message_ids=[message_id])
                if not seed:
                    return
                others = await repo.pending_turns(
                    exclude=[message_id], limit=batch_size * 2
                )
            # Free riders: rule-labelled now, LLM ones fill the seed's batch.
            others_llm = await self._write_rule_labels(others) if others else []
            batch = [*seed, *others_llm][:batch_size]
            result = await self._classify_with_llm(batch)
        if any(t.message_id == message_id for t in result.deferred):
            raise await self._defer_for_budget()

    async def backfill(self, message_ids: Sequence[uuid.UUID]) -> int:
        """Label the given exchanges in batches; returns how many were labelled."""
        labelled = 0
        async with self._classifier_lock() as acquired:
            if not acquired:
                raise JobDeferredError(utcnow() + LOCK_BUSY_RETRY, "classifier busy")
            async with self.sessions() as session:
                turns = await InsightsRepository(session).pending_turns(
                    message_ids=message_ids
                )
            pending = await self._write_rule_labels(turns)
            labelled += len(turns) - len(pending)
            size = self.settings.insights_batch_size
            for start in range(0, len(pending), size):
                result = await self._classify_with_llm(pending[start : start + size])
                labelled += len(result.labels)
                if result.deferred:
                    logger.info("insights_backfill_deferred", labelled=labelled)
                    raise await self._defer_for_budget()
        return labelled

    # -- rollup -------------------------------------------------------------

    async def rollup(self, days: Sequence[date] | None = None) -> list[date]:
        """Recompute ``days`` (default: today, yesterday and late-labelled days)."""
        tz = self.settings.insights_timezone
        async with self.sessions() as session:
            repo = InsightsRepository(session)
            await repo.lock_rollup()
            target = sorted(set(days)) if days else await repo.dirty_days(tz)
            test_ips, _users, _as_of = await get_classified_ips(session)
            rows = await repo.recompute_days(target, tz, sorted(test_ips))
            await session.commit()
        logger.info("insights_rollup_done", days=len(target), rows=rows)
        return target
