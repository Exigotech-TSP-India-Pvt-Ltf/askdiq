"""Documents inventory, canonical Markdown, chunk hits and citation stats (B7.3).

Pure tests run anywhere; tests using ``client`` / ``db_session`` need Postgres
(they run in CI).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select, update

from app.api.routes.documents import resolve_range
from app.core.config import get_settings
from app.documents import citations as cit
from app.documents.inventory import (
    ChunkAggregate,
    LastIngest,
    build_summary,
    infer_original_format,
    ingest_event,
)
from app.ingestion.service import backfill_provenance, provenance_extras
from app.models.db_models import (
    AuditLog,
    Chunk,
    CitationSource,
    CitationSyncState,
    Document,
)
from app.schemas.document import DocumentChunkItem
from tests.feedback_helpers import make_exchange, make_feedback, make_session, marker
from tests.test_docx_converter import _create_minimal_docx

FULL_HASH = "a" * 64


def _doc(meta: dict | None, *, name: str = "policy.md") -> Document:
    return Document(
        id=uuid.uuid4(),
        source_name=name,
        source_type="markdown",
        created_at=datetime(2026, 10, 1, 9, 0, 0),
        updated_at=datetime(2026, 10, 2, 9, 0, 0),
        content_hash=FULL_HASH,
        doc_metadata=meta,
    )


# ---------------------------------------------------------------------------
# Pure: inventory mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("meta", "filename", "expected"),
    [
        ({"converted_from": "docx"}, "x.md", "docx"),
        ({"original_format": "markdown"}, "x.md", "md"),
        ({}, "Report.DOCX", "docx"),
        ({}, "notes.markdown", "md"),
        ({}, "unknown", None),
    ],
)
def test_infer_original_format(meta: dict, filename: str, expected: str | None) -> None:
    assert infer_original_format(meta, filename) == expected


def test_build_summary_docx_full_provenance() -> None:
    meta = {
        "original_filename": "Policy.docx",
        "original_format": "docx",
        "original_sha256": "b" * 64,
        "converter_version": "v1",
        "ingest_count": 3,
        "last_ingested_at": "2026-10-03T10:00:00",
        "last_ingested_by": "uid-1",
        "warnings": ["images_dropped=2"],
        "quarantined": False,
        "corpus": "authoritative",
        **provenance_extras(
            converted_from="docx",
            original_size_bytes=2048,
            canonical_markdown="# Title\n\nBody é\n",
            requested_strategy="auto",
            strategy_used="metadata_aware",
            pii_found=["email"],
        ),
    }
    s = build_summary(
        _doc(meta),
        ChunkAggregate(count=4, total_chars=900, strategies=["structure_aware"]),
        canonical_bytes=19,
        actor_names={"uid-1": "owner@example.com"},
    )
    assert s.converted_from == "docx"
    assert s.standardized is True
    assert s.canonical_format == "md"
    assert s.active_format == "MARKDOWN"
    assert s.converter == "mammoth+gfm"
    assert s.original_size_bytes == 2048
    assert s.canonical_size_bytes == 19
    assert s.canonical_available is True
    assert s.content_hash == FULL_HASH[:12]
    assert s.content_sha256 == FULL_HASH
    assert s.chunk_count == 4
    assert s.total_chunk_chars == 900
    assert s.chunking_strategy == "metadata_aware"
    assert s.embedding_model == get_settings().embedding_model
    assert s.embedding_dim == get_settings().embedding_dim
    assert s.ingested_by == "owner@example.com"
    assert s.ingested_at == datetime(2026, 10, 3, 10, 0, tzinfo=UTC)
    assert s.ingest_count == 3
    assert s.pii_redacted == ["email"]
    assert s.status == "active"
    assert s.labels == {"corpus": "authoritative"}
    assert s.created_at.tzinfo is not None


def test_build_summary_legacy_row_uses_audit_fallback_and_nulls() -> None:
    last = LastIngest(
        at=datetime(2026, 9, 1, 8, 0), actor="admin@example.com", events=5
    )
    s = build_summary(
        _doc({"corpus": "x"}, name="about.md"),
        ChunkAggregate(count=2, total_chars=10, strategies=["structure_aware"]),
        canonical_bytes=None,
        last_ingest=last,
    )
    assert s.converted_from == "md"
    assert s.standardized is False
    assert s.original_size_bytes is None
    assert s.canonical_size_bytes is None
    assert s.canonical_available is False
    assert s.embedding_model is None
    assert s.pii_redacted is None
    assert s.chunking_strategy == "structure_aware"
    assert s.ingested_by == "admin@example.com"
    assert s.ingested_at == datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    assert s.ingest_count == 5


@pytest.mark.parametrize(
    ("meta", "chunks", "status"),
    [({"quarantined": True}, 3, "quarantined"), ({}, 0, "empty"), ({}, 1, "active")],
)
def test_build_summary_status(meta: dict, chunks: int, status: str) -> None:
    s = build_summary(_doc(meta), ChunkAggregate(count=chunks), canonical_bytes=None)
    assert s.status == status


def test_ingest_event_mapping_tolerates_legacy_detail() -> None:
    ev = ingest_event(
        datetime(2026, 9, 1), "cli", {"source_document": "a.md", "total_chunks": 18}
    )
    assert ev.total_chunks == 18
    assert ev.status is None
    assert ev.pii_redacted is None
    assert ev.at.tzinfo is not None
    assert ingest_event(datetime(2026, 9, 1), "cli", None).warnings == []


def test_provenance_extras_markdown_is_not_standardized() -> None:
    extras = provenance_extras(
        converted_from="md",
        original_size_bytes=10,
        canonical_markdown="abc\n",
        requested_strategy="auto",
        strategy_used="structure_aware",
        pii_found=[],
    )
    assert extras["standardized"] is False
    assert extras["converter"] == "markdown-normalizer"
    assert extras["canonical_size_bytes"] == 4


def _backfill(meta: dict, converted_from: str) -> dict:
    return backfill_provenance(
        meta,
        converted_from=converted_from,
        original_filename=f"x.{converted_from}",
        original_size_bytes=99,
        canonical_markdown="abc\n",
        pii_found=[],
    )


def test_backfill_provenance_fills_gaps_only() -> None:
    out = _backfill({"corpus": "c", "embedding_model": "kept"}, "docx")
    assert out["corpus"] == "c"
    assert out["embedding_model"] == "kept"
    assert out["original_format"] == "docx"
    assert out["standardized"] is True
    assert out["original_size_bytes"] == 99
    assert out["canonical_size_bytes"] == 4
    assert "chunking_strategy" not in out


def test_backfill_provenance_keeps_stored_origin() -> None:
    out = _backfill({"original_format": "docx", "original_filename": "A.docx"}, "md")
    assert out["original_format"] == "docx"
    assert out["standardized"] is True
    assert out["converter"] == "mammoth+gfm"
    assert out["original_filename"] == "A.docx"
    assert "original_size_bytes" not in out  # the .md upload is not the original
    legacy = _backfill({"original_format": "markdown"}, "md")
    assert legacy["standardized"] is False
    assert legacy["original_size_bytes"] == 99


# ---------------------------------------------------------------------------
# Pure: citations
# ---------------------------------------------------------------------------


def test_week_helpers() -> None:
    thursday = date(2026, 10, 15)
    assert cit.week_start(thursday) == date(2026, 10, 12)
    assert cit.hit_window_start(thursday) == date(2026, 8, 24)
    lo, hi = cit.day_bounds(date(2026, 10, 1), date(2026, 10, 1))
    assert hi - lo == timedelta(days=1)


def test_coverage_rounding_and_null() -> None:
    assert cit.coverage(0, 0).coverage_pct is None
    assert cit.coverage(3, 2).coverage_pct == pytest.approx(66.7)


def test_build_citation_stats_orders_and_includes_uncited() -> None:
    now = datetime(2026, 10, 15, tzinfo=UTC)
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    docs = [
        cit.DocRow(a, "beta.md", datetime(2026, 1, 1), datetime(2026, 10, 5)),
        cit.DocRow(b, "alpha.md", datetime(2026, 10, 1), None),
        cit.DocRow(c, "gamma.md", datetime(2026, 10, 14), None),
    ]
    stats = cit.build_citation_stats(
        start=date(2026, 10, 9),
        end=date(2026, 10, 15),
        documents=docs,
        counts={a: (5, datetime(2026, 10, 14)), b: (5, None), uuid.uuid4(): (9, None)},
        answered=10,
        cited=8,
        limit=10,
        now=now,
    )
    assert [d.source_name for d in stats.top_cited] == ["alpha.md", "beta.md"]
    assert stats.top_cited[1].last_cited_at == datetime(2026, 10, 14, tzinfo=UTC)
    ages = {p.source_name: p for p in stats.age_vs_citations}
    assert set(ages) == {"alpha.md", "beta.md", "gamma.md"}
    assert ages["beta.md"].age_days == 10  # updated_at wins over created_at
    assert ages["gamma.md"].citations == 0
    assert stats.coverage.coverage_pct == 80.0
    dumped = stats.model_dump(by_alias=True, mode="json")
    assert dumped["from"] == "2026-10-09"
    assert dumped["tz"] == "UTC"


def test_sort_chunks() -> None:
    def item(i: int, hits: int) -> DocumentChunkItem:
        return DocumentChunkItem(
            id=str(i),
            chunk_index=i,
            content="x",
            strategy="s",
            char_count=1,
            word_count=1,
            hits=hits,
        )

    items = [item(2, 1), item(0, 5), item(1, 5)]
    assert [c.chunk_index for c in cit.sort_chunks(items, "hits")] == [0, 1, 2]
    assert [c.chunk_index for c in cit.sort_chunks(items, "index")] == [0, 1, 2]
    assert [c.hits for c in cit.sort_chunks(items, "hits")] == [5, 5, 1]


def test_chunk_item_counts_words_and_reads_heading() -> None:
    chunk = Chunk(
        id=uuid.uuid4(),
        content="Section: Leave\n\nTwenty days per year.",
        strategy="metadata_aware",
        chunk_index=0,
        chunk_metadata={"section_heading": "Leave", "base_strategy": "structure_aware"},
    )
    out = cit.chunk_item(chunk, None)
    assert out.word_count == 6
    assert out.section_heading == "Leave"
    assert out.base_strategy == "structure_aware"
    assert out.hits == 0
    assert out.weekly_hits == []


def test_resolve_range_defaults_and_validation() -> None:
    today = date(2026, 10, 15)
    assert resolve_range(None, None, today) == (date(2026, 10, 9), today)
    with pytest.raises(HTTPException) as exc:
        resolve_range(date(2026, 10, 10), date(2026, 10, 1), today)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException):
        resolve_range(date(2020, 1, 1), today, today)


# ---------------------------------------------------------------------------
# DB-backed (CI): upload -> standardize -> chunk -> inventory -> citations
# ---------------------------------------------------------------------------


def _docx(text: str) -> bytes:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Annual leave is twenty days per year.</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    return _create_minimal_docx(xml)


async def _upload_docx(client, name: str, text: str) -> dict:
    res = await client.post(
        "/ingest/file",
        files={
            "file": (
                name,
                _docx(text),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_docx_upload_is_standardized_and_listed(client, fake_embed) -> None:
    mk = marker()
    payload = await _upload_docx(client, f"{mk}.docx", f"Leave policy {mk}")
    assert payload["status"] == "new"
    assert payload["converted_from"] == "docx"
    assert payload["source_name"] == f"{mk}.md"
    assert payload["chunk_count"] >= 1
    assert payload["original_size_bytes"] > 0
    assert payload["canonical_size_bytes"] > 0
    doc_id = payload["document_id"]

    listed = {d["document_id"]: d for d in (await client.get("/documents")).json()}
    row = listed[doc_id]
    assert row["original_filename"] == f"{mk}.docx"
    assert row["converted_from"] == "docx"
    assert row["standardized"] is True
    assert row["canonical_format"] == "md"
    assert row["canonical_available"] is True
    assert row["converter"] == "mammoth+gfm"
    assert row["chunk_count"] == payload["chunk_count"]
    assert row["original_size_bytes"] == payload["original_size_bytes"]
    assert row["canonical_size_bytes"] == payload["canonical_size_bytes"]
    assert len(row["content_hash"]) == 12
    assert row["content_sha256"].startswith(row["content_hash"])
    assert row["embedding_model"] == get_settings().embedding_model
    assert row["ingested_by"]
    assert row["status"] == "active"

    detail = (await client.get(f"/documents/{doc_id}")).json()
    assert detail["chunk_stats"]["count"] == payload["chunk_count"]
    assert detail["history"][0]["status"] == "new"
    assert detail["history"][0]["converted_from"] == "docx"

    canonical = (await client.get(f"/documents/{doc_id}/canonical")).json()
    assert canonical["available"] is True
    assert f"Leave policy {mk}" in canonical["content"]
    assert canonical["size_bytes"] == payload["canonical_size_bytes"]

    chunks = (await client.get(f"/documents/{doc_id}/chunks")).json()
    assert len(chunks) == payload["chunk_count"]
    assert any(mk in c["content"] for c in chunks)
    assert all(c["hits"] == 0 and c["weekly_hits"] == [] for c in chunks)
    found = await client.get(f"/documents/{doc_id}/chunks", params={"q": "TWENTY"})
    assert len(found.json()) >= 1
    none = await client.get(f"/documents/{doc_id}/chunks", params={"q": "100%_x"})
    assert none.json() == []


async def test_unchanged_reupload_backfills_canonical(
    client, db_session, fake_embed
) -> None:
    mk = marker()
    payload = await _upload_docx(client, f"{mk}.docx", f"Backfill {mk}")
    doc_id = uuid.UUID(payload["document_id"])
    # Simulate a row ingested before canonical text and provenance were kept.
    await db_session.execute(
        update(Document)
        .where(Document.id == doc_id)
        .values(canonical_markdown=None, doc_metadata={"corpus": "legacy"})
    )
    await db_session.commit()
    before = (await client.get(f"/documents/{doc_id}/canonical")).json()
    assert before["available"] is False
    assert before["content"] is None
    legacy = (await client.get(f"/documents/{doc_id}")).json()
    assert legacy["original_size_bytes"] is None
    assert legacy["canonical_available"] is False

    again = await _upload_docx(client, f"{mk}.docx", f"Backfill {mk}")
    assert again["status"] == "unchanged"
    after = (await client.get(f"/documents/{doc_id}/canonical")).json()
    assert after["available"] is True
    assert f"Backfill {mk}" in after["content"]
    row = (await client.get(f"/documents/{doc_id}")).json()
    assert row["canonical_available"] is True
    assert row["standardized"] is True
    assert row["converted_from"] == "docx"
    assert row["converter"] == "mammoth+gfm"
    assert row["original_size_bytes"] == payload["original_size_bytes"]
    assert row["canonical_size_bytes"] == after["size_bytes"]
    assert row["labels"] == {"corpus": "legacy"}


async def test_citations_hits_and_low_rated(client, db_session, fake_embed) -> None:
    mk = marker()
    payload = await _upload_docx(client, f"{mk}.docx", f"Cited {mk}")
    doc_id = payload["document_id"]
    chunk = (
        await db_session.execute(
            select(Chunk).where(Chunk.document_id == uuid.UUID(doc_id)).limit(1)
        )
    ).scalar_one()

    session = await make_session(db_session, f"{mk}@example.com")
    # Turn 1: audit row lists the chunk (current format) and is rated 1.
    _u1, asst1 = await make_exchange(
        db_session, session, turn=1, question=f"{mk} first question"
    )
    db_session.add(
        AuditLog(
            request_id=asst1.request_id,
            actor=f"{mk}@example.com",
            action="query",
            detail={
                "sources": [
                    {"chunk_id": str(chunk.id), "document_id": doc_id, "score": 0.8}
                ]
            },
        )
    )
    await make_feedback(
        db_session, asst1, actor="rater@example.com", rating=1, comment="wrong"
    )
    # Turn 2: legacy audit format (chunk ids only) + an evaluation record
    # naming the same chunk: still one citation for this turn.
    _u2, asst2 = await make_exchange(
        db_session,
        session,
        turn=2,
        question=f"{mk} second question",
        sources=[{"chunk_id": str(chunk.id), "source_name": f"{mk}.md"}],
    )
    db_session.add(
        AuditLog(
            request_id=asst2.request_id,
            actor=f"{mk}@example.com",
            action="query",
            detail={"source_ids": [str(chunk.id)]},
        )
    )
    # Turn 3: answered without sources (lowers coverage).
    await make_exchange(db_session, session, turn=3, question=f"{mk} third")
    await db_session.commit()

    stats_res = await client.get("/documents/stats/citations", params={"limit": 50})
    assert stats_res.status_code == 200
    assert stats_res.headers.get("etag")
    stats = stats_res.json()
    top = {d["document_id"]: d for d in stats["top_cited"]}
    assert top[doc_id]["citations"] == 2
    ages = {p["document_id"]: p for p in stats["age_vs_citations"]}
    assert ages[doc_id]["citations"] == 2
    assert ages[doc_id]["age_days"] == 0
    assert stats["coverage"]["answered_queries"] >= 3
    assert stats["coverage"]["cited_queries"] >= 2

    chunks = (
        await client.get(f"/documents/{doc_id}/chunks", params={"sort": "hits"})
    ).json()
    assert chunks[0]["id"] == str(chunk.id)
    assert chunks[0]["hits"] == 2
    assert chunks[0]["low_rated_hits"] == 1
    assert chunks[0]["last_hit_at"] is not None
    assert sum(w["hits"] for w in chunks[0]["weekly_hits"]) == 2

    low = (await client.get(f"/documents/{doc_id}/low-rated-citations")).json()
    assert low["total"] == 1
    item = low["items"][0]
    assert item["message_id"] == str(asst1.id)
    assert item["chunk_id"] == str(chunk.id)
    assert item["rating"] == 1
    assert item["turn_index"] == 1
    assert mk in item["query"]

    # Parity: the all-documents pass (the snapshot) equals the per-document
    # queries.
    doc = uuid.UUID(doc_id)
    today = cit.utc_today()
    stats = await cit.all_document_stats(db_session, today=today, low_rated_limit=100)
    direct = await cit.chunk_hits(db_session, doc, today)
    pre = cit.hits_from_stats(stats)

    def norm(h: cit.ChunkHits) -> tuple:
        weeks = sorted((w.week_start, w.hits) for w in h.weekly)
        return (h.hits, h.low_rated, h.last_hit_at, weeks)

    assert direct
    assert {k: norm(pre[k]) for k in direct} == {k: norm(v) for k, v in direct.items()}
    for limit in (1, 20):
        assert cit.low_rated_from_stats(stats, doc, limit) == (
            await cit.low_rated_citations(db_session, doc, limit=limit, today=today)
        )


async def test_unknown_document_is_404(client) -> None:
    missing = uuid.uuid4()
    for suffix in ("", "/canonical", "/chunks", "/low-rated-citations"):
        res = await client.get(f"/documents/{missing}{suffix}")
        assert res.status_code == 404, suffix


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/documents/stats/citations", {"from": "2026-10-10", "to": "2026-10-01"}),
        ("/documents/stats/citations", {"from": "not-a-day"}),
        ("/documents/stats/citations", {"limit": 0}),
        ("/documents/stats/citations", {"limit": 51}),
        (f"/documents/{uuid.UUID(int=7)}/chunks", {"sort": "bogus"}),
        (f"/documents/{uuid.UUID(int=7)}/chunks", {"q": "x" * 201}),
        (f"/documents/{uuid.UUID(int=7)}/low-rated-citations", {"limit": 101}),
        ("/documents/not-a-uuid/chunks", {}),
    ],
)
async def test_invalid_parameters_are_422(client, path: str, params: dict) -> None:
    res = await client.get(path, params=params)
    assert res.status_code == 422


@pytest.mark.parametrize("via", ["query", "form"])
async def test_dry_run_flag_never_persists(client, db_session, fake_embed, via) -> None:
    """The dashboard sends ``dry_run`` as a query parameter; it must not ingest."""
    mk = marker()
    files = {"file": (f"{mk}.md", f"# Title\n\nBody {mk}\n".encode(), "text/markdown")}
    if via == "query":
        res = await client.post("/ingest/file?dry_run=true", files=files)
    else:
        res = await client.post("/ingest/file", files=files, data={"dry_run": "true"})
    assert res.status_code == 200, res.text
    assert res.json()["dry_run"] is True
    assert res.json()["status"] == "new"
    stored = await db_session.scalar(
        select(Document).where(Document.source_name == f"{mk}.md")
    )
    assert stored is None
    assert fake_embed["calls"] == []


async def _sync_until(db_session, now: datetime) -> None:
    """Run the incremental sync until every source table is expanded to now."""
    for _ in range(200):
        await cit.sync_citation_sources(db_session, now=now)
        states = (await db_session.execute(select(CitationSyncState))).scalars().all()
        if len(states) == 2 and all(s.synced_until >= now for s in states):
            return
    raise AssertionError("citation sources did not catch up")


async def test_citation_sources_keep_stats_identical(
    client, db_session, fake_embed
) -> None:
    """Parity: stats from the expanded ``citation_sources`` rows (+ the live
    tail) equal the direct JSON expansion, before and after an incremental
    sync, and a repeated sync adds nothing."""
    mk = marker()
    payload = await _upload_docx(client, f"{mk}.docx", f"Synced {mk}")
    doc_id = uuid.UUID(payload["document_id"])
    chunk = (
        await db_session.execute(
            select(Chunk).where(Chunk.document_id == doc_id).limit(1)
        )
    ).scalar_one()
    session = await make_session(db_session, f"{mk}@example.com")

    async def cited_turn(turn: int) -> None:
        _u, asst = await make_exchange(
            db_session,
            session,
            turn=turn,
            question=f"{mk} q{turn}",
            sources=[{"chunk_id": str(chunk.id), "source_name": f"{mk}.md"}],
        )
        db_session.add(
            AuditLog(
                request_id=asst.request_id,
                actor=f"{mk}@example.com",
                action="query",
                detail={
                    "sources": [{"chunk_id": str(chunk.id), "document_id": str(doc_id)}]
                },
            )
        )
        await db_session.commit()

    today = datetime.now(UTC).date()
    start = today - timedelta(days=30)

    async def snapshot() -> tuple[dict, dict]:
        stats = await cit.citation_stats(db_session, start=start, end=today, limit=50)
        hits = await cit.chunk_hits(db_session, doc_id, today)
        return stats.model_dump(), {k: (v.hits, v.low_rated) for k, v in hits.items()}

    try:
        await cited_turn(1)
        await cited_turn(2)
        live = await snapshot()
        # Synced past the overlap: these rows are read from citation_sources.
        later = datetime.now(UTC).replace(tzinfo=None) + cit.SYNC_OVERLAP * 2
        await _sync_until(db_session, later)
        stored = (
            (
                await db_session.execute(
                    select(CitationSource).where(
                        CitationSource.chunk_ref == str(chunk.id)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(stored) >= 4  # 2 audit + 2 evaluation sources
        assert await snapshot() == live
        # Idempotent: the overlap is re-read, nothing is duplicated.
        assert await cit.sync_citation_sources(db_session, now=later) == 0
        assert await snapshot() == live
    finally:
        await db_session.execute(delete(CitationSyncState))
        await db_session.execute(delete(CitationSource))
        await db_session.commit()
    # Unsynced again: everything is expanded live; the new turn counts.
    await cited_turn(3)
    after = await snapshot()

    def cites(snap: tuple[dict, dict]) -> int:
        top = {str(d["document_id"]): d for d in snap[0]["top_cited"]}
        return top[str(doc_id)]["citations"]

    assert cites(live) == 2
    assert cites(after) == 3
