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
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.db_models import Chunk
from app.retrieval.reranker import rerank

settings = get_settings()


class VectorStore:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        filters: dict | None = None,
        query_text: str | None = None,
    ) -> list[tuple[Chunk, float]]:
        """Return the top_k most relevant chunks for a query embedding.

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

        stmt = select(
            Chunk,
            Chunk.embedding.cosine_distance(query_embedding).label("distance"),
        )

        if filters:
            for key, value in filters.items():
                stmt = stmt.where(Chunk.chunk_metadata[key].as_string() == str(value))

        stmt = stmt.order_by("distance").limit(candidate_k)

        rows = (await self.session.execute(stmt)).all()

        if not rows:
            return []

        # ---------------------------------------------------------
        # STEP 2: Dedupe by chunk id, keeping the best cosine score
        # ---------------------------------------------------------

        unique_candidates: dict = {}

        for chunk, distance in rows:
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
        # STEP 5: Rerank the candidate pool before final selection
        # ---------------------------------------------------------

        if query_text:
            # CPU-bound ONNX inference — off the event loop so it doesn't
            # stall every other concurrent request behind it.
            candidates = await asyncio.to_thread(rerank, query_text, candidates)

        # ---------------------------------------------------------
        # STEP 3: Cross-document quota selection
        # ---------------------------------------------------------

        return self._select_diverse_chunks(
            candidates=candidates,
            top_k=top_k,
        )

    @staticmethod
    def _select_diverse_chunks(
        candidates: list[tuple[Chunk, float]],
        top_k: int,
    ) -> list[tuple[Chunk, float]]:
        """Allocate the result budget across the top-scoring documents.

        Guarantees cross-document coverage: if two or more documents score
        strongly, each gets a fair quota of slots via round-robin, so a
        second genuinely relevant document is never starved by the single
        top-scoring one (the bug this replaces).
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

        # Documents ranked by their single best-scoring chunk.
        ranked_docs = sorted(
            doc_order,
            key=lambda doc_id: by_doc[doc_id][0][1],
            reverse=True,
        )

        quota_docs = ranked_docs[
            : min(settings.cross_doc_quota_max_documents, len(ranked_docs))
        ]
        per_doc_quota = max(1, top_k // len(quota_docs))

        selected: list[tuple[Chunk, float]] = []
        selected_ids: set = set()
        doc_taken: defaultdict[str, int] = defaultdict(int)
        doc_cursor: defaultdict[str, int] = defaultdict(int)

        progressed = True
        while len(selected) < top_k and progressed:
            progressed = False

            for doc_id in quota_docs:
                if len(selected) >= top_k:
                    break

                if doc_taken[doc_id] >= per_doc_quota:
                    continue

                doc_chunks = by_doc[doc_id]

                while doc_cursor[doc_id] < len(doc_chunks):
                    chunk, score = doc_chunks[doc_cursor[doc_id]]
                    doc_cursor[doc_id] += 1

                    if chunk.id in selected_ids:
                        continue

                    selected.append((chunk, score))
                    selected_ids.add(chunk.id)
                    doc_taken[doc_id] += 1
                    progressed = True
                    break

        # Backfill any remaining slots with the next best-scoring chunks
        # overall, regardless of document, once quotas are satisfied.
        for chunk, score in candidates:
            if len(selected) >= top_k:
                break

            if chunk.id in selected_ids:
                continue

            selected.append((chunk, score))
            selected_ids.add(chunk.id)

        selected.sort(key=lambda item: item[1], reverse=True)

        return selected
