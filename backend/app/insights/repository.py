"""Data access for insights: turns to classify, labels, usage, budget, rollup."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.governance.costing import compute_exchange_cost
from app.insights.taxonomy import (
    DEFAULT_TAXONOMY,
    Taxonomy,
    TaxonomyEntry,
    TaxonomyKind,
)
from app.insights.types import LlmUsage, TurnInput, TurnLabel
from app.models.db_models import (
    ChatMessageRecord,
    InsightLlmUsage,
    InsightTaxonomy,
    TurnInsight,
)

# Labels committed while a rollup ran can carry a classified_at slightly
# older than that rollup's computed_at; re-check this window every run.
ROLLUP_OVERLAP = timedelta(minutes=15)

_TOKENS_TODAY_SQL = text(
    """
    SELECT COALESCE(SUM(total_tokens), 0) FROM insight_llm_usage
    WHERE created_at >= (date_trunc('day', now() AT TIME ZONE CAST(:tz AS text))
                         AT TIME ZONE CAST(:tz AS text))
    """
)

_NEXT_RESET_SQL = text(
    """
    SELECT (date_trunc('day', now() AT TIME ZONE CAST(:tz AS text))
            + interval '1 day') AT TIME ZONE CAST(:tz AS text)
    """
)

# chat_messages.created_at is naive UTC; days are local to INSIGHTS_TIMEZONE.
_DIRTY_DAYS_SQL = text(
    """
    WITH mark AS (
        SELECT COALESCE(MAX(computed_at), '-infinity'::timestamptz)
               - CAST(:overlap AS interval) AS ts
        FROM insight_daily_rollup
    )
    SELECT DISTINCT
           CAST((a.created_at AT TIME ZONE 'UTC') AT TIME ZONE CAST(:tz AS text)
                AS date) AS day
    FROM turn_insights ti
    JOIN chat_messages a ON a.id = ti.message_id
    WHERE ti.classified_at > (SELECT ts FROM mark)
    UNION
    SELECT CAST(now() AT TIME ZONE CAST(:tz AS text) AS date)
    UNION
    SELECT CAST(now() AT TIME ZONE CAST(:tz AS text) AS date) - 1
    """
)

_DELETE_DAYS_SQL = text(
    "DELETE FROM insight_daily_rollup WHERE day = ANY(CAST(:days AS date[]))"
)

_INSERT_DAYS_SQL = text(
    """
    INSERT INTO insight_daily_rollup
        (day, audience, intent, quality, outcome,
         count, handled, substantive, computed_at)
    SELECT CAST((a.created_at AT TIME ZONE 'UTC') AT TIME ZONE CAST(:tz AS text)
                AS date) AS day,
           CASE WHEN u.ip_address IS NULL
                     OR u.ip_address = ANY(CAST(:test_ips AS text[]))
                THEN 'testing' ELSE 'users' END AS audience,
           ti.intent,
           ti.bot_quality,
           COALESCE(a.outcome, 'unknown') AS outcome,
           count(*),
           count(*) FILTER (WHERE ti.handled),
           count(*) FILTER (WHERE ti.substantive),
           now()
    FROM turn_insights ti
    JOIN chat_messages a ON a.id = ti.message_id
    LEFT JOIN chat_messages u
           ON u.session_id = a.session_id
          AND u.turn_index = a.turn_index
          AND u.role = 'user'
    WHERE a.created_at >= (CAST(CAST(:lo AS date) AS timestamp)
                           AT TIME ZONE CAST(:tz AS text)) AT TIME ZONE 'UTC'
      AND a.created_at < (CAST(CAST(:hi AS date) AS timestamp)
                          AT TIME ZONE CAST(:tz AS text)) AT TIME ZONE 'UTC'
      AND CAST((a.created_at AT TIME ZONE 'UTC') AT TIME ZONE CAST(:tz AS text)
               AS date) = ANY(CAST(:days AS date[]))
    GROUP BY 1, 2, 3, 4, 5
    """
)

ROLLUP_LOCK_KEY = 0x1A51_6E70_0002
CLASSIFIER_LOCK_KEY = 0x1A51_6E70_0001


class InsightsRepository:
    """Queries for the classifier and rollup; the caller owns the transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- taxonomy ---------------------------------------------------------

    async def load_taxonomy(self) -> Taxonomy:
        rows = (
            await self.session.execute(
                select(InsightTaxonomy)
                .where(InsightTaxonomy.is_active.is_(True))
                .order_by(InsightTaxonomy.kind, InsightTaxonomy.sort_order)
            )
        ).scalars()
        entries = [
            TaxonomyEntry(
                kind=TaxonomyKind(row.kind),
                code=row.code,
                label=row.label,
                group=row.group,
                substantive=row.substantive,
                handled=row.handled,
                color=row.color,
            )
            for row in rows
        ]
        taxonomy = Taxonomy.from_entries(entries)
        if not (taxonomy.intents and taxonomy.qualities):
            return DEFAULT_TAXONOMY
        return taxonomy

    # -- turns --------------------------------------------------------------

    def _turns_query(self, *, pending_only: bool) -> Any:
        assistant = aliased(ChatMessageRecord, name="a")
        user = aliased(ChatMessageRecord, name="u")
        query = (
            select(
                assistant.id,
                assistant.session_id,
                func.coalesce(user.content, "").label("user_text"),
                assistant.content.label("answer_text"),
                assistant.outcome,
                assistant.grounded,
                assistant.guardrail_flags,
            )
            .select_from(assistant)
            .outerjoin(
                user,
                and_(
                    user.session_id == assistant.session_id,
                    user.turn_index == assistant.turn_index,
                    user.role == "user",
                ),
            )
            .where(assistant.role == "assistant")
        )
        if pending_only:
            query = query.outerjoin(
                TurnInsight, TurnInsight.message_id == assistant.id
            ).where(TurnInsight.id.is_(None))
        return query, assistant

    @staticmethod
    def _to_turn(row: Any) -> TurnInput:
        flags = row.guardrail_flags if isinstance(row.guardrail_flags, list) else []
        return TurnInput(
            message_id=row.id,
            session_id=row.session_id,
            user_text=row.user_text or "",
            answer_text=row.answer_text or "",
            outcome=row.outcome,
            grounded=row.grounded,
            guardrail_flags=tuple(str(f) for f in flags),
        )

    async def pending_turns(
        self,
        *,
        message_ids: Sequence[uuid.UUID] | None = None,
        exclude: Sequence[uuid.UUID] = (),
        limit: int | None = None,
    ) -> list[TurnInput]:
        """Unlabelled exchanges (by assistant message id), newest first."""
        query, assistant = self._turns_query(pending_only=True)
        if message_ids is not None:
            query = query.where(assistant.id.in_(list(message_ids)))
        if exclude:
            query = query.where(assistant.id.not_in(list(exclude)))
        query = query.order_by(assistant.created_at.desc())
        if limit is not None:
            query = query.limit(limit)
        rows = (await self.session.execute(query)).all()
        return [self._to_turn(row) for row in rows]

    async def pending_ids(
        self, *, since: datetime | None = None, limit: int | None = None
    ) -> list[uuid.UUID]:
        """Unlabelled assistant message ids, oldest first (backfill order)."""
        query, assistant = self._turns_query(pending_only=True)
        query = query.with_only_columns(assistant.id)
        if since is not None:
            query = query.where(assistant.created_at >= since)
        query = query.order_by(assistant.created_at, assistant.id)
        if limit is not None:
            query = query.limit(limit)
        return list((await self.session.execute(query)).scalars())

    async def count_pending(self, *, since: datetime | None = None) -> int:
        query, assistant = self._turns_query(pending_only=True)
        query = query.with_only_columns(func.count(assistant.id))
        if since is not None:
            query = query.where(assistant.created_at >= since)
        return int(await self.session.scalar(query) or 0)

    # -- writes -------------------------------------------------------------

    async def insert_labels(self, labels: Sequence[TurnLabel]) -> int:
        """Insert labels; an already-labelled message is left untouched."""
        if not labels:
            return 0
        stmt = (
            pg_insert(TurnInsight)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "message_id": label.message_id,
                        "session_id": label.session_id,
                        "intent": label.intent,
                        "bot_quality": label.bot_quality,
                        "issue": label.issue,
                        "substantive": label.substantive,
                        "handled": label.handled,
                        "friction": label.friction,
                        "confidence": label.confidence,
                        "classified_by": str(label.classified_by),
                        "model": label.model,
                    }
                    for label in labels
                ]
            )
            .on_conflict_do_nothing(index_elements=[TurnInsight.message_id])
            .returning(TurnInsight.id)
        )
        return len((await self.session.execute(stmt)).all())

    async def record_usage(self, usages: Sequence[LlmUsage]) -> None:
        for usage in usages:
            cost_usd, details = compute_exchange_cost(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                cached_tokens=usage.cached_tokens,
            )
            self.session.add(
                InsightLlmUsage(
                    model=usage.model,
                    turns=usage.turns,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    cost_usd=cost_usd,
                    token_details=details,
                )
            )
        await self.session.flush()

    # -- budget -------------------------------------------------------------

    async def tokens_used_today(self, tz: str) -> int:
        used = await self.session.scalar(_TOKENS_TODAY_SQL, {"tz": tz})
        return int(used or 0)

    async def next_budget_reset(self, tz: str) -> datetime:
        return await self.session.scalar(_NEXT_RESET_SQL, {"tz": tz})

    # -- rollup -------------------------------------------------------------

    async def lock_rollup(self) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": ROLLUP_LOCK_KEY}
        )

    async def dirty_days(self, tz: str) -> list[date]:
        """Today, yesterday and every day that received labels since the last run."""
        rows = await self.session.execute(
            _DIRTY_DAYS_SQL, {"tz": tz, "overlap": ROLLUP_OVERLAP}
        )
        return sorted(row.day for row in rows)

    async def recompute_days(
        self, days: Sequence[date], tz: str, test_ips: Sequence[str]
    ) -> int:
        """Replace the rollup rows of ``days`` from the current labels."""
        if not days:
            return 0
        params = {
            "days": list(days),
            "tz": tz,
            "test_ips": list(test_ips),
            "lo": min(days),
            "hi": max(days) + timedelta(days=1),
        }
        await self.session.execute(_DELETE_DAYS_SQL, {"days": params["days"]})
        result = await self.session.execute(_INSERT_DAYS_SQL, params)
        return int(result.rowcount or 0)
