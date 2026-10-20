"""Citation analytics for documents and chunks (contract B7.3).

A **citation** is one assistant turn (``request_id``) that listed a source.
Sources are stored in two places, both read here:

- the ``query`` audit row: ``detail.sources[]`` (``chunk_id``, ``document_id``)
  or, for older rows, ``detail.source_ids[]`` (chunk ids);
- ``evaluation_records.retrieved_chunks[]`` (``chunk_id`` and/or the source
  name as ``source_name`` / ``doc``).

A source resolves to a document through its chunk (when the chunk still
exists), its ``document_id``, or its source name. Each turn counts once per
document and once per chunk. The JSON is expanded once into
``citation_sources`` (``sync_citation_sources``, run by the jobs worker); a
read combines those rows with a live expansion of the rows newer than the
sync point, so any date range is an indexed aggregation with the same result
as expanding everything.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import TextClause, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.documents.inventory import as_utc
from app.governance.pii import redact_pii
from app.governance.usage_queries import escape_ilike_pattern
from app.models.db_models import Chunk, CitationSyncState, Document
from app.schemas.document import (
    CitationCoverage,
    CitedDocument,
    DocumentAgePoint,
    DocumentChunkItem,
    DocumentCitationStats,
    LowRatedCitation,
    LowRatedCitations,
    WeeklyHits,
)

LOW_RATING_MAX = 2
HIT_WEEKS = 8
DAYS_PER_WEEK = 7
SECONDS_PER_DAY = 86_400

# The JSON arrays holding a row's cited sources (one element per source).
_AUDIT_ELEMENTS = """json_array_elements(
        CASE
            WHEN json_typeof(a.detail -> 'sources') = 'array'
                 THEN CASE
                     WHEN json_array_length(a.detail -> 'sources') > 0
                          THEN a.detail -> 'sources'
                     WHEN json_typeof(a.detail -> 'source_ids') = 'array'
                          THEN a.detail -> 'source_ids'
                     ELSE CAST('[]' AS json)
                 END
            WHEN json_typeof(a.detail -> 'source_ids') = 'array'
                 THEN a.detail -> 'source_ids'
            ELSE CAST('[]' AS json)
        END
    )"""
_EVAL_ELEMENTS = """json_array_elements(
        CASE
            WHEN json_typeof(r.retrieved_chunks) = 'array' THEN r.retrieved_chunks
            ELSE CAST('[]' AS json)
        END
    )"""
_AUDIT_REFS = """COALESCE(e.value ->> 'chunk_id', e.value #>> '{}') AS chunk_ref,
           e.value ->> 'document_id' AS doc_ref,
           CAST(NULL AS text) AS name_ref"""
_EVAL_REFS = """e.value ->> 'chunk_id' AS chunk_ref,
           CAST(NULL AS text) AS doc_ref,
           COALESCE(e.value ->> 'source_name', e.value ->> 'doc') AS name_ref"""


def _fill(template: str) -> str:
    """Substitute the static SQL fragments into ``template`` by name."""
    return (
        template.replace("{AUDIT_REFS}", _AUDIT_REFS)
        .replace("{AUDIT_ELEMENTS}", _AUDIT_ELEMENTS)
        .replace("{EVAL_REFS}", _EVAL_REFS)
        .replace("{EVAL_ELEMENTS}", _EVAL_ELEMENTS)
    )


# Every cited source in [:start, :end) as (request_id, cited_at, chunk_ref,
# doc_ref, name_ref), then resolved to (chunk_id, chunk_index, document_id).
# Sources older than each table's sync cut (:audit_cut / :eval_cut) come from
# ``citation_sources`` (expanded once, indexed on cited_at); newer ones are
# expanded live from the JSON, so the result is the same as expanding all.
# Static SQL: every value is a bound parameter.
# One static template; the shared fragments above are substituted by name
# (no user input ever reaches the SQL text).
_RAW_CTE_TEMPLATE = """
WITH audit_src AS (
    SELECT a.request_id,
           a.created_at AS cited_at,
           {AUDIT_REFS}
    FROM audit_log a
    CROSS JOIN LATERAL {AUDIT_ELEMENTS} AS e(value)
    WHERE a.action = 'query'
      AND a.created_at >= GREATEST(CAST(:start AS timestamp), CAST(:audit_cut AS timestamp)) AND a.created_at < :end
),
eval_src AS (
    SELECT r.request_id,
           r.created_at AS cited_at,
           {EVAL_REFS}
    FROM evaluation_records r
    CROSS JOIN LATERAL {EVAL_ELEMENTS} AS e(value)
    WHERE r.created_at >= GREATEST(CAST(:start AS timestamp), CAST(:eval_cut AS timestamp)) AND r.created_at < :end
),
stored_src AS (
    SELECT s.request_id, s.cited_at, s.chunk_ref, s.doc_ref, s.name_ref
    FROM citation_sources s
    WHERE s.cited_at >= :start AND s.cited_at < :end
      AND ((s.audit_id IS NOT NULL AND s.cited_at < :audit_cut)
        OR (s.eval_id IS NOT NULL AND s.cited_at < :eval_cut))
),
raw AS (
    SELECT * FROM stored_src
    UNION ALL
    SELECT * FROM audit_src
    UNION ALL
    SELECT * FROM eval_src
),
resolved AS (
    SELECT raw.request_id,
           raw.cited_at,
           c.id AS chunk_id,
           c.chunk_index,
           COALESCE(c.document_id, dd.id, dn.id) AS document_id
    FROM raw
    LEFT JOIN chunks c ON CAST(c.id AS text) = raw.chunk_ref
    LEFT JOIN documents dd ON CAST(dd.id AS text) = raw.doc_ref
    LEFT JOIN documents dn
           ON raw.name_ref IS NOT NULL
          AND lower(dn.source_name) = lower(raw.name_ref)
)
"""
_RAW_CTE = _fill(_RAW_CTE_TEMPLATE)


def _with_sources(body: str) -> TextClause:
    return text(_RAW_CTE + body)


# --- citation_sources: the expanded sources, kept in step incrementally ----------

# Rows this close to the sync point are always read live: a row committed late
# (its created_at set before the sync ran) is never missed.
SYNC_OVERLAP = timedelta(minutes=5)
# Never synced: everything is expanded live.
NEVER_SYNCED = datetime(1970, 1, 1)
# One sync expands at most this much history per table (gentle backfill).
SYNC_MAX_SPAN = timedelta(days=30)
SYNC_MAX_STEPS = 12
ORIGIN_AUDIT = "audit"
ORIGIN_EVAL = "eval"

_SYNC_SQL = {
    ORIGIN_AUDIT: text(
        _fill(
            """
INSERT INTO citation_sources
    (audit_id, ordinal, request_id, cited_at, chunk_ref, doc_ref, name_ref)
SELECT a.id, e.ordinality, a.request_id, a.created_at,
       {AUDIT_REFS}
FROM audit_log a
CROSS JOIN LATERAL {AUDIT_ELEMENTS} WITH ORDINALITY AS e(value, ordinality)
WHERE a.action = 'query' AND a.created_at >= :since AND a.created_at < :until
ON CONFLICT DO NOTHING
"""
        )
    ),
    ORIGIN_EVAL: text(
        _fill(
            """
INSERT INTO citation_sources
    (eval_id, ordinal, request_id, cited_at, chunk_ref, doc_ref, name_ref)
SELECT r.id, e.ordinality, r.request_id, r.created_at,
       {EVAL_REFS}
FROM evaluation_records r
CROSS JOIN LATERAL {EVAL_ELEMENTS} WITH ORDINALITY AS e(value, ordinality)
WHERE r.created_at >= :since AND r.created_at < :until
ON CONFLICT DO NOTHING
"""
        )
    ),
}
_FIRST_ROW_SQL = {
    ORIGIN_AUDIT: text("SELECT min(created_at) FROM audit_log WHERE action = 'query'"),
    ORIGIN_EVAL: text("SELECT min(created_at) FROM evaluation_records"),
}


async def source_cuts(db: AsyncSession) -> dict[str, datetime]:
    """Per table, the time before which ``citation_sources`` is complete."""
    synced = dict(
        (
            await db.execute(
                select(CitationSyncState.origin, CitationSyncState.synced_until)
            )
        ).all()
    )

    def cut(origin: str) -> datetime:
        until = synced.get(origin)
        return until - SYNC_OVERLAP if until is not None else NEVER_SYNCED

    return {"audit_cut": cut(ORIGIN_AUDIT), "eval_cut": cut(ORIGIN_EVAL)}


async def sync_citation_sources(db: AsyncSession, now: datetime | None = None) -> int:
    """Expand the source rows written since the last sync (re-reading the
    overlap; duplicates are ignored), at most ``SYNC_MAX_SPAN`` of history per
    step and ``SYNC_MAX_STEPS`` steps per call (the first backfill spreads
    over a few cycles; reads stay exact meanwhile). Idempotent; returns the
    rows inserted."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    inserted = 0
    for origin, sql in _SYNC_SQL.items():
        for _step in range(SYNC_MAX_STEPS):
            last = await db.scalar(
                select(CitationSyncState.synced_until).where(
                    CitationSyncState.origin == origin
                )
            )
            if last is None:
                since = await db.scalar(_FIRST_ROW_SQL[origin]) or now
            else:
                since = last - SYNC_OVERLAP
            until = min(now, since + SYNC_MAX_SPAN)
            result = await db.execute(sql, {"since": since, "until": until})
            inserted += max(int(getattr(result, "rowcount", 0) or 0), 0)
            stmt = insert(CitationSyncState).values(origin=origin, synced_until=until)
            await db.execute(
                stmt.on_conflict_do_update(
                    index_elements=[CitationSyncState.origin],
                    set_={"synced_until": stmt.excluded.synced_until},
                )
            )
            await db.commit()
            if until >= now:
                break
    return inserted


_DOC_CITATIONS = _with_sources(
    """
SELECT document_id, COUNT(DISTINCT request_id) AS citations, MAX(cited_at)
FROM resolved
WHERE document_id IS NOT NULL
GROUP BY document_id
"""
)

_COVERAGE = _with_sources(
    """
SELECT COUNT(*) AS answered,
       COUNT(*) FILTER (WHERE cited.request_id IS NOT NULL) AS cited
FROM chat_messages m
LEFT JOIN (SELECT DISTINCT request_id FROM raw) cited
       ON cited.request_id = m.request_id
WHERE m.role = 'assistant'
  AND COALESCE(m.outcome, 'answered') = 'answered'
  AND m.created_at >= :start AND m.created_at < :end
"""
)

_CHUNK_HITS = _with_sources(
    """
, hits AS (
    SELECT chunk_id, request_id, cited_at
    FROM resolved
    WHERE document_id = :doc AND chunk_id IS NOT NULL
),
low AS (
    SELECT DISTINCT m.request_id
    FROM chat_messages m
    JOIN message_feedback f ON f.message_id = m.id AND f.rating <= :low_max
    WHERE m.role = 'assistant'
      AND m.request_id IN (SELECT request_id FROM hits)
)
SELECT h.chunk_id,
       COUNT(DISTINCT h.request_id) FILTER (WHERE h.cited_at >= :window_start),
       COUNT(DISTINCT h.request_id)
           FILTER (WHERE h.cited_at >= :window_start AND l.request_id IS NOT NULL),
       MAX(h.cited_at)
FROM hits h
LEFT JOIN low l ON l.request_id = h.request_id
GROUP BY h.chunk_id
"""
)

_CHUNK_WEEKS = _with_sources(
    """
SELECT chunk_id,
       CAST(date_trunc('week', cited_at) AS date) AS week_start,
       COUNT(DISTINCT request_id)
FROM resolved
WHERE document_id = :doc AND chunk_id IS NOT NULL AND cited_at >= :window_start
GROUP BY chunk_id, week_start
"""
)

_LOW_RATED = _with_sources(
    """
, rows AS (
    SELECT DISTINCT ON (m.id, r.chunk_id)
           m.id AS message_id,
           m.session_id,
           m.turn_index,
           m.request_id,
           r.chunk_id,
           r.chunk_index,
           f.rating,
           f.reason_tags,
           f.comment,
           f.created_at,
           u.content AS query
    FROM resolved r
    JOIN chat_messages m ON m.request_id = r.request_id AND m.role = 'assistant'
    JOIN message_feedback f ON f.message_id = m.id AND f.rating <= :low_max
    LEFT JOIN chat_messages u
           ON u.session_id = m.session_id
          AND u.turn_index = m.turn_index
          AND u.role = 'user'
    WHERE r.document_id = :doc
    ORDER BY m.id, r.chunk_id, f.created_at DESC
)
SELECT message_id, session_id, turn_index, request_id, chunk_id, chunk_index,
       rating, reason_tags, comment, created_at, query, COUNT(*) OVER () AS total
FROM rows
ORDER BY created_at DESC, message_id
LIMIT :limit
"""
)


# --- date helpers --------------------------------------------------------------


def utc_today() -> date:
    return datetime.now(UTC).date()


def day_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Naive-UTC ``[start 00:00, end+1 00:00)`` for the stored naive timestamps."""
    return (
        datetime.combine(start, time.min),
        datetime.combine(end + timedelta(days=1), time.min),
    )


def week_start(day: date) -> date:
    """Monday of ``day``'s ISO week."""
    return day - timedelta(days=day.weekday())


def hit_window_start(today: date) -> date:
    """First Monday of the last ``HIT_WEEKS`` ISO weeks (current week included)."""
    return week_start(today) - timedelta(weeks=HIT_WEEKS - 1)


def lookback_start(today: date) -> date:
    return today - timedelta(days=get_settings().usage_max_range_days)


def age_days(reference: datetime | None, now: datetime) -> int:
    if reference is None:
        return 0
    ref = as_utc(reference) or now
    return max(0, int((now - ref).total_seconds() // SECONDS_PER_DAY))


def coverage(answered: int, cited: int) -> CitationCoverage:
    pct = round(100 * cited / answered, 1) if answered else None
    return CitationCoverage(
        answered_queries=answered, cited_queries=cited, coverage_pct=pct
    )


# --- document stats -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DocRow:
    document_id: uuid.UUID
    source_name: str
    created_at: datetime | None
    updated_at: datetime | None


def build_citation_stats(
    *,
    start: date,
    end: date,
    documents: list[DocRow],
    counts: dict[uuid.UUID, tuple[int, datetime | None]],
    answered: int,
    cited: int,
    limit: int,
    now: datetime,
) -> DocumentCitationStats:
    """Pure assembly of ``GET /documents/stats/citations``."""
    by_id = {d.document_id: d for d in documents}
    top = [
        CitedDocument(
            document_id=str(doc_id),
            source_name=by_id[doc_id].source_name,
            citations=n,
            last_cited_at=as_utc(last),
        )
        for doc_id, (n, last) in counts.items()
        if doc_id in by_id and n > 0
    ]
    top.sort(key=lambda d: (-d.citations, d.source_name.lower()))
    ages = [
        DocumentAgePoint(
            document_id=str(d.document_id),
            source_name=d.source_name,
            age_days=age_days(d.updated_at or d.created_at, now),
            citations=counts.get(d.document_id, (0, None))[0],
        )
        for d in documents
    ]
    ages.sort(key=lambda p: (-p.citations, p.source_name.lower()))
    return DocumentCitationStats(
        from_=start,
        to=end,
        top_cited=top[:limit],
        age_vs_citations=ages,
        coverage=coverage(answered, cited),
    )


async def citation_stats(
    db: AsyncSession, *, start: date, end: date, limit: int
) -> DocumentCitationStats:
    lo, hi = day_bounds(start, end)
    params = {"start": lo, "end": hi, **await source_cuts(db)}
    docs = [
        DocRow(*row)
        for row in (
            await db.execute(
                select(
                    Document.id,
                    Document.source_name,
                    Document.created_at,
                    Document.updated_at,
                )
            )
        ).all()
    ]
    counts = {
        doc_id: (int(n), last)
        for doc_id, n, last in (await db.execute(_DOC_CITATIONS, params)).all()
    }
    answered, cited = (await db.execute(_COVERAGE, params)).one()
    return build_citation_stats(
        start=start,
        end=end,
        documents=docs,
        counts=counts,
        answered=int(answered or 0),
        cited=int(cited or 0),
        limit=limit,
        now=datetime.now(UTC),
    )


# --- chunks ----------------------------------------------------------------------


@dataclass(slots=True)
class ChunkHits:
    hits: int = 0
    low_rated: int = 0
    last_hit_at: datetime | None = None
    weekly: list[WeeklyHits] = field(default_factory=list)


def chunk_item(chunk: Chunk, stats: ChunkHits | None) -> DocumentChunkItem:
    """Pure mapping of a chunk row (+ hit stats) to its API item."""
    meta: dict[str, Any] = (
        chunk.chunk_metadata if isinstance(chunk.chunk_metadata, dict) else {}
    )
    s = stats or ChunkHits()
    heading = meta.get("section_heading")
    return DocumentChunkItem(
        id=str(chunk.id),
        chunk_index=chunk.chunk_index,
        content=chunk.content,
        strategy=chunk.strategy,
        base_strategy=meta.get("base_strategy"),
        section_heading=heading or None,
        char_count=len(chunk.content),
        word_count=len(chunk.content.split()),
        content_hash=chunk.content_hash,
        hits=s.hits,
        low_rated_hits=s.low_rated,
        last_hit_at=as_utc(s.last_hit_at),
        weekly_hits=sorted(s.weekly, key=lambda w: w.week_start),
    )


def sort_chunks(items: list[DocumentChunkItem], sort: str) -> list[DocumentChunkItem]:
    if sort == "hits":
        return sorted(items, key=lambda c: (-c.hits, c.chunk_index))
    return sorted(items, key=lambda c: c.chunk_index)


async def chunk_hits(
    db: AsyncSession, document_id: uuid.UUID, today: date
) -> dict[uuid.UUID, ChunkHits]:
    lo, hi = day_bounds(lookback_start(today), today)
    window_lo, _ = day_bounds(hit_window_start(today), today)
    params = {
        "start": lo,
        "end": hi,
        "doc": document_id,
        "window_start": window_lo,
        "low_max": LOW_RATING_MAX,
        **await source_cuts(db),
    }
    out: dict[uuid.UUID, ChunkHits] = defaultdict(ChunkHits)
    for chunk_id, hits, low, last in (await db.execute(_CHUNK_HITS, params)).all():
        entry = out[chunk_id]
        entry.hits, entry.low_rated, entry.last_hit_at = int(hits), int(low), last
    for chunk_id, week, n in (await db.execute(_CHUNK_WEEKS, params)).all():
        out[chunk_id].weekly.append(WeeklyHits(week_start=week, hits=int(n)))
    return dict(out)


async def list_chunks(
    db: AsyncSession,
    document_id: uuid.UUID,
    *,
    q: str | None,
    sort: str,
    today: date,
) -> list[DocumentChunkItem]:
    stmt = select(Chunk).where(Chunk.document_id == document_id)
    if q:
        pattern = f"%{escape_ilike_pattern(q)}%"
        stmt = stmt.where(
            or_(
                Chunk.content.ilike(pattern, escape="\\"),
                Chunk.chunk_metadata["section_heading"]
                .as_string()
                .ilike(pattern, escape="\\"),
            )
        )
    chunks = (await db.execute(stmt.order_by(Chunk.chunk_index))).scalars().all()
    stats = await chunk_hits(db, document_id, today) if chunks else {}
    return sort_chunks([chunk_item(c, stats.get(c.id)) for c in chunks], sort)


# --- low-rated citations ---------------------------------------------------------


def _redacted(value: str | None) -> str | None:
    return redact_pii(value)[0] if value else value


async def low_rated_citations(
    db: AsyncSession, document_id: uuid.UUID, *, limit: int, today: date
) -> LowRatedCitations:
    lo, hi = day_bounds(lookback_start(today), today)
    rows = (
        await db.execute(
            _LOW_RATED,
            {
                "start": lo,
                "end": hi,
                "doc": document_id,
                "low_max": LOW_RATING_MAX,
                "limit": limit,
                **await source_cuts(db),
            },
        )
    ).all()
    items = [
        LowRatedCitation(
            message_id=str(r.message_id),
            session_id=str(r.session_id),
            turn_index=r.turn_index,
            request_id=r.request_id,
            chunk_id=str(r.chunk_id) if r.chunk_id else None,
            chunk_index=r.chunk_index,
            query=_redacted(r.query),
            rating=r.rating,
            reason_tags=[str(t) for t in (r.reason_tags or [])],
            comment=r.comment,
            created_at=as_utc(r.created_at) or r.created_at,
        )
        for r in rows
    ]
    total = int(rows[0].total) if rows else 0
    return LowRatedCitations(document_id=str(document_id), total=total, items=items)
