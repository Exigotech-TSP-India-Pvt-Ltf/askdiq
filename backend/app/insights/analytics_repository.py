"""SQL for the insights dashboard (newplan 6.4, the reference HTML's formulas).

One CTE chain reproduces the renderer's data model from our tables:

- ``ex``: one row per labelled exchange (user message + its assistant reply's
  ``turn_insights``), with the timestamp in the viewer's zone. ``is_sub`` is
  the HTML's ``isSub()``: intent not ``GRE``/``ACK``.
- ``conv``: one row per conversation (chat session). ``user_type``,
  ``outcome``, ``experience_rating``, ``issue`` and ``failing_question`` are
  computed here at query time from the labels; nothing is stored twice.
- ``scoped``: conversations passing ``passConv()``; ``scoped_ex``: their
  exchanges passing ``scope()`` (date range, intent and quality narrow the
  exchange set too).

Only fixed SQL fragments are concatenated; every user value is a bind
parameter and the ORDER BY column comes from an allow-list.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.governance.usage_queries import escape_ilike_pattern
from app.insights.repository import ROLLUP_OVERLAP
from app.models.db_models import InsightTaxonomy, TurnInsight
from app.schemas.insights import (
    Audience,
    ConversationPage,
    ConversationSort,
    InsightsFilters,
    Period,
    SortDirection,
)

# A conversation's user type is the intent group holding at least this share
# of its substantive turns, otherwise "Mixed" (newplan 6.4).
USER_TYPE_DOMINANCE_PCT = 60
PERIOD_DAYS = {Period.LAST_7_DAYS: 7, Period.LAST_30_DAYS: 30}
PERSON_SALT_CONTEXT = b"askdiq-insights-person:"

_EX = """
ex AS (
    SELECT ti.session_id,
           u.turn_index,
           (u.created_at AT TIME ZONE 'UTC') AT TIME ZONE CAST(:tz AS text) AS ts,
           u.content AS user_message,
           ti.intent,
           ti.bot_quality AS quality,
           ti.issue,
           ti.handled,
           ti.friction,
           ti.intent NOT IN ('GRE', 'ACK') AS is_sub,
           COALESCE(tx."group", 'Other') AS grp
    FROM turn_insights ti
    JOIN chat_sessions cs ON cs.id = ti.session_id
    JOIN chat_messages a ON a.id = ti.message_id
    JOIN chat_messages u
      ON u.session_id = a.session_id
     AND u.turn_index = a.turn_index
     AND u.role = 'user'
    LEFT JOIN insight_taxonomy tx ON tx.kind = 'intent' AND tx.code = ti.intent
    WHERE TRUE {ex_where}
)"""

# Lean variants (no text, groups or feedback): used when no filter needs a
# conversation-level field, e.g. the KPI strip and exchange counts.
_EX_LEAN = """
ex AS (
    SELECT ti.session_id,
           (u.created_at AT TIME ZONE 'UTC') AT TIME ZONE CAST(:tz AS text) AS ts,
           ti.intent,
           ti.bot_quality AS quality,
           ti.handled,
           ti.intent NOT IN ('GRE', 'ACK') AS is_sub
    FROM turn_insights ti
    JOIN chat_sessions cs ON cs.id = ti.session_id
    JOIN chat_messages a ON a.id = ti.message_id
    JOIN chat_messages u
      ON u.session_id = a.session_id
     AND u.turn_index = a.turn_index
     AND u.role = 'user'
    WHERE TRUE {ex_where}
)"""

_CONV_LEAN = """
sess AS (
    SELECT session_id,
           min(ts) AS start_ts,
           count(*) FILTER (WHERE is_sub) AS substantive,
           count(*) FILTER (WHERE is_sub AND handled) AS handled
    FROM ex
    GROUP BY session_id
),
conv AS (
    SELECT s.*,
           left(encode(sha256(convert_to(
               CAST(:salt AS text) || COALESCE(
                   NULLIF(lower(cs.user_email), ''),
                   'ip:' || cs.created_ip,
                   'sid:' || CAST(cs.id AS text)),
               'UTF8')), 'hex'), 16) AS person_key,
           CASE WHEN s.substantive = 0 THEN 'Greeting only'
                WHEN s.handled = s.substantive THEN 'Served'
                WHEN s.handled = 0 THEN 'Not served'
                ELSE 'Partly served' END AS outcome
    FROM sess s
    JOIN chat_sessions cs ON cs.id = s.session_id
),
scoped AS (
    SELECT * FROM conv c WHERE TRUE {conv_where}
),
scoped_ex AS (
    SELECT ex.* FROM ex JOIN scoped USING (session_id) WHERE TRUE {scoped_ex_where}
)"""

_CONV = """
sess AS (
    SELECT session_id,
           min(ts) AS start_ts,
           max(ts) AS end_ts,
           count(*) AS exchanges,
           count(*) FILTER (WHERE is_sub) AS substantive,
           count(*) FILTER (WHERE is_sub AND handled) AS handled,
           bool_or(friction IS NOT NULL) AS friction,
           (array_agg(issue ORDER BY turn_index DESC)
                FILTER (WHERE is_sub AND NOT handled))[1] AS issue,
           (array_agg(user_message ORDER BY turn_index DESC)
                FILTER (WHERE is_sub AND NOT handled))[1] AS failing_question,
           string_agg(user_message, ' | ' ORDER BY turn_index) AS questions
    FROM ex
    GROUP BY session_id
),
top_grp AS (
    SELECT DISTINCT ON (session_id) session_id, grp, n
    FROM (
        SELECT session_id, grp, count(*) AS n
        FROM ex WHERE is_sub GROUP BY session_id, grp
    ) g
    ORDER BY session_id, n DESC, grp
),
fb AS (
    SELECT session_id, min(rating) AS fb_min, max(rating) AS fb_max
    FROM message_feedback
    GROUP BY session_id
),
conv0 AS (
    SELECT s.*,
           left(encode(sha256(convert_to(
               CAST(:salt AS text) || COALESCE(
                   NULLIF(lower(cs.user_email), ''),
                   'ip:' || cs.created_ip,
                   'sid:' || CAST(cs.id AS text)),
               'UTF8')), 'hex'), 16) AS person_key,
           CASE WHEN s.substantive = 0 THEN 'Greeting only'
                WHEN s.handled = s.substantive THEN 'Served'
                WHEN s.handled = 0 THEN 'Not served'
                ELSE 'Partly served' END AS outcome,
           CASE WHEN s.substantive = 0 THEN 'Greeting only'
                WHEN g.n * 100 >= s.substantive * CAST(:dominance AS integer)
                    THEN g.grp
                ELSE 'Mixed' END AS user_type,
           fb.fb_min,
           fb.fb_max,
           it.label AS issue_label
    FROM sess s
    JOIN chat_sessions cs ON cs.id = s.session_id
    LEFT JOIN top_grp g ON g.session_id = s.session_id
    LEFT JOIN fb ON fb.session_id = s.session_id
    LEFT JOIN insight_taxonomy it ON it.kind = 'issue' AND it.code = s.issue
),
conv AS (
    SELECT c.*,
           CASE WHEN c.fb_min <= 2 THEN 'Dropped'
                WHEN c.outcome = 'Served' AND c.fb_max = 5 THEN 'Great'
                WHEN c.outcome = 'Served' AND c.friction THEN 'Served with friction'
                WHEN c.outcome = 'Served' THEN 'Good'
                WHEN c.outcome = 'Partly served' THEN 'Partly served'
                WHEN c.outcome = 'Not served' THEN 'Dropped'
                ELSE 'No question asked' END AS rating
    FROM conv0 c
),
scoped AS (
    SELECT * FROM conv c WHERE TRUE {conv_where}
),
scoped_ex AS (
    SELECT ex.* FROM ex JOIN scoped USING (session_id) WHERE TRUE {scoped_ex_where}
)"""

_PERSON_LABELS = """,
plabel AS (
    SELECT person_key,
           row_number() OVER (ORDER BY min(start_ts), person_key) AS pn
    FROM scoped GROUP BY person_key
)"""

_SORT_SQL: dict[ConversationSort, str] = {
    ConversationSort.CONV_ID: "CAST(s.session_id AS text)",
    ConversationSort.PERSON: "p.pn",
    ConversationSort.START: "s.start_ts",
    ConversationSort.USER_TYPE: "s.user_type",
    ConversationSort.OUTCOME: "s.outcome",
    ConversationSort.EXPERIENCE_RATING: "s.rating",
    ConversationSort.ISSUE: "COALESCE(s.issue_label, '')",
    ConversationSort.EXCHANGES: "s.exchanges",
}
_DIRECTION_SQL = {SortDirection.ASC: "ASC", SortDirection.DESC: "DESC"}

_CONV_IN_RANGE = "CAST(c.start_ts AS date) BETWEEN :date_from AND :date_to"
_EX_IN_RANGE = "CAST(ex.ts AS date) BETWEEN :date_from AND :date_to"
_CONV_SEARCH = (
    "concat_ws(' ', CAST(c.session_id AS text), c.user_type, c.issue_label, "
    "c.failing_question, c.questions) ILIKE :q"
)
_ANY_TURN = (
    "EXISTS (SELECT 1 FROM ex e2 WHERE e2.session_id = c.session_id AND {match})"
)

_KPIS = """
    SELECT count(*) AS conversations,
           count(*) FILTER (WHERE outcome <> 'Greeting only') AS active,
           count(*) FILTER (WHERE outcome = 'Served') AS served,
           count(DISTINCT person_key) AS people,
           COALESCE(sum(substantive)
               FILTER (WHERE outcome <> 'Greeting only'), 0) AS sum_sub
    FROM scoped
"""
_CONVERSATIONS_PER_DAY = """
    SELECT CAST(start_ts AS date) AS day, count(*) AS n FROM scoped GROUP BY 1
"""
_BOUNDS = """
    SELECT min(CAST(ts AS date)) AS lo, max(CAST(ts AS date)) AS hi FROM ex
"""
_FLOW = """
    SELECT user_type, outcome, count(*) AS n
    FROM scoped WHERE outcome <> 'Greeting only'
    GROUP BY user_type, outcome
"""
_DROPOFF = """
    SELECT issue, max(issue_label) AS label, count(*) AS n
    FROM scoped
    WHERE issue IS NOT NULL AND outcome IN ('Partly served', 'Not served')
    GROUP BY issue
"""
_PEOPLE = """
    SELECT s.person_key, p.pn, s.outcome, count(*) AS n, min(s.start_ts) AS first_ts
    FROM scoped s JOIN plabel p USING (person_key)
    GROUP BY s.person_key, p.pn, s.outcome
"""
_CONVERSATION_PAGE = """
    SELECT
    s.session_id, s.person_key, p.pn, s.start_ts, s.end_ts, s.user_type,
    s.outcome, s.rating, s.issue, s.issue_label, s.failing_question,
    s.exchanges, s.substantive,
           count(*) OVER () AS total
    FROM scoped s JOIN plabel p USING (person_key)
    ORDER BY {order} {direction}, s.session_id {direction}
    LIMIT :limit OFFSET :offset
"""
_CONVERSATION_ONE = """
    SELECT
    s.session_id, s.person_key, p.pn, s.start_ts, s.end_ts, s.user_type,
    s.outcome, s.rating, s.issue, s.issue_label, s.failing_question,
    s.exchanges, s.substantive
    FROM scoped s JOIN plabel p USING (person_key)
"""
_COUNT_SCOPED = " SELECT count(*) AS n FROM scoped"
_LIVE_CUBE = """
    SELECT CAST(ts AS date) AS day, intent, quality,
           count(*) AS n, count(*) FILTER (WHERE handled) AS handled
    FROM scoped_ex
    GROUP BY 1, 2, 3
"""
# Index-friendly lower bound on the naive-UTC column for local day ``since``.
_EX_SINCE = (
    " AND u.created_at >= (CAST(CAST(:since AS date) AS timestamp)"
    " AT TIME ZONE CAST(:tz AS text)) AT TIME ZONE 'UTC'"
)
_HOURLY = """
    SELECT CAST(extract(hour FROM ts) AS integer) AS hour, count(*) AS n
    FROM scoped_ex WHERE is_sub GROUP BY 1
"""

_ROLLUP_FRESH_SQL = text(
    """
    WITH mark AS (
        SELECT max(computed_at) - CAST(:overlap AS interval) AS ts
        FROM insight_daily_rollup
    )
    SELECT CAST(now() AT TIME ZONE CAST(:tz AS text) AS date) AS today,
           (SELECT ts FROM mark) IS NOT NULL
           AND NOT EXISTS (
               SELECT 1
               FROM turn_insights ti
               JOIN chat_messages a ON a.id = ti.message_id
               WHERE ti.classified_at > (SELECT ts FROM mark)
                 AND CAST((a.created_at AT TIME ZONE 'UTC')
                          AT TIME ZONE CAST(:tz AS text) AS date)
                     < CAST(now() AT TIME ZONE CAST(:tz AS text) AS date)
           ) AS fresh
    """
)

_ROLLUP_CUBE_SQL = """
    SELECT day, intent, quality,
           CAST(sum(count) AS integer) AS n,
           CAST(sum(handled) AS integer) AS handled
    FROM insight_daily_rollup
    WHERE day < :today {where}
    GROUP BY day, intent, quality
"""

_TURNS_SQL = """
    SELECT u.turn_index,
           (u.created_at AT TIME ZONE 'UTC') AT TIME ZONE CAST(:tz AS text) AS ts,
           u.content AS user_message,
           ti.intent, ti.bot_quality AS quality, ti.issue, ti.handled,
           ti.friction, ti.classified_by
    FROM chat_messages u
    JOIN chat_sessions cs ON cs.id = u.session_id
    LEFT JOIN chat_messages a
      ON a.session_id = u.session_id
     AND a.turn_index = u.turn_index
     AND a.role = 'assistant'
    LEFT JOIN turn_insights ti ON ti.message_id = a.id
    WHERE u.session_id = :session_id AND u.role = 'user' {audience}
    ORDER BY u.turn_index, u.created_at
"""


def person_salt(secret: str) -> str:
    """Salt for the person pseudonym, derived from the server secret.

    A bare sha256 of an e-mail or IPv4 address is reversible by brute force;
    salting keeps the pseudonym stable but not linkable outside this server.
    """
    return hashlib.sha256(PERSON_SALT_CONTEXT + secret.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CubeRow:
    """Exchange counts per (day, intent, quality): the rollup's grain."""

    day: date
    intent: str
    quality: str
    n: int
    handled: int


@dataclass(frozen=True, slots=True)
class QueryContext:
    """Resolved filters plus the server-side values the SQL needs."""

    filters: InsightsFilters
    date_from: date | None
    date_to: date | None
    salt: str
    test_ips: tuple[str, ...]


def resolve_range(
    filters: InsightsFilters, today: date
) -> tuple[date | None, date | None]:
    """Concrete date bounds; ``all`` is unbounded (the HTML's MIN..MAX)."""
    if filters.period is Period.CUSTOM:
        return filters.date_from, filters.date_to
    days = PERIOD_DAYS.get(filters.period)
    if days is None:
        return None, None
    return today - timedelta(days=days - 1), today


class InsightsAnalyticsRepository:
    """Read-only analytics queries; the caller owns the session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- SQL assembly -------------------------------------------------------

    @staticmethod
    def _params(ctx: QueryContext) -> dict[str, Any]:
        f = ctx.filters
        params: dict[str, Any] = {
            "tz": f.tz,
            "salt": ctx.salt,
            "dominance": USER_TYPE_DOMINANCE_PCT,
            "test_ips": list(ctx.test_ips),
            "date_from": ctx.date_from,
            "date_to": ctx.date_to,
            "user_type": f.user_type,
            "outcome": f.outcome.value if f.outcome else None,
            "rating": f.rating.value if f.rating else None,
            "issue": f.issue,
            "intent": f.intent,
            "quality": f.quality,
            "person": f.person,
        }
        if f.q:
            params["q"] = f"%{escape_ilike_pattern(f.q)}%"
        return params

    @staticmethod
    def _audience_sql(audience: Audience) -> str:
        if audience is Audience.TESTING:
            return (
                " AND (cs.created_ip IS NULL"
                " OR cs.created_ip = ANY(CAST(:test_ips AS text[])))"
            )
        if audience is Audience.USERS:
            return (
                " AND cs.created_ip IS NOT NULL"
                " AND NOT (cs.created_ip = ANY(CAST(:test_ips AS text[])))"
            )
        return ""

    @classmethod
    def _select(
        cls,
        ctx: QueryContext,
        tail: str,
        *,
        session_id: uuid.UUID | None = None,
        person_labels: bool = False,
        lean: bool = False,
        since: date | None = None,
    ) -> str:
        """The CTE chain for ``ctx`` followed by the final ``tail`` SELECT.

        ``lean`` skips the conversation text, user type and rating (only valid
        without conversation-level filters); ``since`` keeps only exchanges
        from that local day on.

        Only module-level SQL fragments are concatenated here; filter values
        are bind parameters and the sort column comes from ``_SORT_SQL``.
        """
        f = ctx.filters
        ex_where = cls._audience_sql(f.audience)
        if session_id is not None:
            ex_where += " AND ti.session_id = :session_id"
        if since is not None:
            ex_where += _EX_SINCE

        conv_where: list[str] = []
        ex_filters: list[str] = []
        if ctx.date_from is not None:
            conv_where.append(_CONV_IN_RANGE)
            ex_filters.append(_EX_IN_RANGE)
        for value, clause in (
            (f.user_type, "c.user_type = :user_type"),
            (f.outcome, "c.outcome = :outcome"),
            (f.rating, "c.rating = :rating"),
            (f.issue, "c.issue = :issue"),
            (f.person, "c.person_key = :person"),
            (f.q, _CONV_SEARCH),
        ):
            if value is not None:
                conv_where.append(clause)
        turn_match: list[str] = []
        if f.intent:
            turn_match.append("e2.intent = :intent")
            ex_filters.append("ex.intent = :intent")
        if f.quality:
            turn_match.append("e2.quality = :quality")
            ex_filters.append("ex.quality = :quality")
        if turn_match:
            conv_where.append(_ANY_TURN.format(match=" AND ".join(turn_match)))

        if lean and f.has_conversation_filters:
            raise ValueError("lean queries cannot apply conversation filters")
        ex_sql, conv_sql = (_EX_LEAN, _CONV_LEAN) if lean else (_EX, _CONV)
        ctes = (
            ex_sql.format(ex_where=ex_where)
            + ","
            + conv_sql.format(
                conv_where="".join(f" AND {c}" for c in conv_where),
                scoped_ex_where="".join(f" AND {c}" for c in ex_filters),
            )
        )
        if person_labels:
            ctes += _PERSON_LABELS
        return "WITH " + ctes + tail

    @staticmethod
    def _lean(ctx: QueryContext) -> bool:
        """Exchange counts and KPIs need no conversation text unless filtered on."""
        return not ctx.filters.has_conversation_filters

    async def _rows(self, sql: str, params: dict[str, Any]) -> Sequence[Any]:
        return (await self.session.execute(text(sql), params)).all()

    async def _select_rows(
        self,
        ctx: QueryContext,
        tail: str,
        *,
        params: dict[str, Any] | None = None,
        session_id: uuid.UUID | None = None,
        person_labels: bool = False,
        lean: bool = False,
        since: date | None = None,
    ) -> Sequence[Any]:
        sql = self._select(
            ctx,
            tail,
            session_id=session_id,
            person_labels=person_labels,
            lean=lean,
            since=since,
        )
        params = {**(params or {}), "since": since}
        return await self._rows(sql, {**self._params(ctx), **(params or {})})

    # -- time ---------------------------------------------------------------

    async def today(self, tz: str) -> date:
        return await self.session.scalar(
            text("SELECT CAST(now() AT TIME ZONE CAST(:tz AS text) AS date)"),
            {"tz": tz},
        )

    # -- conversation-level -------------------------------------------------

    async def conversation_kpis(self, ctx: QueryContext) -> Any:
        return (await self._select_rows(ctx, _KPIS, lean=self._lean(ctx)))[0]

    async def conversations_per_day(self, ctx: QueryContext) -> dict[date, int]:
        rows = await self._select_rows(
            ctx, _CONVERSATIONS_PER_DAY, lean=self._lean(ctx)
        )
        return {r.day: r.n for r in rows}

    async def data_bounds(self, ctx: QueryContext) -> tuple[date | None, date | None]:
        """First and last exchange day (the HTML's MIN and MAX)."""
        sql = "WITH " + _EX.format(ex_where=self._audience_sql(ctx.filters.audience))
        row = (await self._rows(sql + _BOUNDS, self._params(ctx)))[0]
        return row.lo, row.hi

    async def flow(self, ctx: QueryContext) -> Sequence[Any]:
        return await self._select_rows(ctx, _FLOW)

    async def dropoff(self, ctx: QueryContext) -> Sequence[Any]:
        return await self._select_rows(ctx, _DROPOFF)

    async def people(self, ctx: QueryContext) -> Sequence[Any]:
        return await self._select_rows(ctx, _PEOPLE, person_labels=True)

    async def conversations(
        self, ctx: QueryContext, page: ConversationPage
    ) -> tuple[Sequence[Any], int]:
        tail = _CONVERSATION_PAGE.format(
            order=_SORT_SQL[page.sort], direction=_DIRECTION_SQL[page.direction]
        )
        rows = await self._select_rows(
            ctx,
            tail,
            person_labels=True,
            params={"limit": page.limit, "offset": page.offset},
        )
        if rows:
            return rows, int(rows[0].total)
        total = (await self._select_rows(ctx, _COUNT_SCOPED))[0].n
        return rows, int(total)

    async def conversation(self, ctx: QueryContext, session_id: uuid.UUID) -> Any:
        rows = await self._select_rows(
            ctx,
            _CONVERSATION_ONE,
            session_id=session_id,
            person_labels=True,
            params={"session_id": session_id},
        )
        return rows[0] if rows else None

    async def turns(self, ctx: QueryContext, session_id: uuid.UUID) -> Sequence[Any]:
        sql = _TURNS_SQL.format(audience=self._audience_sql(ctx.filters.audience))
        params = {
            "tz": ctx.filters.tz,
            "session_id": session_id,
            "test_ips": list(ctx.test_ips),
        }
        return await self._rows(sql, params)

    # -- exchange-level -----------------------------------------------------

    async def live_cube(
        self, ctx: QueryContext, *, since: date | None = None
    ) -> list[CubeRow]:
        """Scoped exchanges per (day, intent, quality), optionally from ``since``."""
        rows = await self._select_rows(
            ctx, _LIVE_CUBE, lean=self._lean(ctx), since=since
        )
        return [CubeRow(r.day, r.intent, r.quality, r.n, r.handled) for r in rows]

    async def rollup_status(self, tz: str) -> tuple[date, bool]:
        """(today in ``tz``, whether every closed day's rollup is up to date)."""
        row = (
            await self.session.execute(
                _ROLLUP_FRESH_SQL, {"tz": tz, "overlap": ROLLUP_OVERLAP}
            )
        ).one()
        return row.today, bool(row.fresh)

    async def rollup_cube(self, ctx: QueryContext, today: date) -> list[CubeRow]:
        where = ""
        if ctx.filters.intent:
            where += " AND intent = :intent"
        if ctx.filters.quality:
            where += " AND quality = :quality"
        params = {
            "today": today,
            "intent": ctx.filters.intent,
            "quality": ctx.filters.quality,
        }
        rows = await self._rows(_ROLLUP_CUBE_SQL.format(where=where), params)
        return [CubeRow(r.day, r.intent, r.quality, r.n, r.handled) for r in rows]

    async def hourly(self, ctx: QueryContext) -> dict[int, int]:
        rows = await self._select_rows(ctx, _HOURLY, lean=self._lean(ctx))
        return {r.hour: r.n for r in rows}

    # -- labels and progress ------------------------------------------------

    async def taxonomy_rows(self) -> list[InsightTaxonomy]:
        """Every taxonomy entry, archived ones included (history keeps labels)."""
        result = await self.session.execute(
            select(InsightTaxonomy).order_by(
                InsightTaxonomy.kind, InsightTaxonomy.sort_order, InsightTaxonomy.code
            )
        )
        return list(result.scalars())

    async def labelled_count(self) -> int:
        return int(await self.session.scalar(select(func.count(TurnInsight.id))) or 0)
