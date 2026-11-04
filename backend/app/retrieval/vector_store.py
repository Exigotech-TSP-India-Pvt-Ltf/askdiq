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
from app.knowledge.links import document_url
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
    run_in_rerank_slot,
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


# A routed document (website page) cites SITE_BASE_URL + its route.
_ROUTE = Document.doc_metadata["route"].as_string()


def _document_link(
    source_url: str | None,
    section_links: bool | None,
    title: str | None,
    source_name: str | None,
    route: str | None,
) -> DocumentLink:
    url = document_url(source_url, route, settings.site_base_url)
    return DocumentLink(url, bool(section_links), title, source_name)


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
        # Filled by each similarity_search call (reset per call): chunk id ->
        # raw cross-encoder logit against that call's query_text. The
        # relevance gate reads it right after the call; empty when the
        # reranker is disabled or failed (the gate then falls back to cosine).
        self.last_ce_scores: dict[uuid.UUID, float] = {}

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

    async def similarity_search(  # pylint: disable=too-many-locals
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

        self.last_ce_scores = {}
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
                _ROUTE,
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

        for (
            chunk,
            distance,
            source_url,
            section_links,
            title,
            source_name,
            route,
        ) in rows:
            self.document_links[chunk.document_id] = _document_link(
                source_url, section_links, title, source_name, route
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

        # Hybrid signal, rerank and selection are CPU-bound: one worker
        # thread (inside a rerank slot when the ONNX model runs) so they
        # never stall other requests on the event loop.
        job = _RankJob(
            query_text=query_text,
            candidates=candidates,
            names=self._document_names() if hybrid and query_text else {},
            hybrid=hybrid,
            candidate_k=candidate_k,
            top_k=top_k,
            max_documents=max_documents,
        )
        if query_text and settings.enable_reranker:
            selected, signal, logits = await run_in_rerank_slot(_rank_and_select, job)
        else:
            selected, signal, logits = await asyncio.to_thread(_rank_and_select, job)
        self.rank_boosts.update(signal.boosts)
        if query_text:
            self.last_ce_scores = logits
        return selected

    def _document_names(self) -> dict[uuid.UUID, str]:
        return {
            doc_id: f"{link.source_name or ''} {link.title or ''}"
            for doc_id, link in self.document_links.items()
        }

    async def best_chunk_per_entity_document(  # pylint: disable=too-many-locals
        self,
        query_embedding: list[float],
        entities: list[str],
        filters: dict | None = None,
    ) -> list[tuple[Chunk, float]]:
        """The best chunk (by cosine to the query) of every document the given
        catalogue entities name, best first. One query; used for a "which
        service?" card so its options are retrieved service pages."""
        if not entities:
            return []
        title = Document.doc_metadata["title"].as_string()
        distance = Chunk.embedding.cosine_distance(query_embedding)
        rank_in_doc = func.row_number().over(
            partition_by=Chunk.document_id, order_by=distance
        )
        inner = (
            select(Chunk.id.label("chunk_id"), rank_in_doc.label("rank"))
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.document_id.not_in(quarantined_document_ids()))
            .where(
                func.concat_ws(" ", Document.source_name, title).op("~*")(
                    entity_sql_pattern(entities)
                )
            )
        )
        for key, value in usable_filters(filters).items():
            inner = inner.where(Chunk.chunk_metadata[key].as_string() == value)
        best = inner.subquery()
        stmt = (
            select(
                Chunk,
                distance.label("distance"),
                Document.source_url,
                Document.section_links,
                title,
                Document.source_name,
                _ROUTE,
            )
            .join(Document, Document.id == Chunk.document_id)
            .join(best, best.c.chunk_id == Chunk.id)
            .options(defer(Chunk.embedding))
            .where(best.c.rank == 1)
            .order_by(distance)
        )
        rows = (await self.session.execute(stmt)).all()
        await self.session.commit()
        found: list[tuple[Chunk, float]] = []
        for (
            chunk,
            dist,
            source_url,
            section_links,
            doc_title,
            source_name,
            route,
        ) in rows:
            self.document_links[chunk.document_id] = _document_link(
                source_url, section_links, doc_title, source_name, route
            )
            found.append((chunk, 1 - dist))
        return found

    async def chunks_in_documents(  # pylint: disable=too-many-locals
        self,
        query_embedding: list[float],
        document_ids: list[str],
        per_document: int = 2,
    ) -> list[tuple[Chunk, float]]:
        """The ``per_document`` best chunks (cosine) of each given document,
        best first (a reply to a clarification: the options' pages)."""
        ids = [uuid.UUID(d) for d in dict.fromkeys(document_ids)]
        if not ids:
            return []
        title = Document.doc_metadata["title"].as_string()
        distance = Chunk.embedding.cosine_distance(query_embedding)
        rank_in_doc = func.row_number().over(
            partition_by=Chunk.document_id, order_by=distance
        )
        inner = (
            select(Chunk.id.label("chunk_id"), rank_in_doc.label("rank"))
            .where(Chunk.document_id.in_(ids))
            .where(Chunk.document_id.not_in(quarantined_document_ids()))
        ).subquery()
        stmt = (
            select(
                Chunk,
                distance.label("distance"),
                Document.source_url,
                Document.section_links,
                title,
                Document.source_name,
                _ROUTE,
            )
            .join(Document, Document.id == Chunk.document_id)
            .join(inner, inner.c.chunk_id == Chunk.id)
            .options(defer(Chunk.embedding))
            .where(inner.c.rank <= per_document)
            .order_by(distance)
        )
        rows = (await self.session.execute(stmt)).all()
        await self.session.commit()
        found: list[tuple[Chunk, float]] = []
        for (
            chunk,
            dist,
            source_url,
            section_links,
            doc_title,
            source_name,
            route,
        ) in rows:
            self.document_links[chunk.document_id] = _document_link(
                source_url, section_links, doc_title, source_name, route
            )
            found.append((chunk, 1 - dist))
        return found

    async def chunks_for_route(self, route: str, limit: int = 8) -> list[Chunk]:
        """The chunks of the (non-quarantined) document at site ``route``,
        in reading order (a fixed page such as the contact page)."""
        title = Document.doc_metadata["title"].as_string()
        stmt = (
            select(
                Chunk,
                Document.source_url,
                Document.section_links,
                title,
                Document.source_name,
                _ROUTE,
            )
            .join(Document, Document.id == Chunk.document_id)
            .options(defer(Chunk.embedding))
            .where(Document.doc_metadata["route"].as_string() == route)
            .where(Chunk.document_id.not_in(quarantined_document_ids()))
            .order_by(Chunk.document_id, Chunk.chunk_index)
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        await self.session.commit()
        chunks: list[Chunk] = []
        for chunk, source_url, section_links, doc_title, source_name, doc_route in rows:
            self.document_links[chunk.document_id] = _document_link(
                source_url, section_links, doc_title, source_name, doc_route
            )
            chunks.append(chunk)
        return chunks

    async def _fetch_entity_chunks(  # pylint: disable=too-many-locals
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
                _ROUTE,
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
            for chunk, _d, _u, _s, doc_title, source_name, _r in rows
        }
        best: dict[str, float] = {}
        for chunk, dist, *_rest in rows:
            doc_id = str(chunk.document_id)
            best[doc_id] = max(best.get(doc_id, float("-inf")), 1 - dist)
        keep = set(
            pick_entity_documents(missing, names, best, _MAX_ENTITY_FETCH_DOCUMENTS)
        )

        fetched: list[tuple[Chunk, float]] = []
        for (
            chunk,
            dist,
            source_url,
            section_links,
            doc_title,
            source_name,
            route,
        ) in rows:
            if str(chunk.document_id) not in keep:
                continue
            self.document_links[chunk.document_id] = _document_link(
                source_url, section_links, doc_title, source_name, route
            )
            fetched.append((chunk, 1 - dist))
        logger.info(
            "retrieval_entity_fetch entities=%s chunks=%d", missing, len(fetched)
        )
        return fetched

    @staticmethod
    def _select_diverse_chunks(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        candidates: list[tuple[Chunk, float]],
        top_k: int,
        max_documents: int | None = None,
        signal: HybridSignal | None = None,
        order: dict[uuid.UUID, float] | None = None,
    ) -> list[tuple[Chunk, float]]:
        """Allocate the result budget across the top-scoring documents.

        Guarantees cross-document coverage: if two or more documents score
        strongly, each gets a fair quota of slots via round-robin, so a
        second genuinely relevant document is never starved by the single
        top-scoring one (the bug this replaces).

        ``order`` (chunk id -> rank key, higher first) is the cross-encoder
        order; it ranks documents, chunks and the final list. Without it
        (reranker off) cosine + hybrid boost is used.

        Entity seeding is a recall aid only: a document the query names is
        never dropped by the document cap and keeps ONE chunk (its best),
        or one per section heading for "what does X cover"; everything else
        competes on relevance, so the answer document is never crowded out.
        """

        if not candidates:
            return []

        signal = signal or HybridSignal()

        def key(item: tuple[Chunk, float]) -> float:
            if order is not None:
                return order.get(item[0].id, float("-inf"))
            return signal.rank_key(item[0].id, item[1])

        by_doc: dict[str, list[tuple[Chunk, float]]] = defaultdict(list)
        doc_order: list[str] = []

        for chunk, score in candidates:
            doc_id = str(chunk.document_id)
            if doc_id not in by_doc:
                doc_order.append(doc_id)
            by_doc[doc_id].append((chunk, score))

        # "What does X cover": headed chunks of the named document first.
        spread_bonus = _SPREAD_HEADING_BONUS if signal.spread else 0.0
        for doc_chunks in by_doc.values():
            doc_chunks.sort(
                key=lambda item: (
                    key(item)
                    + (
                        spread_bonus
                        if order is None
                        and _heading(item[0])
                        and str(item[0].document_id) in signal.entity_doc_ids
                        else 0.0
                    )
                ),
                reverse=True,
            )

        # Documents ranked by their single best chunk.
        ranked_docs = sorted(
            doc_order, key=lambda doc_id: key(by_doc[doc_id][0]), reverse=True
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

        # Named documents are seeded first with ONE chunk each (recall aid);
        # "What does X cover": up to SPREAD_MAX_CHUNKS, one per heading.
        # Beyond the first, a seeded chunk must clear the retrieval floor.
        # One slot always stays free for others.
        seed_budget = max(1, top_k - 1)
        levels = SPREAD_MAX_CHUNKS if signal.spread else 1
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

        # Backfill any remaining slots with the next best chunks overall,
        # regardless of document, once quotas are satisfied.
        for item in sorted(candidates, key=key, reverse=True):
            if len(selected) >= top_k:
                break

            if item[0].id in selected_ids:
                continue

            selected.append(item)
            selected_ids.add(item[0].id)

        selected.sort(key=key, reverse=True)

        return selected


class _RankJob(NamedTuple):
    query_text: str | None
    candidates: list[tuple[Chunk, float]]
    names: dict[uuid.UUID, str]
    hybrid: bool
    candidate_k: int
    top_k: int
    max_documents: int | None


def _rank_and_select(
    job: _RankJob,
) -> tuple[list[tuple[Chunk, float]], HybridSignal, dict[uuid.UUID, float]]:
    """STEP 5 + STEP 3 (pure CPU, run in a worker thread): hybrid signal,
    pool trim, cross-encoder rerank, cross-document quota selection."""
    candidates = job.candidates
    signal = HybridSignal()
    if job.hybrid and job.query_text:
        # String ops only (no embedding call): lexical + named-entity
        # boost, then trim the pool back to its normal rerank size.
        signal = compute_hybrid_signal(job.query_text, candidates, job.names)
        candidates = trim_pool(candidates, signal, job.candidate_k)

    order: dict[uuid.UUID, float] | None = None
    logits: dict[uuid.UUID, float] = {}
    if job.query_text:
        boosts = [signal.boosts.get(chunk.id, 0.0) for chunk, _ in candidates]
        candidates = rerank(job.query_text, candidates, boosts, logits)
        if logits:
            # The cross-encoder order is final: selection never re-sorts
            # by cosine (position rank, higher is better).
            order = {
                chunk.id: float(-pos) for pos, (chunk, _s) in enumerate(candidates)
            }

    selected = VectorStore._select_diverse_chunks(  # pylint: disable=protected-access
        candidates=candidates,
        top_k=job.top_k,
        max_documents=job.max_documents,
        signal=signal,
        order=order,
    )
    return selected, signal, logits
