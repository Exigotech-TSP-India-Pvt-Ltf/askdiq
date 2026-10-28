"""Embeds a query and retrieves the top-k relevant chunks.

STEP 4: compound questions (two "?", an "and" joining two clauses, etc.) are
split into sub-queries so each topic gets its own embedding + retrieval pass
instead of being averaged into one vector that matches neither topic well.
"""

import asyncio
import re
import time
import uuid
from functools import lru_cache

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.guardrails.pre_checks import is_gibberish_token
from app.knowledge.links import citation_url
from app.knowledge.settings_service import current_knowledge_settings
from app.retrieval.embeddings import embed_text, embedding_timing_sink
from app.retrieval.vector_store import VectorStore
from app.schemas.query import ChatTurn, SourceChunk

# Query-decomposition heuristics.
_MAX_SHORT_INPUT_CHARS = 2
_SHORT_DOMAIN_TERMS = {"e8", "ai", "ip", "db"}
_MIN_SUBQUESTIONS = 2
_MIN_CLAUSE_WORDS = 3

logger = get_logger(__name__)

_AND_SPLIT_RE = re.compile(r"\band\b", re.IGNORECASE)

# Referential language that signals a query only makes sense alongside prior
# conversation turns (e.g. "what about ITS pricing?", "does IT support SSO?").
_REFERENTIAL_RE = re.compile(
    r"\b(it|its|this|that|these|those|they|them|their|the same|"
    r"what about|how about|and (?:the|its|their)|above|previous|earlier|"
    r"the (?:first|second|third|last) one|the one you mentioned)\b",
    re.IGNORECASE,
)
# A named subject (brand, service or framework) makes a short "Who is Exigo?"
# standalone: it needs no earlier turn to be understood. Literals only.
_NAMED_SUBJECT_RE = re.compile(
    r"\b(exigo|deploy[ _-]{0,3}iq|essential[ _-]{0,3}(?:eight|8)|e8|acsc|iso|cis|"
    r"zero[ _-]{0,3}trust|security[ _-]{0,3}baseline|m365|microsoft[ _-]{0,3}365)\b",
    re.IGNORECASE,
)
# Capitalised word after the first one: a name, not sentence case.
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][A-Za-z0-9]+\b")
_MAX_SCAN_CHARS = 2000

_SHORT_QUERY_WORD_COUNT = 3
_SHORT_FOLLOW_UP_TOKENS = {
    "why",
    "how",
    "more",
    "details",
    "pricing",
    "cost",
    "features",
    "elaborate",
    "explain",
    "what",
    "can",
    "does",
    "is",
    "are",
}


@lru_cache(maxsize=1024)
def needs_conversation_context(query: str) -> bool:
    """Heuristic: does this query look like a follow-up on its own?

    Rule-based (no LLM call), mirroring the style of decompose_query:
    flags queries that are either short interrogatives or contain referential
    language that can't be resolved without the preceding conversation turns.
    Guards against attaching history to gibberish or pure punctuation.
    """

    text = query.strip()

    if not text:
        return False

    # Never attach history if input is pure punctuation or symbols
    if re.match(r"^[\s\W_]+$", text):
        return False

    # Never attach history if input contains any gibberish token
    words = text.split()
    if any(is_gibberish_token(w) for w in words):
        return False

    # Ultra-short inputs (<= 2 chars) that are not recognized domain terms
    clean_chars = re.sub(r"[\W_]", "", text).lower()
    if (
        len(clean_chars) <= _MAX_SHORT_INPUT_CHARS
        and clean_chars not in _SHORT_DOMAIN_TERMS
    ):
        return False

    text = " ".join(text.split())[:_MAX_SCAN_CHARS]
    if _REFERENTIAL_RE.search(text):
        return True

    # "Who is Exigo?": a named subject stands on its own.
    rest = text.split(" ", 1)[1] if " " in text else ""
    named = _NAMED_SUBJECT_RE.search(text) or _PROPER_NOUN_RE.search(rest)

    # Short queries (<= 3 words) must contain at least one recognized follow-up token
    clean_words = {w.lower().strip("?!.,:;") for w in words}
    return (
        not named
        and len(words) <= _SHORT_QUERY_WORD_COUNT
        and bool(clean_words & _SHORT_FOLLOW_UP_TOKENS)
    )


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

    if question_marks >= _MIN_SUBQUESTIONS:
        parts = [p.strip() for p in re.split(r"(?<=\?)\s*", text) if p.strip()]
        if len(parts) >= _MIN_SUBQUESTIONS:
            return parts

    if question_marks >= 1 and _AND_SPLIT_RE.search(text):
        first, second = _AND_SPLIT_RE.split(text, maxsplit=1)
        first, second = first.strip(), second.strip()

        if (
            len(first.split()) >= _MIN_CLAUSE_WORDS
            and len(second.split()) >= _MIN_CLAUSE_WORDS
        ):
            if not first.endswith("?"):
                first += "?"
            if not second.endswith("?"):
                second += "?"
            return [first, second]

    return [text]


async def embed_query(query: str) -> list[float]:
    """Generate a query embedding with the async Azure client."""

    return await embed_text(query)


async def retrieve(
    vector_store: VectorStore,
    query: str,
    top_k: int = 5,
    filters: dict | None = None,
    history: list[ChatTurn] | None = None,
    max_documents: int | None = None,
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
        logger.info(
            "subquery_decomposition",
            original_query=query,
            subqueries_count=len(sub_queries),
            subqueries=sub_queries,
        )
        # Each half still gets history folded in independently, so a
        # follow-up reference inside one clause can still be resolved.
        sub_queries = [contextualize_query(sq, history) for sq in sub_queries]
    else:
        ctx_query = contextualize_query(query, history)
        if ctx_query != query:
            logger.info(
                "history_contextualized",
                original=query,
                contextualized=ctx_query,
            )
        sub_queries = [ctx_query]

    # Compound questions need enough budget to hold evidence for both parts.
    final_top_k = (
        min(max(top_k, settings.compound_query_top_k), 10) if is_compound else top_k
    )
    per_query_k = min(final_top_k, max(top_k, 6))

    # Embeddings are independent API calls, so they run concurrently. The DB
    # searches share one AsyncSession, which cannot run two operations at
    # once (SQLAlchemy raises IllegalStateChangeError), so they run in order.
    embed_started = time.perf_counter()
    embeddings = await asyncio.gather(*(embed_query(sq) for sq in sub_queries))
    embed_sink = embedding_timing_sink.get()
    if embed_sink is not None:
        embed_sink.append((time.perf_counter() - embed_started) * 1000)
    per_query_results: list[list[tuple]] = []
    for sub_query, embedding in zip(sub_queries, embeddings, strict=True):
        per_query_results.append(
            await vector_store.similarity_search(
                embedding,
                top_k=per_query_k,
                filters=filters,
                query_text=sub_query,
                **({"max_documents": max_documents} if max_documents else {}),
            )
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

    embeddings_by_id = await vector_store.fetch_embeddings(
        [chunk.id for chunk, _ in results]
    )
    allowed_hosts = current_knowledge_settings().allowed_hosts
    links = getattr(vector_store, "document_links", {})

    def _link(chunk) -> tuple[str | None, str | None]:  # noqa: ANN001
        """Every chunk cites its document URL (+ anchor with section links)."""
        found = links.get(chunk.document_id)
        if found is None:
            return None, None
        url = citation_url(
            found.source_url,
            section_links=found.section_links,
            metadata=chunk.chunk_metadata,
            allowed_hosts=allowed_hosts,
        )
        return url, found.title

    sources = []
    for chunk, score in results:
        url, title = _link(chunk)
        sources.append(
            SourceChunk(
                chunk_id=str(chunk.id),
                document_id=str(chunk.document_id),
                content=chunk.content,
                score=score,
                metadata=chunk.chunk_metadata,
                embedding=embeddings_by_id.get(chunk.id),
                url=url,
                title=title,
            )
        )
    return sources
