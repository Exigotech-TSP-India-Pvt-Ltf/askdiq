"""In-memory insights facts: every Analytics widget for any filters in milliseconds.

The per-widget endpoints (``analytics_service``) run one CTE chain per widget
and filter set: a new period, cross-filter or audience costs a second of SQL
per widget. Here the same CTE chain (``analytics_repository``) runs once per
time zone, *unfiltered*, and its rows are kept in memory:

- one :class:`ConvFact` per labelled conversation (the ``conv`` CTE: outcome,
  user type, rating, issue, person key, ...) with its exchanges (day, hour,
  intent, quality, handled), read in one statement so both always agree;
- one :class:`SessionFact` per chat session (person detail);
- the taxonomy labels, the classification backlog and the users' e-mails.

Any period, audience, cross-filter, search, sort or page is then a pass over
those rows (``passConv()`` / ``scope()`` of the reference HTML, exactly as the
SQL applies them), and the numbers equal the SQL endpoints'
(tests/test_insights_bundle.py). Text columns sort by ranks the database
computed (its collation), so the conversations table orders rows as SQL does.

Freshness: the facts are reloaded in the background when the console sees a
data change (``console.changed`` ``data``, every DASHBOARD_WATCH_SECONDS) and
at least every DASHBOARD_SNAPSHOT_REFRESH_SECONDS while read
(stale-while-revalidate); a reload announces ``insights.facts`` on the live
stream so open consoles refetch at once.
"""

from __future__ import annotations

import asyncio
import bisect
import contextlib
import hashlib
import itertools
import json
import time
import uuid
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import dashboard_cache
from app.core.concurrency import ScopedRunner
from app.core.config import get_settings
from app.core.events import TOPIC_CONSOLE_CHANGED, Event, get_event_bus, publish_event
from app.core.logging_config import get_logger
from app.dashboard.active_visitors import ANONYMOUS_ACTORS
from app.insights import analytics_repository as sql
from app.insights.analytics_repository import (
    InsightsAnalyticsRepository,
    person_salt,
    resolve_range,
    split_visitor,
    visitor_id,
)
from app.insights.analytics_service import (
    HOURS_PER_DAY,
    INSIGHTS_CACHE_PREFIX,
    MAX_TREND_DAYS,
    OUTCOME_ORDER,
    PCT_DIGITS,
    QUALITY_ORDER,
    dominant_outcome,
    is_substantive,
    mask,
    pct,
    person_label,
)
from app.insights.taxonomy import TaxonomyKind
from app.schemas.insights import (
    Audience,
    ConversationOutcome,
    ConversationPage,
    ConversationSort,
    InsightsFilters,
    Period,
    PersonIdentity,
    SortDirection,
)

logger = get_logger(__name__)

Gather = Callable[..., Awaitable[list[Any]]]

# ``dashboard_cache`` key of a zone's facts; the "insights:" prefix makes
# ``invalidate_insights_cache`` (taxonomy edits) drop them too.
FACTS_CACHE_PREFIX = f"{INSIGHTS_CACHE_PREFIX}facts:"
# Live-stream scope announced after a reload (frontend: "insights." scopes).
FACTS_SCOPE = "insights.facts"
# Console scope that means "the data changed" (app.dashboard.snapshots).
DATA_SCOPE = "data"
# Encoded bundle bodies kept per process.
BODY_CACHE_SIZE = 256

GREETING_ONLY = ConversationOutcome.GREETING_ONLY.value
DROPPED_OUTCOMES = frozenset(
    {ConversationOutcome.PARTLY_SERVED.value, ConversationOutcome.NOT_SERVED.value}
)
SERVED = ConversationOutcome.SERVED.value

# Every widget of the bundle; ``conversations`` is the table page.
PARTS = (
    "summary",
    "trend",
    "hourly",
    "flow",
    "topics",
    "quality",
    "dropoff",
    "people",
    "conversations",
    "pending",
)

# ---------------------------------------------------------------------------
# SQL (the repository's fragments, unfiltered)
# ---------------------------------------------------------------------------

# Per conversation: the ``conv`` CTE plus its exchanges as one JSON array (one
# statement, so conversations and exchanges always come from the same
# snapshot) and the database's sort ranks of the text columns (collation).
_CONV_FACTS_TEMPLATE = """
WITH {EX}, {CONV},
exj AS (
    SELECT session_id,
           json_agg(json_build_array(
               to_char(ts, 'YYYY-MM-DD'),
               CAST(extract(hour FROM ts) AS integer),
               intent, quality, handled, is_sub
           ) ORDER BY turn_index, ts) AS ex
    FROM ex GROUP BY session_id
)
SELECT c.session_id, c.start_ts, c.end_ts, c.exchanges, c.substantive,
       c.user_type, c.outcome, c.rating, c.issue, c.issue_label,
       c.failing_question, c.person_key, c.created_ip,
       NULLIF(lower(trim(cs.user_email)), '') AS email,
       concat_ws(' ', CAST(c.session_id AS text), c.user_type, c.issue_label,
                 c.failing_question, c.questions) AS search_text,
       dense_rank() OVER (ORDER BY CAST(c.session_id AS text)) AS r_conv_id,
       dense_rank() OVER (ORDER BY c.user_type) AS r_user_type,
       dense_rank() OVER (ORDER BY c.outcome) AS r_outcome,
       dense_rank() OVER (ORDER BY c.rating) AS r_rating,
       dense_rank() OVER (ORDER BY COALESCE(c.issue_label, '')) AS r_issue,
       CAST(exj.ex AS text) AS ex
FROM conv c
JOIN exj USING (session_id)
JOIN chat_sessions cs ON cs.id = c.session_id
"""

# Every chat session (labelled or not): the person detail's source.
_SESSION_FACTS_TEMPLATE = """
SELECT {PERSON_KEY} AS person_key,
       {IDENTITY} AS identity,
       cs.created_ip AS ip,
       NULLIF(lower(trim(cs.user_email)), '') AS email,
       cs.created_at AS created_raw,
       cs.last_activity_at AS last_raw,
       (cs.created_at AT TIME ZONE 'UTC') AT TIME ZONE CAST(:tz AS text)
           AS created_local,
       (cs.last_activity_at AT TIME ZONE 'UTC') AT TIME ZONE CAST(:tz AS text)
           AS last_local
FROM chat_sessions cs
"""


def _fill(template: str) -> str:
    """Substitute the repository's static SQL fragments by name (no user
    input ever reaches the SQL text; values are bind parameters)."""
    conv = sql._CONV.format(  # noqa: SLF001 - the shared CTE chain
        person_key=sql._PERSON_KEY,  # noqa: SLF001
        conv_where="",
        scoped_ex_where="",
    )
    return (
        template.replace("{EX}", sql._EX.format(ex_where=""))  # noqa: SLF001
        .replace("{CONV}", conv)
        .replace("{PERSON_KEY}", sql._PERSON_KEY)  # noqa: SLF001
        .replace("{IDENTITY}", sql._PERSON_IDENTITY)  # noqa: SLF001
    )


_CONV_FACTS_SQL = _fill(_CONV_FACTS_TEMPLATE)
_SESSION_FACTS_SQL = _fill(_SESSION_FACTS_TEMPLATE)

_USERS_SQL = "SELECT id, lower(email) AS email FROM users"
_LABELLED_SQL = "SELECT count(*) FROM turn_insights"


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExFact:
    """One labelled exchange (the ``ex`` CTE row)."""

    day: date
    hour: int
    intent: str | None
    quality: str | None
    # SQL ``count(*) FILTER (WHERE handled)``: NULL counts as not handled.
    handled: bool
    # SQL ``is_sub`` (``intent NOT IN ('GRE', 'ACK')``; NULL is false).
    is_sub: bool


@dataclass(frozen=True, slots=True)
class ConvFact:
    """One labelled conversation (the ``conv`` CTE row) and its exchanges,
    with the per-conversation totals every widget sums."""

    idx: int
    session_id: uuid.UUID
    start_ts: datetime
    end_ts: datetime
    start_day: date
    exchanges: int
    substantive: int
    user_type: str
    outcome: str
    rating: str
    issue: str | None
    issue_label: str | None
    failing_question: str | None
    person_key: str
    created_ip: str | None
    email: str | None
    search: str
    ranks: dict[ConversationSort, int]
    ex: tuple[ExFact, ...]
    # Exchange totals (``is_substantive`` of the intent, as the cube is read).
    handled_n: int
    sub_n: int
    sub_handled_n: int
    sub_intents: tuple[str | None, ...]
    sub_handled_intents: tuple[str | None, ...]
    sub_qualities: tuple[str | None, ...]
    sub_days: tuple[date, ...]
    # Hours of the SQL ``is_sub`` exchanges (the hour chart).
    sub_hours: tuple[int, ...]
    # Every exchange on one day (most conversations): that day, else None.
    one_day: date | None
    intents: frozenset[str | None]
    qualities: frozenset[str | None]
    hours: frozenset[int]


@dataclass(frozen=True, slots=True)
class SessionFact:
    """One chat session (person detail)."""

    person_key: str
    identity: str
    ip: str | None
    email: str | None
    created_raw: datetime
    last_raw: datetime
    created_local: datetime
    last_local: datetime


@dataclass(frozen=True, slots=True)
class Label:
    label: str
    group: str | None
    color: str | None


def _start_key(c: ConvFact) -> tuple[datetime, uuid.UUID]:
    return c.start_ts, c.session_id


class AudienceView:
    """The conversations of one audience (by start), their person numbers and
    lookup indexes (built on first use)."""

    def __init__(self, convs: list[ConvFact]) -> None:
        self.convs = sorted(convs, key=_start_key)
        self.start_days = [c.start_day for c in self.convs]
        first: dict[str, datetime] = {}
        count: Counter[str] = Counter()
        days = [d for c in self.convs for d in (c.one_day,) if d is not None]
        days += [e.day for c in self.convs if c.one_day is None for e in c.ex]
        # First and last exchange day (the HTML's MIN and MAX).
        self.bounds: tuple[date | None, date | None] = (
            (min(days), max(days)) if days else (None, None)
        )
        for c in self.convs:
            count[c.person_key] += 1
            if c.person_key not in first:
                first[c.person_key] = c.start_ts  # convs are in start order
        # ``row_number() OVER (ORDER BY min(start_ts), person_key)``.
        order = sorted(first, key=lambda k: (first[k], k))
        # person_key -> (P-number, conversations): ``plabel`` over the audience.
        self.numbers = {k: (n, count[k]) for n, k in enumerate(order, start=1)}
        self._indexes: dict[str, dict[Any, list[ConvFact]]] = {}
        self._orders: dict[ConversationSort, list[ConvFact]] = {}

    def index(self, name: str) -> dict[Any, list[ConvFact]]:
        """Conversations per value of ``name`` (a field, or ``intents`` /
        ``qualities`` / ``hours``: any exchange with that value)."""
        hit = self._indexes.get(name)
        if hit is None:
            hit = defaultdict(list)
            for c in self.convs:
                value = getattr(c, name)
                if isinstance(value, frozenset):
                    for v in value:
                        hit[v].append(c)
                else:
                    hit[value].append(c)
            self._indexes[name] = hit = dict(hit)
        return hit

    def order(self, sort: ConversationSort) -> list[ConvFact]:
        """Every conversation by ``sort`` ascending (ties by session id)."""
        hit = self._orders.get(sort)
        if hit is None:
            hit = self._orders[sort] = sorted(self.convs, key=_sort_key(sort, self))
        return hit


@dataclass(slots=True)
class Facts:
    tz: str
    version: str
    loaded_at: datetime
    convs: list[ConvFact]
    sessions: list[SessionFact]
    labels: dict[tuple[str, str], Label]
    users: dict[uuid.UUID, str]
    pending: int
    labelled: int
    _views: dict[tuple[Audience, tuple[str, ...]], AudienceView] = field(
        default_factory=dict
    )
    scopes: dict[tuple[Any, ...], Scope] = field(default_factory=dict)

    def label(self, kind: TaxonomyKind, code: str | None) -> Label | None:
        if code is None:
            return None
        return self.labels.get((kind.value, code))

    def view(self, audience: Audience, test_ips: tuple[str, ...]) -> AudienceView:
        key = (audience, test_ips if audience is not Audience.OVERVIEW else ())
        hit = self._views.get(key)
        if hit is None:
            ips = frozenset(test_ips)
            hit = self._views[key] = AudienceView(
                [c for c in self.convs if in_audience(c.created_ip, audience, ips)]
            )
        return hit


def in_audience(ip: str | None, audience: Audience, test_ips: frozenset[str]) -> bool:
    """``_AUDIENCE_SQL``: testing = no IP or a test IP; users = any other IP."""
    if audience is Audience.TESTING:
        return ip is None or ip in test_ips
    if audience is Audience.USERS:
        return ip is not None and ip not in test_ips
    return True


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

_versions = itertools.count(1)


def _sql_params(tz: str) -> dict[str, Any]:
    return {
        "tz": tz,
        "salt": person_salt(get_settings().session_secret.get_secret_value()),
        "dominance": sql.USER_TYPE_DOMINANCE_PCT,
        "anon_actors": list(ANONYMOUS_ACTORS),
    }


def _parse_conv(idx: int, row: Any, days: dict[str, date]) -> ConvFact:
    exchanges = []
    for day_text, hour, intent, quality, handled, is_sub in json.loads(row.ex):
        day = days.get(day_text)
        if day is None:
            day = days[day_text] = date.fromisoformat(day_text)
        exchanges.append(
            ExFact(day, int(hour), intent, quality, handled is True, is_sub is True)
        )
    subs = [e for e in exchanges if is_substantive(e.intent)]
    ex_days = {e.day for e in exchanges}
    return ConvFact(
        idx=idx,
        session_id=row.session_id,
        start_ts=row.start_ts,
        end_ts=row.end_ts,
        start_day=row.start_ts.date(),
        exchanges=int(row.exchanges),
        substantive=int(row.substantive),
        user_type=row.user_type,
        outcome=row.outcome,
        rating=row.rating,
        issue=row.issue,
        issue_label=row.issue_label,
        failing_question=row.failing_question,
        person_key=row.person_key,
        created_ip=row.created_ip,
        email=row.email,
        search=(row.search_text or "").lower(),
        ranks={
            ConversationSort.CONV_ID: int(row.r_conv_id),
            ConversationSort.USER_TYPE: int(row.r_user_type),
            ConversationSort.OUTCOME: int(row.r_outcome),
            ConversationSort.EXPERIENCE_RATING: int(row.r_rating),
            ConversationSort.ISSUE: int(row.r_issue),
        },
        ex=tuple(exchanges),
        handled_n=sum(1 for e in exchanges if e.handled),
        sub_n=len(subs),
        sub_handled_n=sum(1 for e in subs if e.handled),
        sub_intents=tuple(e.intent for e in subs),
        sub_handled_intents=tuple(e.intent for e in subs if e.handled),
        sub_qualities=tuple(e.quality for e in subs),
        sub_days=tuple(e.day for e in subs),
        sub_hours=tuple(e.hour for e in exchanges if e.is_sub),
        one_day=next(iter(ex_days)) if len(ex_days) == 1 else None,
        intents=frozenset(e.intent for e in exchanges),
        qualities=frozenset(e.quality for e in exchanges),
        hours=frozenset(e.hour for e in exchanges),
    )


def _parse_convs(rows: Sequence[Any]) -> list[ConvFact]:
    days: dict[str, date] = {}
    return [_parse_conv(i, r, days) for i, r in enumerate(rows)]


async def load_facts(tz: str, gather: Gather) -> Facts:
    """Read every fact for ``tz`` (about a second on 16k exchanges)."""
    params = _sql_params(tz)

    async def convs(session: AsyncSession) -> list[ConvFact]:
        rows = (await session.execute(text(_CONV_FACTS_SQL), params)).all()
        # Parsing is CPU work: off the event loop.
        return await asyncio.to_thread(_parse_convs, rows)

    async def sessions(session: AsyncSession) -> list[SessionFact]:
        rows = (await session.execute(text(_SESSION_FACTS_SQL), params)).all()
        return [
            SessionFact(
                r.person_key,
                r.identity,
                r.ip,
                r.email,
                r.created_raw,
                r.last_raw,
                r.created_local,
                r.last_local,
            )
            for r in rows
        ]

    async def small(session: AsyncSession) -> tuple[Any, ...]:
        repo = InsightsAnalyticsRepository(session)
        taxonomy = await repo.taxonomy_rows()
        labels = {(t.kind, t.code): Label(t.label, t.group, t.color) for t in taxonomy}
        users = {r.id: r.email for r in (await session.execute(text(_USERS_SQL))).all()}
        pending = await repo.pending_count()
        labelled = int(await session.scalar(text(_LABELLED_SQL)) or 0)
        return labels, users, pending, labelled

    conv_rows, session_rows, (labels, users, pending, labelled) = await gather(
        convs, sessions, small
    )
    loaded_at = datetime.now(UTC)
    return Facts(
        tz=tz,
        version=f"{next(_versions)}.{int(loaded_at.timestamp() * 1000)}",
        loaded_at=loaded_at,
        convs=conv_rows,
        sessions=session_rows,
        labels=labels,
        users=users,
        pending=pending,
        labelled=labelled,
    )


# ---------------------------------------------------------------------------
# Cache, background reloads and the live stream
# ---------------------------------------------------------------------------

_background: set[asyncio.Task[Any]] = set()
_reloading: dict[str, asyncio.Task[Any]] = {}
_dirty: set[str] = set()
_listening = False


def _facts_key(tz: str) -> str:
    return FACTS_CACHE_PREFIX + tz


def peek_facts(tz: str) -> Facts | None:
    """The facts in memory for ``tz`` (any age), without loading."""
    return dashboard_cache.peek(_facts_key(tz))


async def get_facts(tz: str, gather: Gather) -> Facts:
    """The facts for ``tz``: from memory (a stale copy refreshes in the
    background), else loaded now (single-flight)."""
    cfg = get_settings()
    if cfg.dashboard_cache_fresh_seconds <= 0:
        return await load_facts(tz, gather)
    _listen()
    return await dashboard_cache.get_or_compute(
        _facts_key(tz),
        lambda: load_facts(tz, gather),
        fresh_seconds=max(
            cfg.dashboard_cache_fresh_seconds, cfg.dashboard_snapshot_refresh_seconds
        ),
        stale_seconds=cfg.dashboard_cache_stale_seconds,
    )


async def reload_facts(tz: str, gather: Gather | None = None) -> Facts:
    """Load ``tz`` again now, rebuild the recently requested responses,
    replace the memory copy and announce it (the consoles' refetch is then
    a memory hit)."""
    facts = await load_facts(tz, gather or ScopedRunner().gather)
    await asyncio.to_thread(_rewarm, facts)
    dashboard_cache.put(_facts_key(tz), facts)
    with contextlib.suppress(Exception):
        await publish_event(TOPIC_CONSOLE_CHANGED, FACTS_SCOPE)
    return facts


def schedule_reload(tz: str) -> None:
    """Reload ``tz`` in the background; a change during a reload runs one more."""
    if tz in _reloading:
        _dirty.add(tz)
        return

    async def run() -> None:
        try:
            while True:
                _dirty.discard(tz)
                await reload_facts(tz)
                if tz not in _dirty:
                    return
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("insights_facts_reload_failed", tz=tz, exc_info=True)
        finally:
            _reloading.pop(tz, None)

    task = asyncio.create_task(run())
    _reloading[tz] = task
    _background.add(task)
    task.add_done_callback(_background.discard)


async def settled(tz: str) -> None:
    """Wait for a reload of ``tz`` in progress, if any."""
    task = _reloading.get(tz)
    if task is not None:
        with contextlib.suppress(Exception):
            await asyncio.shield(task)


def loaded_zones() -> list[str]:
    return [
        key.removeprefix(FACTS_CACHE_PREFIX)
        for key in list(dashboard_cache._store)  # noqa: SLF001 - read-only scan
        if key.startswith(FACTS_CACHE_PREFIX)
    ]


def on_console_changed(event: Event) -> None:
    """Event-bus callback: the data changed, reload every zone in memory."""
    if event.id != DATA_SCOPE:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    for tz in loaded_zones():
        schedule_reload(tz)


def _listen() -> None:
    global _listening  # noqa: PLW0603 - one registration per process
    if _listening:
        return
    _listening = True
    get_event_bus().on(TOPIC_CONSOLE_CHANGED, on_console_changed)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def today_in(tz: str) -> date | None:
    """Today in ``tz`` from the process clock, or None without tz data."""
    if tz == "UTC":
        return datetime.now(UTC).date()
    try:
        return datetime.now(ZoneInfo(tz)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class Query:
    """Resolved filters: the in-memory twin of ``QueryContext``."""

    filters: InsightsFilters
    date_from: date | None
    date_to: date | None
    test_ips: tuple[str, ...]
    # Lower-case e-mail of the ``visitor`` user (a user UUID visitor).
    visitor_email: str | None = None

    @property
    def ranged(self) -> bool:
        return self.date_from is not None

    @property
    def narrow(self) -> bool:
        """Exchange-level filters (they also narrow the exchanges counted)."""
        f = self.filters
        return f.hour is not None or bool(f.intent) or bool(f.quality)

    def in_range(self, day: date) -> bool:
        return self.date_to is not None and self.date_from <= day <= self.date_to  # type: ignore[operator]


def build_query(
    facts: Facts, filters: InsightsFilters, today: date, test_ips: Iterable[str]
) -> Query:
    date_from, date_to = resolve_range(filters, today)
    visitor_email = None
    if filters.visitor is not None:
        _ip, user = split_visitor(filters.visitor)
        if user is not None:
            with contextlib.suppress(ValueError):
                visitor_email = facts.users.get(uuid.UUID(user))
    ips = tuple(sorted(test_ips)) if filters.audience is not Audience.OVERVIEW else ()
    return Query(filters, date_from, date_to, ips, visitor_email)


def _turn_matches(e: ExFact, f: InsightsFilters) -> bool:
    if f.hour is not None and e.hour != f.hour:
        return False
    if f.intent and e.intent != f.intent:
        return False
    return not (f.quality and e.quality != f.quality)


def _conv_passes(
    c: ConvFact, q: Query, visitor_ip: str | None, needle: str | None
) -> bool:
    """``passConv()``: the conversation-level filters of ``_select``."""
    f = q.filters
    if q.ranged and not q.in_range(c.start_day):
        return False
    if f.user_type is not None and c.user_type != f.user_type:
        return False
    if f.outcome is not None and c.outcome != f.outcome.value:
        return False
    if f.rating is not None and c.rating != f.rating.value:
        return False
    if f.issue is not None and c.issue != f.issue:
        return False
    if f.person is not None and c.person_key != f.person:
        return False
    if needle is not None and needle not in c.search:
        return False
    if f.visitor is not None:
        if visitor_ip is not None:
            if c.created_ip != visitor_ip:
                return False
        elif q.visitor_email is None or c.email != q.visitor_email:
            return False
    if q.narrow:
        # Kept if any one of its exchanges (any day) matches them all.
        return any(_turn_matches(e, f) for e in c.ex)
    return True


def _candidates(
    view: AudienceView, q: Query, visitor_ip: str | None
) -> tuple[list[ConvFact], bool]:
    """The smallest superset of the scoped conversations an index gives, and
    whether every filter still has to be checked on it."""
    f = q.filters
    if q.ranged:
        if q.date_to is None:
            return [], False
        lo = bisect.bisect_left(view.start_days, q.date_from)
        hi = bisect.bisect_right(view.start_days, q.date_to)
        best = view.convs[lo:hi]
    else:
        best = view.convs
    lookups: list[tuple[str, Any]] = []
    if f.person is not None:
        lookups.append(("person_key", f.person))
    if f.visitor is not None:
        if visitor_ip is not None:
            lookups.append(("created_ip", visitor_ip))
        elif q.visitor_email is None:
            return [], False
        else:
            lookups.append(("email", q.visitor_email))
    for name, value in (
        ("issue", f.issue),
        ("outcome", f.outcome.value if f.outcome else None),
        ("user_type", f.user_type),
        ("rating", f.rating.value if f.rating else None),
        ("intents", f.intent or None),
        ("qualities", f.quality or None),
        ("hours", f.hour),
    ):
        if value is not None:
            lookups.append((name, value))
    for name, value in lookups:
        found = view.index(name).get(value, [])
        if len(found) < len(best):
            best = found
    return best, bool(lookups) or f.q is not None


@dataclass(slots=True)
class Scope:
    """``scoped`` and ``scoped_ex``: the conversations (in start order), those
    whose every exchange is in scope (``whole``) and the in-scope exchanges
    of the others (``partial``)."""

    convs: list[ConvFact]
    whole: list[ConvFact]
    partial: list[ExFact]


def scope(view: AudienceView, q: Query) -> Scope:
    f = q.filters
    visitor_ip = split_visitor(f.visitor)[0] if f.visitor is not None else None
    needle = f.q.lower() if f.q else None
    candidates, check = _candidates(view, q, visitor_ip)
    if check:
        convs = [c for c in candidates if _conv_passes(c, q, visitor_ip, needle)]
    else:
        convs = list(candidates)
    if q.narrow:
        partial = [
            e
            for c in convs
            for e in c.ex
            if (not q.ranged or q.in_range(e.day)) and _turn_matches(e, f)
        ]
        return Scope(convs, [], partial)
    if not q.ranged:
        return Scope(convs, convs, [])
    # A one-day conversation starting in range is in range entirely.
    whole = [c for c in convs if c.one_day is not None]
    partial = [e for c in convs if c.one_day is None for e in c.ex if q.in_range(e.day)]
    return Scope(convs, whole, partial)


# ---------------------------------------------------------------------------
# Widgets (same formulas as ``InsightsAnalyticsService._compute_*``)
# ---------------------------------------------------------------------------

_chain = itertools.chain.from_iterable


def _range(lo: date | None, hi: date | None) -> dict[str, str | None]:
    return {
        "from": lo.isoformat() if lo else None,
        "to": hi.isoformat() if hi else None,
    }


def _sub_partial(s: Scope) -> list[ExFact]:
    return [e for e in s.partial if is_substantive(e.intent)]


def summary(s: Scope, q: Query, facts: Facts) -> dict[str, Any]:
    questions = handled = exchanges = handled_ex = 0
    for c in s.whole:
        questions += c.sub_n
        handled += c.sub_handled_n
        exchanges += c.exchanges
        handled_ex += c.handled_n
    for e in s.partial:
        exchanges += 1
        if e.handled:
            handled_ex += 1
        if is_substantive(e.intent):
            questions += 1
            if e.handled:
                handled += 1
    active = served = sum_sub = 0
    for c in s.convs:
        if c.outcome != GREETING_ONLY:
            active += 1
            sum_sub += c.substantive
            if c.outcome == SERVED:
                served += 1
    return {
        "range": _range(q.date_from, q.date_to),
        "conversations": len(s.convs),
        "exchanges": exchanges,
        "handled_exchanges": handled_ex,
        "substantive_questions": questions,
        "handled_questions": handled,
        "handled_pct": pct(handled, questions),
        "served_conversations": served,
        "active_conversations": active,
        "served_pct": pct(served, active),
        "people": len({c.person_key for c in s.convs}),
        "avg_questions_per_conversation": (
            round(sum_sub / active, PCT_DIGITS) if active else None
        ),
        "pending_classification": facts.pending,
        "source": "live",
    }


def trend(s: Scope, q: Query, view: AudienceView) -> dict[str, Any]:
    if q.filters.period is Period.ALL:
        lo, hi = view.bounds
    else:
        lo, hi = q.date_from, q.date_to
    questions: Counter[date] = Counter()
    for c in s.whole:
        if c.one_day is not None:
            questions[c.one_day] += c.sub_n
        else:
            questions.update(c.sub_days)
    questions.update(e.day for e in _sub_partial(s))
    conversations: Counter[date] = Counter(c.start_day for c in s.convs)
    days = []
    if lo is not None and hi is not None:
        day = lo
        while day <= hi and len(days) < MAX_TREND_DAYS:
            days.append(
                {
                    "day": day.isoformat(),
                    "questions": questions.get(day, 0),
                    "conversations": conversations.get(day, 0),
                }
            )
            day += timedelta(days=1)
    return {"range": _range(lo, hi), "days": days, "source": "live"}


def hourly(s: Scope, q: Query) -> dict[str, Any]:
    counts: Counter[int] = Counter(_chain(c.sub_hours for c in s.whole))
    counts.update(e.hour for e in s.partial if e.is_sub)
    hours = [counts.get(h, 0) for h in range(HOURS_PER_DAY)]
    total = sum(hours)
    return {
        "tz": q.filters.tz,
        "hours": hours,
        "total": total,
        "peak_hour": hours.index(max(hours)) if total else None,
    }


def flow(s: Scope) -> dict[str, Any]:
    pairs: Counter[tuple[str, str]] = Counter(
        (c.user_type, c.outcome) for c in s.convs if c.outcome != GREETING_ONLY
    )
    by_type: Counter[str] = Counter()
    by_outcome: Counter[str] = Counter()
    links = []
    for (user_type, outcome), n in sorted(pairs.items()):
        by_type[user_type] += n
        by_outcome[outcome] += n
        links.append({"user_type": user_type, "outcome": outcome, "conversations": n})
    return {
        "total": sum(by_type.values()),
        "user_types": [
            {"name": name, "conversations": n}
            for name, n in sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "outcomes": [
            {"name": o.value, "conversations": by_outcome[o.value]}
            for o in OUTCOME_ORDER
            if by_outcome[o.value]
        ],
        "links": links,
    }


def topics(s: Scope, facts: Facts) -> dict[str, Any]:
    subs = _sub_partial(s)
    questions: Counter[Any] = Counter(_chain(c.sub_intents for c in s.whole))
    questions.update(e.intent for e in subs)
    handled: Counter[Any] = Counter(_chain(c.sub_handled_intents for c in s.whole))
    handled.update(e.intent for e in subs if e.handled)
    items = []
    for intent, n in sorted(questions.items(), key=lambda kv: (-kv[1], kv[0])):
        entry = facts.label(TaxonomyKind.INTENT, intent)
        items.append(
            {
                "intent": intent,
                "label": entry.label if entry is not None else intent,
                "group": entry.group if entry is not None else None,
                "questions": n,
                "handled": handled[intent],
                "handled_pct": pct(handled[intent], n) or 0.0,
            }
        )
    return {"total": sum(questions.values()), "items": items, "source": "live"}


def quality(s: Scope, facts: Facts) -> dict[str, Any]:
    counts: Counter[Any] = Counter(_chain(c.sub_qualities for c in s.whole))
    counts.update(e.quality for e in _sub_partial(s))
    total = sum(counts.values())
    items = []
    for code in QUALITY_ORDER:
        if not counts[code]:
            continue
        entry = facts.label(TaxonomyKind.BOT_QUALITY, code)
        items.append(
            {
                "quality": code,
                "label": entry.label if entry is not None else code,
                "color": entry.color if entry is not None else None,
                "questions": counts[code],
                "pct": pct(counts[code], total) or 0.0,
            }
        )
    return {"total": total, "items": items, "source": "live"}


def dropoff(s: Scope) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    labels: dict[str, str | None] = {}
    for c in s.convs:
        if c.issue is not None and c.outcome in DROPPED_OUTCOMES:
            counts[c.issue] += 1
            # ``max(issue_label)``: one taxonomy label per code (or NULL).
            if c.issue_label is not None:
                labels[c.issue] = max(labels.get(c.issue) or "", c.issue_label)
            else:
                labels.setdefault(c.issue, None)
    items = sorted(
        (
            {"issue": issue, "label": labels[issue] or issue, "conversations": n}
            for issue, n in counts.items()
        ),
        key=lambda i: (-i["conversations"], i["label"]),
    )
    return {"total": sum(i["conversations"] for i in items), "items": items}


def people(s: Scope, view: AudienceView) -> dict[str, Any]:
    # Conversations are in start order: the first seen is the earliest.
    per: dict[str, dict[str, list[Any]]] = {}
    for c in s.convs:
        outcomes = per.get(c.person_key)
        if outcomes is None:
            per[c.person_key] = {c.outcome: [1, c.start_ts]}
            continue
        hit = outcomes.get(c.outcome)
        if hit is None:
            outcomes[c.outcome] = [1, c.start_ts]
        else:
            hit[0] += 1
    numbers = view.numbers
    items = []
    for key in sorted(per, key=lambda k: numbers[k][0]):
        outcomes = per[key]
        if len(outcomes) == 1:
            ((only, (n, _first)),) = outcomes.items()
            items.append(
                {
                    "person": person_label(numbers[key][0]),
                    "person_id": key,
                    "conversations": n,
                    "dominant_outcome": only,
                    "outcomes": {only: n},
                }
            )
            continue
        items.append(
            {
                "person": person_label(numbers[key][0]),
                "person_id": key,
                "conversations": sum(n for n, _ in outcomes.values()),
                "dominant_outcome": dominant_outcome(
                    {o: (n, first) for o, (n, first) in outcomes.items()}
                ),
                "outcomes": {o: n for o, (n, _) in outcomes.items()},
            }
        )
    return {"total": len(items), "items": items}


def _sort_key(sort: ConversationSort, view: AudienceView) -> Callable[[ConvFact], Any]:
    if sort is ConversationSort.START:
        return _start_key
    if sort is ConversationSort.EXCHANGES:
        return lambda c: (c.exchanges, c.session_id)
    if sort is ConversationSort.PERSON:
        return lambda c: (view.numbers[c.person_key][0], c.session_id)
    return lambda c: (c.ranks[sort], c.session_id)


def conversation_row(
    c: ConvFact, number: int | None, *, with_person: bool = True
) -> dict[str, Any]:
    minutes = (c.end_ts - c.start_ts).total_seconds() / 60
    return {
        "conv_id": str(c.session_id),
        "person": person_label(number) if with_person and number else None,
        "person_id": c.person_key,
        "start": c.start_ts.isoformat(),
        "end": c.end_ts.isoformat(),
        "minutes": round(minutes, 1),
        "user_type": c.user_type,
        "outcome": c.outcome,
        "experience_rating": c.rating,
        "issue": c.issue,
        "issue_label": c.issue_label or c.issue,
        "failing_question": mask(c.failing_question),
        "exchanges": c.exchanges,
        "substantive_exchanges": c.substantive,
    }


# Below this share of the audience the scoped rows are sorted directly;
# above it the audience's presorted order is walked.
_SORT_DIRECT_SHARE = 8


def page_rows(s: Scope, view: AudienceView, page: ConversationPage) -> list[ConvFact]:
    """``ORDER BY <sort> <dir>, session_id <dir> LIMIT .. OFFSET ..``."""
    desc = page.direction is SortDirection.DESC
    end = page.offset + page.limit
    if len(s.convs) * _SORT_DIRECT_SHARE < len(view.convs):
        rows = sorted(s.convs, key=_sort_key(page.sort, view), reverse=desc)
        return rows[page.offset : end]
    order = view.order(page.sort)
    walk = reversed(order) if desc else iter(order)
    if len(s.convs) == len(view.convs):
        return list(itertools.islice(walk, page.offset, end))
    members = {c.idx for c in s.convs}
    return list(
        itertools.islice((c for c in walk if c.idx in members), page.offset, end)
    )


def conversations(
    s: Scope, view: AudienceView, page: ConversationPage
) -> dict[str, Any]:
    window = page_rows(s, view, page)
    return {
        "items": [conversation_row(c, view.numbers[c.person_key][0]) for c in window],
        "total": len(s.convs),
        "limit": page.limit,
        "offset": page.offset,
        "sort": page.sort.value,
        "direction": page.direction.value,
    }


def pending(facts: Facts) -> dict[str, Any]:
    total = facts.pending + facts.labelled
    return {
        "pending": facts.pending,
        "labelled": facts.labelled,
        "total": total,
        "progress_pct": pct(facts.labelled, total),
    }


# Scopes kept per facts version (a page or sort change reuses its scope).
_SCOPES_PER_FACTS = 32


def scoped(facts: Facts, q: Query) -> tuple[AudienceView, Scope]:
    view = facts.view(q.filters.audience, q.test_ips)
    key = (
        q.filters.model_dump_json(),
        q.date_from,
        q.date_to,
        q.test_ips,
        q.visitor_email,
    )
    hit = facts.scopes.get(key)
    if hit is None:
        hit = facts.scopes[key] = scope(view, q)
        while len(facts.scopes) > _SCOPES_PER_FACTS:
            facts.scopes.pop(next(iter(facts.scopes)))
    return view, hit


def bundle(
    facts: Facts,
    q: Query,
    page: ConversationPage,
    parts: Iterable[str] = PARTS,
) -> dict[str, Any]:
    """Every requested widget for ``q`` (``as_of`` = when the facts were read)."""
    wanted = set(parts)
    view, s = scoped(facts, q)
    builders: dict[str, Callable[[], Any]] = {
        "summary": lambda: summary(s, q, facts),
        "trend": lambda: trend(s, q, view),
        "hourly": lambda: hourly(s, q),
        "flow": lambda: flow(s),
        "topics": lambda: topics(s, facts),
        "quality": lambda: quality(s, facts),
        "dropoff": lambda: dropoff(s),
        "people": lambda: people(s, view),
        "conversations": lambda: conversations(s, view, page),
        "pending": lambda: pending(facts),
    }
    out: dict[str, Any] = {"as_of": facts.loaded_at.isoformat()}
    for name in PARTS:
        out[name] = builders[name]() if name in wanted else None
    return out


# ---------------------------------------------------------------------------
# Person and conversation detail
# ---------------------------------------------------------------------------


def person_detail(
    facts: Facts, person_id: str, audience: Audience, test_ips: tuple[str, ...]
) -> dict[str, Any] | None:
    """``InsightsAnalyticsService.person`` from memory."""
    ips = frozenset(test_ips)
    mine = [
        s
        for s in facts.sessions
        if s.person_key == person_id and in_audience(s.ip, audience, ips)
    ]
    if not mine:
        return None
    groups: dict[str | None, list[SessionFact]] = defaultdict(list)
    for s in mine:
        groups[s.ip].append(s)
    ordered = sorted(
        groups.items(), key=lambda kv: max(s.last_raw for s in kv[1]), reverse=True
    )
    # SQL ``max(identity)`` of the most recent IP group (account < network < session).
    identity = max(s.identity for s in ordered[0][1])
    visitors = []
    for ip, rows in ordered:
        vid = visitor_id(ip)
        if vid is None:
            continue
        visitors.append(
            {
                "visitor_id": vid,
                "sessions": len(rows),
                "first_seen": min(
                    rows, key=lambda s: s.created_raw
                ).created_local.isoformat(),
                "last_seen": max(rows, key=lambda s: s.last_raw).last_local.isoformat(),
            }
        )
    account = None
    if identity == PersonIdentity.ACCOUNT.value:
        emails = {s.email for s in mine if s.email}
        account = next(
            (str(uid) for uid, mail in facts.users.items() if mail in emails), None
        )
    number = facts.view(audience, test_ips).numbers.get(person_id)
    return {
        "person": person_label(number[0]) if number else None,
        "person_id": person_id,
        "identity": identity,
        "conversations": number[1] if number else 0,
        "sessions": len(mine),
        "first_seen": min(mine, key=lambda s: s.created_raw).created_local.isoformat(),
        "last_seen": max(mine, key=lambda s: s.last_raw).last_local.isoformat(),
        "visitors": visitors,
        "account_visitor_id": account,
    }


# ---------------------------------------------------------------------------
# Encoded responses
# ---------------------------------------------------------------------------

_bodies: OrderedDict[str, bytes] = OrderedDict()


# A response recipe: facts -> (ETag, body builder).
Recipe = Callable[[Facts], tuple[str, Callable[[], Any]]]
# Recently requested responses, rebuilt for new facts before a reload is
# announced (at most RECIPES_MAX, each for RECIPE_TTL_SECONDS after its last
# request).
RECIPES_MAX = 24
RECIPE_TTL_SECONDS = 600.0
_recipes: OrderedDict[tuple[str, str], tuple[float, Recipe]] = OrderedDict()


def remember(tz: str, key: str, recipe: Recipe) -> None:
    _recipes[(tz, key)] = (time.monotonic(), recipe)
    _recipes.move_to_end((tz, key))
    while len(_recipes) > RECIPES_MAX:
        _recipes.popitem(last=False)


def _rewarm(facts: Facts) -> int:
    """Encode the recent responses of ``facts.tz`` for ``facts`` (a thread)."""
    now = time.monotonic()
    built = 0
    for (tz, _key), (at, recipe) in list(_recipes.items()):
        if tz != facts.tz or now - at > RECIPE_TTL_SECONDS:
            continue
        try:
            etag, build = recipe(facts)
            encoded(etag, build)
            built += 1
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("insights_rewarm_failed", exc_info=True)
    return built


def etag_for(facts: Facts, *parts: Any) -> str:
    raw = json.dumps([facts.tz, facts.version, *parts], default=str, sort_keys=True)
    return f'W/"{hashlib.sha256(raw.encode()).hexdigest()[:32]}"'


def encoded(etag: str, build: Callable[[], Any]) -> bytes:
    """The JSON body of ``etag`` (built once per facts version and params)."""
    body = _bodies.get(etag)
    if body is not None:
        _bodies.move_to_end(etag)
        return body
    body = json.dumps(
        build(), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    _bodies[etag] = body
    while len(_bodies) > BODY_CACHE_SIZE:
        _bodies.popitem(last=False)
    return body


async def encoded_async(etag: str, build: Callable[[], Any]) -> bytes:
    """``encoded`` with the computation off the event loop (tens of ms on a
    large history; other requests keep being served meanwhile)."""
    body = _bodies.get(etag)
    if body is not None:
        return body
    return await asyncio.to_thread(encoded, etag, build)


def clear() -> None:
    """Drop every encoded body and recipe (tests)."""
    _bodies.clear()
    _recipes.clear()
