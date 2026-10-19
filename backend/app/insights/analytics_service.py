"""Insights dashboard service: the reference HTML's renderer formulas server-side.

Each method mirrors one renderer function (newplan 6.4): ``summary`` is
``metrics()``, ``trend``/``hourly``/``flow``/``topics``/``quality``/
``dropoff``/``people`` are ``trend()``/``radial()``/``flow()``/``treemap()``/
``donut()``/``lollipop()``/``bubble()``, and ``conversations`` is ``table()``.

Exchange-level counts (questions, handled, per intent and quality) come from
``insight_daily_rollup`` for closed days when the formula allows it: the
"all" period, overview audience, the rollup's own time zone, no
conversation-level filter and an up-to-date rollup. Today and every other
case are computed live. Conversation-level fields are always computed at
query time.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import dashboard_cache, dashboard_cached
from app.core.config import get_settings
from app.core.database import release_connection
from app.governance.audience import get_classified_ips
from app.governance.pii import redact_pii
from app.insights.analytics_repository import (
    CubeRow,
    InsightsAnalyticsRepository,
    QueryContext,
    person_salt,
    resolve_range,
)
from app.insights.taxonomy import NON_SUBSTANTIVE_INTENTS, TaxonomyKind
from app.schemas.insights import (
    Audience,
    ConversationDetail,
    ConversationList,
    ConversationOutcome,
    ConversationPage,
    ConversationRow,
    DateRange,
    DropoffItem,
    FlowLink,
    FlowNode,
    InsightsDropoff,
    InsightsFilters,
    InsightsFlow,
    InsightsHourly,
    InsightsPeople,
    InsightsQuality,
    InsightsSummary,
    InsightsTopics,
    InsightsTrend,
    PendingClassification,
    Period,
    PersonItem,
    QualityItem,
    TopicItem,
    TrendDay,
    TurnDetail,
)

SessionTask = Callable[[AsyncSession], Awaitable[Any]]
ParallelRunner = Callable[..., Awaitable[list[Any]]]
Source = Literal["rollup", "live"]
T = TypeVar("T")

# Prefix of every insights aggregate in ``dashboard_cache``.
INSIGHTS_CACHE_PREFIX = "insights:"

# Donut order of the reference HTML; ACK (closings) is not a question.
QUALITY_ORDER = ("OK", "CORR", "SCOPE", "PART", "FB", "CLAR", "OFF", "FALSE")
OUTCOME_ORDER = (
    ConversationOutcome.SERVED,
    ConversationOutcome.PARTLY_SERVED,
    ConversationOutcome.NOT_SERVED,
)
MAX_TREND_DAYS = 400  # the HTML's days() cap
HOURS_PER_DAY = 24
PCT_DIGITS = 2


def pct(part: int, whole: int) -> float | None:
    """The HTML's ``pct()``: null when the denominator is zero."""
    return round(100 * part / whole, PCT_DIGITS) if whole else None


def is_substantive(intent: str) -> bool:
    """The HTML's ``isSub()``."""
    return intent not in NON_SUBSTANTIVE_INTENTS


def person_label(number: int) -> str:
    return f"P{number:02d}"


def mask(value: str | None) -> str | None:
    if value is None:
        return None
    return redact_pii(value, allowlist=get_settings().pii_allowlist)[0]


def context_key(ctx: QueryContext) -> str:
    """Stable cache key of a resolved query context (filters, days, test IPs)."""
    raw = "|".join(
        (
            ctx.filters.model_dump_json(),
            str(ctx.date_from),
            str(ctx.date_to),
            ",".join(ctx.test_ips),
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def invalidate_insights_cache() -> int:
    """Drop every cached insights aggregate (taxonomy edits, reclassification)."""
    return dashboard_cache.invalidate_prefix(INSIGHTS_CACHE_PREFIX)


def dominant_outcome(counts: dict[str, tuple[int, Any]]) -> str:
    """Most common outcome; ties go to the one seen first (``bubble()``)."""
    return min(counts.items(), key=lambda kv: (-kv[1][0], kv[1][1]))[0]


@dataclass(frozen=True, slots=True)
class Labels:
    """Taxonomy lookups by (kind, code), archived entries included."""

    entries: dict[tuple[str, str], Any]

    def get(self, kind: TaxonomyKind, code: str | None) -> Any | None:
        if code is None:
            return None
        return self.entries.get((kind.value, code))

    def label(self, kind: TaxonomyKind, code: str | None) -> str | None:
        entry = self.get(kind, code)
        return entry.label if entry is not None else code


@dataclass(frozen=True, slots=True)
class Cube:
    rows: list[CubeRow]
    source: Source

    def substantive(self) -> Iterable[CubeRow]:
        return (r for r in self.rows if is_substantive(r.intent))


class InsightsAnalyticsService:
    def __init__(self, db: AsyncSession, runner: ParallelRunner) -> None:
        self.db = db
        self.runner = runner
        self.settings = get_settings()

    # -- context ------------------------------------------------------------

    async def context(self, filters: InsightsFilters) -> QueryContext:
        repo = InsightsAnalyticsRepository(self.db)
        today = await repo.today(filters.tz)
        date_from, date_to = resolve_range(filters, today)
        test_ips: tuple[str, ...] = ()
        if filters.audience is not Audience.OVERVIEW:
            ips, _users, _as_of = await get_classified_ips(self.db)
            test_ips = tuple(sorted(ips))
        return QueryContext(
            filters=filters,
            date_from=date_from,
            date_to=date_to,
            salt=person_salt(self.settings.session_secret.get_secret_value()),
            test_ips=test_ips,
        )

    async def labels(self, session: AsyncSession | None = None) -> Labels:
        rows = await InsightsAnalyticsRepository(session or self.db).taxonomy_rows()
        return Labels({(row.kind, row.code): row for row in rows})

    # -- caching ------------------------------------------------------------

    async def _one(self, task: SessionTask) -> Any:
        """Run ``task`` on its own short-lived session (safe to share / refresh)."""
        (result,) = await self.runner(task)
        return result

    async def _cached(
        self, name: str, ctx: QueryContext, compute: Callable[[], Awaitable[T]]
    ) -> T:
        """Every aggregate is cached per resolved context (stale-while-revalidate).

        The key holds the resolved day range, so relative periods roll over at
        midnight, and the audience's test IPs, so a classification change
        never serves another audience's numbers.
        """
        # The work runs on its own sessions: hand the request's connection
        # back to the pool instead of holding it idle meanwhile.
        await release_connection(self.db)
        return await dashboard_cached(
            f"{INSIGHTS_CACHE_PREFIX}{name}:{context_key(ctx)}", compute
        )

    async def _cube(self, ctx: QueryContext) -> Cube:
        """The exchange cube, computed once per context for summary, trend,
        topics and quality (each used to rebuild it on every request)."""
        return await self._cached(
            "cube", ctx, lambda: self._one(lambda s: self.cube(s, ctx))
        )

    def rollup_allowed(self, filters: InsightsFilters) -> bool:
        return (
            filters.period is Period.ALL
            and not filters.has_conversation_filters
            and filters.audience is Audience.OVERVIEW
            and filters.tz == self.settings.insights_timezone
        )

    async def cube(self, session: AsyncSession, ctx: QueryContext) -> Cube:
        """Exchange counts per (day, intent, quality) in scope."""
        repo = InsightsAnalyticsRepository(session)
        if self.rollup_allowed(ctx.filters):
            today, fresh = await repo.rollup_status(ctx.filters.tz)
            if fresh:
                closed = await repo.rollup_cube(ctx, today)
                live = await repo.live_cube(ctx, since=today)
                return Cube([*closed, *live], "rollup")
        return Cube(await repo.live_cube(ctx), "live")

    # -- endpoints ----------------------------------------------------------
    # Each public method resolves the context (cheap) and returns the cached
    # aggregate; the ``_compute_*`` twin does the SQL on its own sessions.

    async def summary(self, filters: InsightsFilters) -> InsightsSummary:
        ctx = await self.context(filters)
        return await self._cached("summary", ctx, lambda: self._compute_summary(ctx))

    async def _compute_summary(self, ctx: QueryContext) -> InsightsSummary:
        async def kpis(session: AsyncSession) -> Any:
            return await InsightsAnalyticsRepository(session).conversation_kpis(ctx)

        async def pending(session: AsyncSession) -> int:
            return await InsightsAnalyticsRepository(session).pending_count()

        cube = await self._cube(ctx)
        conv, pending_count = await self.runner(kpis, pending)
        subs = list(cube.substantive())
        questions = sum(r.n for r in subs)
        handled = sum(r.handled for r in subs)
        active = int(conv.active)
        return InsightsSummary(
            range=DateRange(date_from=ctx.date_from, date_to=ctx.date_to),
            conversations=int(conv.conversations),
            exchanges=sum(r.n for r in cube.rows),
            handled_exchanges=sum(r.handled for r in cube.rows),
            substantive_questions=questions,
            handled_questions=handled,
            handled_pct=pct(handled, questions),
            served_conversations=int(conv.served),
            active_conversations=active,
            served_pct=pct(int(conv.served), active),
            people=int(conv.people),
            avg_questions_per_conversation=(
                round(int(conv.sum_sub) / active, PCT_DIGITS) if active else None
            ),
            pending_classification=int(pending_count),
            source=cube.source,
        )

    async def trend(self, filters: InsightsFilters) -> InsightsTrend:
        ctx = await self.context(filters)
        return await self._cached("trend", ctx, lambda: self._compute_trend(ctx))

    async def _compute_trend(self, ctx: QueryContext) -> InsightsTrend:
        async def bounds(session: AsyncSession) -> tuple[date | None, date | None]:
            if ctx.filters.period is Period.ALL:
                return await InsightsAnalyticsRepository(session).data_bounds(ctx)
            return ctx.date_from, ctx.date_to

        async def per_day(session: AsyncSession) -> dict[date, int]:
            return await InsightsAnalyticsRepository(session).conversations_per_day(ctx)

        cube = await self._cube(ctx)
        (lo, hi), per_day_conv = await self.runner(bounds, per_day)
        questions: Counter[date] = Counter()
        for row in cube.substantive():
            questions[row.day] += row.n
        days: list[TrendDay] = []
        if lo is not None and hi is not None:
            day = lo
            while day <= hi and len(days) < MAX_TREND_DAYS:
                days.append(
                    TrendDay(
                        day=day,
                        questions=questions.get(day, 0),
                        conversations=per_day_conv.get(day, 0),
                    )
                )
                day += timedelta(days=1)
        return InsightsTrend(
            range=DateRange(date_from=lo, date_to=hi), days=days, source=cube.source
        )

    async def hourly(self, filters: InsightsFilters) -> InsightsHourly:
        ctx = await self.context(filters)
        return await self._cached("hourly", ctx, lambda: self._compute_hourly(ctx))

    async def _compute_hourly(self, ctx: QueryContext) -> InsightsHourly:
        counts = await self._one(lambda s: InsightsAnalyticsRepository(s).hourly(ctx))
        hours = [counts.get(h, 0) for h in range(HOURS_PER_DAY)]
        total = sum(hours)
        return InsightsHourly(
            tz=ctx.filters.tz,
            hours=hours,
            total=total,
            peak_hour=hours.index(max(hours)) if total else None,
        )

    async def flow(self, filters: InsightsFilters) -> InsightsFlow:
        ctx = await self.context(filters)
        return await self._cached("flow", ctx, lambda: self._compute_flow(ctx))

    async def _compute_flow(self, ctx: QueryContext) -> InsightsFlow:
        rows = await self._one(lambda s: InsightsAnalyticsRepository(s).flow(ctx))
        by_type: Counter[str] = Counter()
        by_outcome: Counter[str] = Counter()
        links = []
        for row in sorted(rows, key=lambda r: (r.user_type, r.outcome)):
            by_type[row.user_type] += row.n
            by_outcome[row.outcome] += row.n
            links.append(
                FlowLink(
                    user_type=row.user_type,
                    outcome=ConversationOutcome(row.outcome),
                    conversations=row.n,
                )
            )
        return InsightsFlow(
            total=sum(by_type.values()),
            user_types=[
                FlowNode(name=name, conversations=n)
                for name, n in sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            outcomes=[
                FlowNode(name=o.value, conversations=by_outcome[o.value])
                for o in OUTCOME_ORDER
                if by_outcome[o.value]
            ],
            links=links,
        )

    async def topics(self, filters: InsightsFilters) -> InsightsTopics:
        ctx = await self.context(filters)
        return await self._cached("topics", ctx, lambda: self._compute_topics(ctx))

    async def _compute_topics(self, ctx: QueryContext) -> InsightsTopics:
        cube = await self._cube(ctx)
        labels = await self._one(self.labels)
        questions: Counter[str] = Counter()
        handled: Counter[str] = Counter()
        for row in cube.substantive():
            questions[row.intent] += row.n
            handled[row.intent] += row.handled
        items = []
        for intent, n in sorted(questions.items(), key=lambda kv: (-kv[1], kv[0])):
            entry = labels.get(TaxonomyKind.INTENT, intent)
            items.append(
                TopicItem(
                    intent=intent,
                    label=entry.label if entry is not None else intent,
                    group=entry.group if entry is not None else None,
                    questions=n,
                    handled=handled[intent],
                    handled_pct=pct(handled[intent], n) or 0.0,
                )
            )
        return InsightsTopics(
            total=sum(questions.values()), items=items, source=cube.source
        )

    async def quality(self, filters: InsightsFilters) -> InsightsQuality:
        ctx = await self.context(filters)
        return await self._cached("quality", ctx, lambda: self._compute_quality(ctx))

    async def _compute_quality(self, ctx: QueryContext) -> InsightsQuality:
        cube = await self._cube(ctx)
        labels = await self._one(self.labels)
        counts: Counter[str] = Counter()
        for row in cube.substantive():
            counts[row.quality] += row.n
        total = sum(counts.values())
        items = []
        for code in QUALITY_ORDER:
            if not counts[code]:
                continue
            entry = labels.get(TaxonomyKind.BOT_QUALITY, code)
            items.append(
                QualityItem(
                    quality=code,
                    label=entry.label if entry is not None else code,
                    color=entry.color if entry is not None else None,
                    questions=counts[code],
                    pct=pct(counts[code], total) or 0.0,
                )
            )
        return InsightsQuality(total=total, items=items, source=cube.source)

    async def dropoff(self, filters: InsightsFilters) -> InsightsDropoff:
        ctx = await self.context(filters)
        return await self._cached("dropoff", ctx, lambda: self._compute_dropoff(ctx))

    async def _compute_dropoff(self, ctx: QueryContext) -> InsightsDropoff:
        rows = await self._one(lambda s: InsightsAnalyticsRepository(s).dropoff(ctx))
        items = sorted(
            (
                DropoffItem(issue=r.issue, label=r.label or r.issue, conversations=r.n)
                for r in rows
            ),
            key=lambda i: (-i.conversations, i.label),
        )
        return InsightsDropoff(total=sum(i.conversations for i in items), items=items)

    async def people(self, filters: InsightsFilters) -> InsightsPeople:
        ctx = await self.context(filters)
        return await self._cached("people", ctx, lambda: self._compute_people(ctx))

    async def _compute_people(self, ctx: QueryContext) -> InsightsPeople:
        rows = await self._one(lambda s: InsightsAnalyticsRepository(s).people(ctx))
        per: dict[str, dict[str, tuple[int, Any]]] = defaultdict(dict)
        numbers: dict[str, int] = {}
        for row in rows:
            per[row.person_key][row.outcome] = (row.n, row.first_ts)
            numbers[row.person_key] = row.pn
        items = [
            PersonItem(
                person=person_label(numbers[key]),
                person_id=key,
                conversations=sum(n for n, _ in outcomes.values()),
                dominant_outcome=ConversationOutcome(dominant_outcome(outcomes)),
                outcomes={o: n for o, (n, _) in outcomes.items()},
            )
            for key, outcomes in per.items()
        ]
        items.sort(key=lambda p: numbers[p.person_id])
        return InsightsPeople(total=len(items), items=items)

    async def conversations(
        self, filters: InsightsFilters, page: ConversationPage
    ) -> ConversationList:
        ctx = await self.context(filters)
        name = (
            f"conversations:{page.sort.value}:{page.direction.value}"
            f":{page.limit}:{page.offset}"
        )
        return await self._cached(
            name, ctx, lambda: self._compute_conversations(ctx, page)
        )

    async def _compute_conversations(
        self, ctx: QueryContext, page: ConversationPage
    ) -> ConversationList:
        rows, total = await self._one(
            lambda s: InsightsAnalyticsRepository(s).conversations(ctx, page)
        )
        return ConversationList(
            items=[self._row(r) for r in rows],
            total=total,
            limit=page.limit,
            offset=page.offset,
            sort=page.sort,
            direction=page.direction,
        )

    async def conversation(
        self, session_id: uuid.UUID, filters: InsightsFilters
    ) -> ConversationDetail | None:
        detail_filters = InsightsFilters(audience=filters.audience, tz=filters.tz)
        ctx = await self.context(detail_filters)
        repo = InsightsAnalyticsRepository(self.db)
        row = await repo.conversation(ctx, session_id)
        turns = await repo.turns(ctx, session_id)
        if row is None and not turns:
            return None
        labels = await self.labels()
        return ConversationDetail(
            conversation=self._row(row, with_person=False) if row else None,
            turns=[self._turn(t, labels) for t in turns],
        )

    async def pending(self) -> PendingClassification:
        repo = InsightsAnalyticsRepository(self.db)
        pending = await repo.pending_count()
        labelled = await repo.labelled_count()
        total = pending + labelled
        return PendingClassification(
            pending=pending,
            labelled=labelled,
            total=total,
            progress_pct=pct(labelled, total),
        )

    # -- mapping ------------------------------------------------------------

    @staticmethod
    def _row(row: Any, *, with_person: bool = True) -> ConversationRow:
        minutes = (row.end_ts - row.start_ts).total_seconds() / 60
        return ConversationRow(
            conv_id=row.session_id,
            person=person_label(row.pn) if with_person else None,
            person_id=row.person_key,
            start=row.start_ts,
            end=row.end_ts,
            minutes=round(minutes, 1),
            user_type=row.user_type,
            outcome=row.outcome,
            experience_rating=row.rating,
            issue=row.issue,
            issue_label=row.issue_label or row.issue,
            failing_question=mask(row.failing_question),
            exchanges=row.exchanges,
            substantive_exchanges=row.substantive,
        )

    @staticmethod
    def _turn(row: Any, labels: Labels) -> TurnDetail:
        pending = row.intent is None
        return TurnDetail(
            turn=row.turn_index,
            timestamp=row.ts,
            user_message=mask(row.user_message) or "",
            intent=row.intent,
            intent_label=labels.label(TaxonomyKind.INTENT, row.intent),
            substantive=None if pending else is_substantive(row.intent),
            handled=row.handled,
            quality=row.quality,
            quality_label=labels.label(TaxonomyKind.BOT_QUALITY, row.quality),
            issue=row.issue,
            issue_label=labels.label(TaxonomyKind.ISSUE, row.issue),
            friction=row.friction,
            classified_by=row.classified_by,
            pending=pending,
        )
