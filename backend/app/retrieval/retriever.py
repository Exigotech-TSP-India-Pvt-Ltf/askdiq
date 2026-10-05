"""Embeds a query and retrieves the top-k relevant chunks.

STEP 4: compound questions (two "?", an "and" joining two clauses, etc.) are
split into sub-queries so each topic gets its own embedding + retrieval pass
instead of being averaged into one vector that matches neither topic well.
"""

import asyncio
import re
import uuid

from app.core.config import get_settings
from app.retrieval.embeddings import embed_text_sync
from app.retrieval.vector_store import VectorStore
from app.schemas.query import ChatTurn, SourceChunk


_AND_SPLIT_RE = re.compile(r"\band\b", re.IGNORECASE)

# Referential language that signals a query only makes sense alongside prior
# conversation turns (e.g. "what about ITS pricing?", "does IT support SSO?").
_REFERENTIAL_RE = re.compile(
    r"\b(it|its|this|that|these|those|they|them|their|the same|"
    r"what about|how about|and (?:the|its|their))\b",
    re.IGNORECASE,
)

# Almost every genuine follow-up either has referential language above, or
# is extremely terse ("why?", "how much?") — real standalone questions that
# just happen to be short ("What is Zero Trust?", "What is the Essential
# Eight?") are usually 4-5 words. Kept low (not 6) so those aren't
# mislabeled as follow-ups and don't get an unrelated prior turn's history
# folded in.
_SHORT_QUERY_WORD_COUNT = 3


def needs_conversation_context(query: str) -> bool:
    """Heuristic: does this query look like a follow-up on its own?

    Rule-based (no LLM call), mirroring the style of decompose_query:
    flags queries that are either short or contain referential language
    that can't be resolved without the preceding conversation turns.
    """

    text = query.strip()

    if not text:
        return False

    if _REFERENTIAL_RE.search(text):
        return True

    return len(text.split()) <= _SHORT_QUERY_WORD_COUNT


def contextualize_query(query: str, history: list[ChatTurn] | None) -> str:
    """Fold recent conversation turns into the retrieval query when needed.

    Only applied when the current query looks like a follow-up (see
    needs_conversation_context); otherwise the query is returned unchanged
    so on-topic standalone questions aren't diluted by unrelated history.
    """

    if not history or not needs_conversation_context(query):
        return query

    # Last user question + last assistant answer give enough grounding to
    # resolve "it"/"that"-style references without pulling in the whole
    # conversation (which would blur the embedding across topics).
    recent_user = next(
        (turn.content for turn in reversed(history) if turn.role == "user"),
        None,
    )
    recent_assistant = next(
        (turn.content for turn in reversed(history) if turn.role == "assistant"),
        None,
    )

    parts = [part for part in (recent_user, recent_assistant) if part]

    if not parts:
        return query

    return " ".join(parts) + " " + query


def decompose_query(query: str) -> list[str]:
    """Cheap rule-based split of a compound query into sub-queries.

    Heuristics only (no LLM call): multiple '?' marks, or a single question
    joined by "and" where both halves look like independent clauses (each
    has enough words to plausibly be its own topic).
    """

    text = query.strip()

    if not text:
        return [text]

    question_marks = text.count("?")

    if question_marks >= 2:
        parts = [p.strip() for p in re.split(r"(?<=\?)\s*", text) if p.strip()]
        if len(parts) >= 2:
            return parts

    if question_marks >= 1 and _AND_SPLIT_RE.search(text):
        first, second = _AND_SPLIT_RE.split(text, maxsplit=1)
        first, second = first.strip(), second.strip()

        if len(first.split()) >= 3 and len(second.split()) >= 3:
            if not first.endswith("?"):
                first += "?"
            if not second.endswith("?"):
                second += "?"
            return [first, second]

    return [text]


async def embed_query(query: str) -> list[float]:
    """Generate a query embedding without blocking the async event loop."""

    return await asyncio.to_thread(
        embed_text_sync,
        query,
    )


async def retrieve(
    vector_store: VectorStore,
    query: str,
    top_k: int = 5,
    filters: dict | None = None,
    history: list[ChatTurn] | None = None,
) -> list[SourceChunk]:

    settings = get_settings()

    # Decide compound-ness from the user's own current question ONLY — never
    # from the history-folded text. Folding in a prior answer (which often
    # contains an incidental "and") could otherwise make decompose_query
    # misfire on a genuinely simple question, silently doubling every
    # embed+DB+rerank call for that request (a real ~2x latency bug found
    # via profiling: two ~4.3s rerank calls instead of one).
    sub_queries = (
        decompose_query(query) if settings.enable_query_decomposition else [query]
    )
    is_compound = len(sub_queries) > 1

    if is_compound:
        # Each half still gets history folded in independently, so a
        # follow-up reference inside one clause can still be resolved.
        sub_queries = [contextualize_query(sq, history) for sq in sub_queries]
    else:
        sub_queries = [contextualize_query(query, history)]

    # Compound questions need enough budget to hold evidence for both parts.
    final_top_k = (
        min(max(top_k, settings.compound_query_top_k), 10) if is_compound else top_k
    )
    per_query_k = min(final_top_k, max(top_k, 6))

    async def _embed_and_search(sub_query: str) -> list[tuple]:
        embedding = await embed_query(sub_query)
        return await vector_store.similarity_search(
            embedding,
            top_k=per_query_k,
            filters=filters,
            query_text=sub_query,
        )

    # Independent per sub-query — run concurrently instead of sequentially
    # so a compound question doesn't pay 2x the embed+search latency.
    per_query_results: list[list[tuple]] = await asyncio.gather(
        *(_embed_and_search(sub_query) for sub_query in sub_queries)
    )

    merged: dict[uuid.UUID, tuple] = {}
    for results in per_query_results:
        for chunk, score in results:
            existing = merged.get(chunk.id)
            if existing is None or score > existing[1]:
                merged[chunk.id] = (chunk, score)

    if is_compound:
        # Round-robin merge so both sub-queries' evidence survives the cap,
        # instead of one topic's higher-scoring chunks crowding out the other.
        final: list[tuple] = []
        seen: set = set()
        index = 0

        while len(final) < final_top_k and any(
            index < len(lst) for lst in per_query_results
        ):
            for lst in per_query_results:
                if len(final) >= final_top_k:
                    break
                if index >= len(lst):
                    continue
                chunk, _ = lst[index]
                if chunk.id in seen:
                    continue
                final.append(merged[chunk.id])
                seen.add(chunk.id)
            index += 1

        results = final
    else:
        results = sorted(
            merged.values(),
            key=lambda item: item[1],
            reverse=True,
        )[:final_top_k]

    return [
        SourceChunk(
            chunk_id=str(chunk.id),
            document_id=str(chunk.document_id),
            content=chunk.content,
            score=score,
            metadata=chunk.chunk_metadata,
            embedding=list(chunk.embedding),
        )
        for chunk, score in results
    ]
