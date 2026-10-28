"""Thin wrapper around pgvector similarity search.

Retrieval pipeline (in order):

1. Broad cosine-distance candidate recall (~5x the requested top_k).
2. Dedupe candidates by chunk id (STEP 2 — not by section heading, since
   headings legitimately repeat across/within documents, e.g. "Key
   Features" or "CTA").
3. Cross-encoder rerank of the candidate pool (STEP 5, see reranker.py).
4. Cross-document quota selection (STEP 3): guarantee representation from
   the top-scoring documents instead of letting one strong document
   consume the whole result budget.
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from typing import NamedTuple

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.config import get_settings
from app.models.db_models import Chunk, Document
from app.retrieval.reranker import (
    INTENT_ENTITY_WEIGHT,
    INTENT_HEADING_SHARE,
    SPREAD_MAX_CHUNKS,
    HybridSignal,
    compute_hybrid_signal,
    entities_missing_from_pool,
    entity_sql_pattern,
    named_entities,
    pick_entity_documents,
    query_intent,
    rerank,
    trim_pool,
)

settings = get_settings()
logger = logging.getLogger(__name__)

_SCALAR = (str, int, float, bool)
_MAX_ENTITY_FETCH_DOCUMENTS = 2
# "What does X cover": headed chunks get a small edge over unheaded ones.
_SPREAD_HEADING_BONUS = 0.05


def usable_filters(filters: dict | None) -> dict[str, str]:
    """Keep only allow-listed metadata keys with scalar values.

    Anything else (e.g. Swagger's placeholder ``{"additionalProp1": {}}``) would
    match no chunk at all and silently empty the answer, so it is dropped.
    """
    if not filters:
        return {}
    allowed = set(settings.retrieval_filter_keys)
    kept = {
        key: str(value)
        for key, value in filters.items()
        if key in allowed and isinstance(value, _SCALAR) and str(value).strip()
    }
    dropped = sorted(set(filters) - set(kept))
    if dropped:
        logger.info("retrieval_filters_ignored keys=%s", dropped[:10])
    return kept


def quarantined_document_ids():
    """Ids of documents the ingest scan quarantined (prompt-injection hits).

    Their chunks stay stored so an admin can inspect them, but retrieval must
    never cite them.
    """
    return select(Document.id).where(
        Document.doc_metadata["quarantined"].as_string() == "true"
    )


def _heading(chunk: Chunk) -> str:
    return str((chunk.chunk_metadata or {}).get("section_heading") or "")


class DocumentLink(NamedTuple):
    """A candidate's document link fields (read in the same query, OR-7)."""

    source_url: str | None
    section_links: bool
    title: str | None
    source_name: str | None = None


class VectorStore:
    def __init__(self, session: AsyncSession):
        self.session = session
        # Filled by similarity_search: document id -> its link fields, so
        # citations cost no extra query.
        self.document_links: dict[uuid.UUID, DocumentLink] = {}
        # Filled by similarity_search: chunk id -> hybrid (lexical/entity)
        # rank boost, so callers can order by cosine + boost. Reported scores
        # stay the raw cosine.
        self.rank_boosts: dict[uuid.UUID, float] = {}

    async def fetch_embeddings(self, chunk_ids: list) -> dict:
        """Embeddings for just these chunks (candidates are loaded without them)."""
        if not chunk_ids:
            return {}
        rows = (
            await self.session.execute(
                select(Chunk.id, Chunk.embedding).where(Chunk.id.in_(chunk_ids))
            )
        ).all()
        await self.session.commit()
        return {chunk_id: list(embedding) for chunk_id, embedding in rows}

    async def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        filters: dict | None = None,
        query_text: str | None = None,
        max_documents: int | None = None,
    ) -> list[tuple[Chunk, float]]:
        """Return the top_k most relevant chunks for a query embedding.

        `max_documents` overrides the cross-document quota (wider spread when
        a short question may need clarifying, see app.harness.clarify).

        `query_text` (the raw query string) is optional but required for the
        cross-encoder reranking stage; without it the cosine-ranked order is
        used as-is.
        """

        # ---------------------------------------------------------
        # STEP 1: Broad semantic candidate recall
        # ---------------------------------------------------------

        candidate_k = max(
            top_k * settings.retrieval_candidate_multiplier,
            settings.retrieval_candidate_min,
        )

        # The hybrid signal may promote a document cosine ranked just outside
        # the pool: fetch a few extra rows (cheap), then trim back to
        # candidate_k before the (expensive) rerank.
        hybrid = bool(query_text) and settings.retrieval_hybrid_enabled
        fetch_k = candidate_k + (settings.retrieval_lexical_pool_extra if hybrid else 0)

        stmt = (
            select(
                Chunk,
                Chunk.embedding.cosine_distance(query_embedding).label("distance"),
                Document.source_url,
                Document.section_links,
                Document.doc_metadata["title"].as_string(),
                Document.source_name,
            )
            .join(Document, Document.id == Chunk.document_id)
            .options(defer(Chunk.embedding))
        )
        stmt = stmt.where(Chunk.document_id.not_in(quarantined_document_ids()))

        for key, value in usable_filters(filters).items():
            stmt = stmt.where(Chunk.chunk_metadata[key].as_string() == value)

        stmt = stmt.order_by("distance").limit(fetch_k)

        rows = (await self.session.execute(stmt)).all()

        # End the read transaction now so the pooled connection is returned
        # before the slow rerank/LLM stages (expire_on_commit=False keeps rows usable).
        await self.session.commit()

        if not rows:
            return []

        # ---------------------------------------------------------
        # STEP 2: Dedupe by chunk id, keeping the best cosine score
        # ---------------------------------------------------------

        unique_candidates: dict = {}

        for chunk, distance, source_url, section_links, title, source_name in rows:
            self.document_links[chunk.document_id] = DocumentLink(
                source_url, bool(section_links), title, source_name
            )
            score = 1 - distance
            existing = unique_candidates.get(chunk.id)

            if existing is None or score > existing[1]:
                unique_candidates[chunk.id] = (chunk, score)

        candidates = sorted(
            unique_candidates.values(),
            key=lambda item: item[1],
            reverse=True,
        )

        # ---------------------------------------------------------
        # STEP 2b: Named-entity document fetch. Reordering cannot rescue a
        # document whose chunks are not in the cosine pool, so when the query
        # names a framework/service whose document is under-represented, one
        # extra document-constrained query (same embedding) adds its chunks.
        # ---------------------------------------------------------
        if hybrid and query_text and settings.retrieval_entity_fetch_enabled:
            extra = await self._fetch_entity_chunks(
                query_embedding, query_text, candidates, filters
            )
            if extra:
                by_id = {chunk.id: (chunk, score) for chunk, score in candidates}
                for chunk, score in extra:
                    current = by_id.get(chunk.id)
                    if current is None or score > current[1]:
                        by_id[chunk.id] = (chunk, score)
                candidates = sorted(
                    by_id.values(), key=lambda item: item[1], reverse=True
                )

        # ---------------------------------------------------------
        # STEP 5: Rerank the candidate pool before final selection
        # ---------------------------------------------------------

        signal = HybridSignal()
        if hybrid and query_text:
            # String ops only (no embedding call): lexical + named-entity
            # boost, then trim the pool back to its normal rerank size.
            signal = compute_hybrid_signal(
                query_text, candidates, self._document_names()
            )
            self.rank_boosts.update(signal.boosts)
            candidates = trim_pool(candidates, signal, candidate_k)

        if query_text:
            boosts = [signal.boosts.get(chunk.id, 0.0) for chunk, _ in candidates]
            # CPU-bound ONNX inference — off the event loop so it doesn't
            # stall every other concurrent request behind it.
            candidates = await asyncio.to_thread(rerank, query_text, candidates, boosts)

        # ---------------------------------------------------------
        # STEP 3: Cross-document quota selection
        # ---------------------------------------------------------

        return self._select_diverse_chunks(
            candidates=candidates,
            top_k=top_k,
            max_documents=max_documents,
            signal=signal,
        )

    def _document_names(self) -> dict[uuid.UUID, str]:
        return {
            doc_id: f"{link.source_name or ''} {link.title or ''}"
            for doc_id, link in self.document_links.items()
        }

    async def _fetch_entity_chunks(
        self,
        query_embedding: list[float],
        query_text: str,
        pool: list[tuple[Chunk, float]],
        filters: dict | None,
    ) -> list[tuple[Chunk, float]]:
        """Top chunks of the documents a named entity maps to, if missing.

        No work (and no DB round trip) unless the query names a catalogue
        entity with fewer than `retrieval_entity_min_chunks` chunks in `pool`
        or (for a question whose words map to heading vocabulary, e.g.
        "stages" -> "Delivery Approach") none under a matching heading. One
        query, restricted to those documents by name and to chunks not already
        in the pool, reusing the already-computed embedding and ranked by
        cosine plus the expanded-vocabulary match; at most two documents.
        """
        entities = named_entities(query_text)
        if not entities:
            return []
        intent = query_intent(query_text)
        missing = entities_missing_from_pool(
            entities,
            pool,
            self._document_names(),
            settings.retrieval_entity_min_chunks,
            intent,
        )
        if not missing:
            return []

        fetch_k = settings.retrieval_entity_fetch_k
        title = Document.doc_metadata["title"].as_string()
        distance = Chunk.embedding.cosine_distance(query_embedding)
        heading = Chunk.chunk_metadata["section_heading"].as_string()
        rank_distance = distance
        if intent:
            pattern = intent.pattern(postgres=True)
            head_hit = case(
                (func.coalesce(heading, "").op("~*")(pattern), 1.0), else_=0.0
            )
            body_hit = case((Chunk.content.op("~*")(pattern), 1.0), else_=0.0)
            rank_distance = distance - INTENT_ENTITY_WEIGHT * (
                INTENT_HEADING_SHARE * head_hit + (1 - INTENT_HEADING_SHARE) * body_hit
            )
        # Breadth first: each document's best chunk, then its second, ... (or,
        # for "what does X cover", each section heading's best chunk first).
        partition = [Chunk.document_id]
        if intent.spread:
            partition.append(func.coalesce(heading, ""))
        rank_in_doc = func.row_number().over(
            partition_by=partition, order_by=rank_distance
        )
        stmt = (
            select(
                Chunk,
                distance.label("distance"),
                Document.source_url,
                Document.section_links,
                title,
                Document.source_name,
            )
            .join(Document, Document.id == Chunk.document_id)
            .options(defer(Chunk.embedding))
            .where(Chunk.document_id.not_in(quarantined_document_ids()))
            .where(Chunk.id.not_in([chunk.id for chunk, _score in pool]))
            .where(
                func.concat_ws(" ", Document.source_name, title).op("~*")(
                    entity_sql_pattern(missing)
                )
            )
        )
        for key, value in usable_filters(filters).items():
            stmt = stmt.where(Chunk.chunk_metadata[key].as_string() == value)
        stmt = stmt.order_by(rank_in_doc, rank_distance).limit(fetch_k)

        rows = (await self.session.execute(stmt)).all()
        await self.session.commit()

        names = {
            chunk.document_id: f"{source_name or ''} {doc_title or ''}"
            for chunk, _d, _u, _s, doc_title, source_name in rows
        }
        best: dict[str, float] = {}
        for chunk, dist, *_rest in rows:
            doc_id = str(chunk.document_id)
            best[doc_id] = max(best.get(doc_id, float("-inf")), 1 - dist)
        keep = set(
            pick_entity_documents(missing, names, best, _MAX_ENTITY_FETCH_DOCUMENTS)
        )

        fetched: list[tuple[Chunk, float]] = []
        for chunk, dist, source_url, section_links, doc_title, source_name in rows:
            if str(chunk.document_id) not in keep:
                continue
            self.document_links[chunk.document_id] = DocumentLink(
                source_url, bool(section_links), doc_title, source_name
            )
            fetched.append((chunk, 1 - dist))
        logger.info(
            "retrieval_entity_fetch entities=%s chunks=%d", missing, len(fetched)
        )
        return fetched

    @staticmethod
    def _select_diverse_chunks(
        candidates: list[tuple[Chunk, float]],
        top_k: int,
        max_documents: int | None = None,
        signal: HybridSignal | None = None,
    ) -> list[tuple[Chunk, float]]:
        """Allocate the result budget across the top-scoring documents.

        Guarantees cross-document coverage: if two or more documents score
        strongly, each gets a fair quota of slots via round-robin, so a
        second genuinely relevant document is never starved by the single
        top-scoring one (the bug this replaces).

        With a hybrid `signal`, documents named by the query are never dropped
        by the document cap and keep at least `retrieval_entity_min_chunks`
        chunks (those above the retrieval floor); the final order uses
        cosine + hybrid boost.
        """

        if not candidates:
            return []

        by_doc: dict[str, list[tuple[Chunk, float]]] = defaultdict(list)
        doc_order: list[str] = []

        for chunk, score in candidates:
            doc_id = str(chunk.document_id)
            if doc_id not in by_doc:
                doc_order.append(doc_id)
            by_doc[doc_id].append((chunk, score))

        signal = signal or HybridSignal()

        # Named documents choose their chunks by cosine + hybrid/intent boost
        # (so a "Delivery Approach" chunk beats "What Is X" for a "stages"
        # question); for "what does X cover" headed chunks get a small edge.
        spread_bonus = _SPREAD_HEADING_BONUS if signal.spread else 0.0
        for doc_id in signal.entity_doc_ids:
            if doc_id in by_doc:
                by_doc[doc_id].sort(
                    key=lambda item: (
                        signal.rank_key(item[0].id, item[1])
                        + (spread_bonus if _heading(item[0]) else 0.0)
                    ),
                    reverse=True,
                )

        # Documents ranked by their single best-scoring chunk.
        ranked_docs = sorted(
            doc_order,
            key=lambda doc_id: signal.rank_key(
                by_doc[doc_id][0][0].id, by_doc[doc_id][0][1]
            ),
            reverse=True,
        )

        doc_cap = max_documents or settings.cross_doc_quota_max_documents
        # Documents the query names come first and are never cut by the cap
        # in favour of unnamed ones (they only compete with each other).
        priority = [doc for doc in signal.entity_doc_ids if doc in by_doc][:doc_cap]
        others = [doc for doc in ranked_docs if doc not in priority]
        quota_docs = (priority + others)[: min(doc_cap, len(ranked_docs))]
        per_doc_quota = max(1, top_k // len(quota_docs))

        selected: list[tuple[Chunk, float]] = []
        selected_ids: set = set()
        doc_taken: defaultdict[str, int] = defaultdict(int)
        doc_cursor: defaultdict[str, int] = defaultdict(int)

        headings_taken: defaultdict[str, set[str]] = defaultdict(set)

        def _take_next(
            doc_id: str, *, above_floor: bool, distinct_heading: bool = False
        ) -> bool:
            doc_chunks = by_doc[doc_id]
            while doc_cursor[doc_id] < len(doc_chunks):
                chunk, score = doc_chunks[doc_cursor[doc_id]]
                doc_cursor[doc_id] += 1

                if chunk.id in selected_ids:
                    continue

                if above_floor and score < settings.min_retrieval_score:
                    continue

                heading = _heading(chunk)
                if distinct_heading and heading in headings_taken[doc_id]:
                    continue

                selected.append((chunk, score))
                selected_ids.add(chunk.id)
                headings_taken[doc_id].add(heading)
                doc_taken[doc_id] += 1
                return True
            return False

        # Named documents are seeded first, breadth before depth, so each
        # keeps `retrieval_entity_min_chunks` chunks (beyond the first they
        # must clear the retrieval floor). One slot stays free for others.
        # "What does X cover": up to SPREAD_MAX_CHUNKS chunks, one per heading.
        seed_budget = max(1, top_k - 1)
        levels = settings.retrieval_entity_min_chunks
        if signal.spread:
            levels = max(levels, SPREAD_MAX_CHUNKS)
        for level in range(levels):
            for doc_id in priority:
                if len(selected) >= seed_budget:
                    break
                _take_next(
                    doc_id, above_floor=level > 0, distinct_heading=signal.spread
                )

        progressed = True
        while len(selected) < top_k and progressed:
            progressed = False

            for doc_id in quota_docs:
                if len(selected) >= top_k:
                    break

                if doc_taken[doc_id] >= per_doc_quota:
                    continue

                if _take_next(doc_id, above_floor=False):
                    progressed = True

        # Backfill any remaining slots with the next best-scoring chunks
        # overall, regardless of document, once quotas are satisfied.
        for chunk, score in candidates:
            if len(selected) >= top_k:
                break

            if chunk.id in selected_ids:
                continue

            selected.append((chunk, score))
            selected_ids.add(chunk.id)

        selected.sort(
            key=lambda item: signal.rank_key(item[0].id, item[1]), reverse=True
        )

        return selected
