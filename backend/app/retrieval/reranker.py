"""STEP 5 reranker over the broad cosine-recall candidate pool.

Chosen algorithm: cross-encoder reranking, scoring (query, chunk) pairs with
`Xenova/ms-marco-MiniLM-L-6-v2` via `onnxruntime` (app/retrieval/cross_encoder.py). This is the
PREFERRED option for a small local corpus (per the task spec) without adding
a torch/sentence-transformers dependency — `onnxruntime` + `tokenizers` are
already in requirements.txt. MMR is intentionally not layered on top: dedupe
by chunk id (STEP 2) plus cross-document quota (STEP 3) already provide the
diversity MMR would add, on a corpus this small.

The cross-encoder is used to REORDER the candidate pool only — its raw
logits aren't on a comparable, query-independent scale (empirically they can
be strongly negative even for on-topic-but-narrow questions), so the
reported score stays the original cosine similarity. That keeps the
off-topic / low-confidence gates in orchestrator.py well-calibrated.
"""

import logging
import math
import re
import threading
import time
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.models.db_models import Chunk

logger = logging.getLogger(__name__)

settings = get_settings()

# Optional per-request sink: the evaluation harness sets a list here and each
# rerank call appends its duration in ms (None outside evaluation).
rerank_timing_sink: ContextVar[list[float] | None] = ContextVar(
    "rerank_timing_sink", default=None
)


_rerank_slots = threading.BoundedSemaphore(max(1, settings.rerank_max_concurrency))
_encoder_lock = threading.Lock()


@lru_cache(maxsize=1)
def _load_encoder():
    from app.retrieval.cross_encoder import TextCrossEncoder  # noqa: PLC0415 - heavy model dependency, loaded on first use

    return TextCrossEncoder(model_name=settings.reranker_model)


def _get_encoder():
    # Lock so concurrent first callers don't each load the model / race imports.
    with _encoder_lock:
        return _load_encoder()


def preload_reranker() -> None:
    """Force-load the cross-encoder at process startup instead of on the
    first real request.

    `_get_encoder()` is `@lru_cache`'d, so without this the ONNX model load
    (plus a first-run ~90MB download from HF Hub) happens lazily inside the
    very first user's /query call, making that request take much longer
    than every one after it. Calling this once at startup moves that cost
    out of the request path entirely.
    """
    if not settings.enable_reranker:
        return

    try:
        _get_encoder()
        logger.info("reranker_preload status=ok model=%s", settings.reranker_model)
    except Exception as exc:
        logger.error("reranker_preload status=failed error=%s", exc)


def is_reranker_loaded() -> bool:
    """Return True if the reranker model weights are loaded in memory."""
    return _load_encoder.cache_info().currsize > 0


def rerank(
    query: str,
    candidates: list[tuple[Chunk, float]],
    boosts: Sequence[float] | None = None,
) -> list[tuple[Chunk, float]]:
    """Reorder (chunk, cosine_score) candidates by cross-encoder relevance.

    The cosine score is preserved (only the order changes). Falls back to
    the original cosine ordering if the reranker model can't be loaded (e.g.
    no network on first run), so retrieval never hard-fails.

    `boosts` (one per candidate, from the lexical/entity hybrid signal) is
    added to the min-max normalised cross-encoder score, so named-entity
    evidence still counts after the cross-encoder reorders. With no boosts
    (or all zero) the order is the pure cross-encoder order.
    """

    if not settings.enable_reranker or len(candidates) <= 1:
        return candidates

    try:
        encoder = _get_encoder()
        documents = [chunk.content for chunk, _ in candidates]
        with _rerank_slots:
            # Timed inside the slot: pure compute, excluding queue wait.
            started = time.perf_counter()
            raw_scores = list(
                encoder.rerank(query, documents, batch_size=settings.rerank_batch_size)
            )
            compute_ms = (time.perf_counter() - started) * 1000
        sink = rerank_timing_sink.get()
        if sink is not None:
            sink.append(compute_ms)
    except Exception:
        logger.warning(
            "Reranker unavailable, falling back to cosine order", exc_info=True
        )
        return candidates

    keys = _fused_rerank_keys(raw_scores, boosts)
    order = sorted(range(len(candidates)), key=lambda i: keys[i], reverse=True)

    return [candidates[i] for i in order]


def _fused_rerank_keys(
    raw_scores: Sequence[float], boosts: Sequence[float] | None
) -> list[float]:
    """Cross-encoder logits, plus hybrid boosts on a 0-1 normalised scale."""
    if not boosts or not any(boosts) or len(boosts) != len(raw_scores):
        return [float(score) for score in raw_scores]
    low, high = min(raw_scores), max(raw_scores)
    span = (high - low) or 1.0
    return [
        (float(score) - low) / span + boost
        for score, boost in zip(raw_scores, boosts, strict=True)
    ]


# ---------------------------------------------------------------------------
# Hybrid lexical / named-entity signal
#
# Pure cosine over generic chunks lets a broad "How DeployIQ Works" or home
# page chunk outrank the document a question names ("Zero Trust", "ISO 27001",
# "Trust Center"). This adds string-only (no embedding call, linear in the
# candidate pool) evidence on top of the cosine score:
#   * lexical boost: IDF-weighted share of the query's content terms found in
#     the chunk's heading / body / document name;
#   * entity-document boost: every chunk of a document whose name/title
#     matches a framework or service named in the query.
# The boost only ever re-ranks; the reported score stays the cosine.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_NAME_SPLIT_RE = re.compile(r"[\s_.\-]+")
_DOC_EXT_RE = re.compile(r"\.(?:md|markdown|docx)$", re.IGNORECASE)
_MAX_QUERY_TERMS = 32
_MAX_QUERY_ENTITIES = 3
_HEADING_WEIGHT = 0.3
_MIN_STEM_LEN = 4
_MIN_TERM_LEN = 3

_STOPWORD_TEXT = """a about after all also an and any are as at be been being but by can could
    did do does doing for from get got had has have how i if in into is it its
    me more most my no not of on one or our out over so some such than that the
    their them then there these they this those to up us was we were what when
    where which who whom why will with would you your tell give show let know
    want need please explain describe list cover covers include includes work
    works follow part parts leave"""
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())

# Catalogue frameworks / services a question can name. Each entry maps a query
# pattern to the pattern that identifies the matching document by its name or
# title (separators already normalised to spaces). Brand-wide words such as
# "DeployIQ" are deliberately absent: they name every document.
_ENTITY_CATALOGUE: tuple[tuple[str, re.Pattern[str], re.Pattern[str]], ...] = tuple(
    (key, re.compile(query_re, re.IGNORECASE), re.compile(doc_re, re.IGNORECASE))
    for key, query_re, doc_re in (
        ("zero trust", r"\bzero[\s_-]*trust\b", r"\bzero trust\b"),
        ("iso 27001", r"\biso[\s_-]*27001\b", r"\biso 27001\b"),
        (
            "essential eight",
            r"\bessential[\s_-]*(?:eight|8)\b|\be8\b",
            r"\be8\b|\bessential (?:eight|8)\b",
        ),
        ("cis", r"\bcis\b", r"\bcis\b"),
        (
            "trust center",
            r"\btrust[\s_-]*cent(?:er|re)\b",
            r"\btrust cent(?:er|re)\b",
        ),
        (
            "security baseline",
            r"\bsecurity[\s_-]*baseline\b",
            r"\bsecurity baseline\b",
        ),
        ("responsible ai", r"\bresponsible[\s_-]*ai\b", r"\bresponsible ai\b"),
    )
)
_ENTITY_DOC_RE = {key: doc_re for key, _query_re, doc_re in _ENTITY_CATALOGUE}


@dataclass(frozen=True)
class HybridSignal:
    """Rank adjustments for one retrieval candidate pool."""

    boosts: dict[Any, float] = field(default_factory=dict)  # chunk id -> boost
    entity_doc_ids: tuple[str, ...] = ()  # documents named by the query
    spread: bool = False  # "what does X cover": one chunk per section heading

    def rank_key(self, chunk_id: Any, score: float) -> float:
        return score + self.boosts.get(chunk_id, 0.0)


# ---------------------------------------------------------------------------
# Query-intent expansion: question words -> the heading/body vocabulary the
# documents use. Deterministic; feeds only the lexical boost and the entity
# fetch ranking (the embedding is never touched).
# ---------------------------------------------------------------------------

_EXPANDED_TERM_WEIGHT = 0.7  # expanded terms count less than the user's own words
SPREAD_MAX_CHUNKS = 4  # "what does X cover": chunks kept per named document
INTENT_ENTITY_WEIGHT = 0.30  # added to entity-document chunks matching the intent
INTENT_HEADING_SHARE = 0.6  # heading vs body share of the intent match score
_INTENT_BODY_HITS = 3  # distinct body terms that saturate the body share
_SHORT_TERM_LEN = 5  # shorter terms match whole words, longer ones as prefixes

# (question-word pattern, vocabulary, spread across section headings)
_INTENT_RULES: tuple[tuple[re.Pattern[str], tuple[str, ...], bool], ...] = tuple(
    (re.compile(query_re, re.IGNORECASE), terms, spread)
    for query_re, terms, spread in (
        (
            r"\b(?:stages?|steps?|phases?|process(?:es)?|workflows?|journey|"
            r"deliver(?:y|ies|s)?|methodolog(?:y|ies)|approach(?:es)?)\b",
            (
                "approach",
                "delivery approach",
                "journey",
                "how it works",
                "phases",
                "assess",
                "prioritise",
                "design",
                "implement",
                "validate",
            ),
            False,
        ),
        (
            r"\b(?:covers?|covered|includ(?:e|es|ed|ing)|contains?|offers?)\b|"
            r"\bwhat\s+is\s+in\b",
            ("sections", "what we", "key", "areas"),
            True,
        ),
        (
            r"\b(?:outcomes?|benefits?|results?)\b",
            ("outcomes", "benefits", "value"),
            False,
        ),
        (
            r"\b(?:pricing|prices?|costs?|plans?)\b",
            ("plans", "starter", "professional", "free"),
            False,
        ),
        (
            r"\bwho\s+is\s+(?:it|this)\s+for\b|\baudience\b",
            ("who", "designed for", "for organisations"),
            False,
        ),
    )
)


@dataclass(frozen=True)
class QueryIntent:
    """Expanded vocabulary for a question (empty when nothing matched)."""

    terms: tuple[str, ...] = ()
    spread: bool = False

    def __bool__(self) -> bool:
        return bool(self.terms)

    def pattern(self, *, postgres: bool = False) -> str:
        """Regex matching any term at a word start (short terms as whole words)."""
        start, end = (r"\m", r"\M") if postgres else (r"\b", r"\b")
        parts = [
            re.escape(term) + (r"\w*" if len(term) >= _SHORT_TERM_LEN else end)
            for term in self.terms
        ]
        return start + "(?:" + "|".join(parts) + ")"

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.pattern(), re.IGNORECASE)

    def stems(self) -> list[str]:
        """Content-term stems of the vocabulary (for the lexical boost)."""
        seen: dict[str, None] = {}
        for term in self.terms:
            for tok in _TOKEN_RE.findall(term.lower()):
                if tok not in _STOPWORDS and len(tok) >= _MIN_TERM_LEN:
                    seen.setdefault(_stem(tok), None)
        return list(seen)

    def match(self, heading: str, content: str) -> float:
        """0..1: heading hit (most weight) plus distinct body terms."""
        if not self:
            return 0.0
        regex = self.compiled()
        head = 1.0 if regex.search(heading) else 0.0
        hits = {m.lower() for m in regex.findall(content)}
        body = min(1.0, len(hits) / _INTENT_BODY_HITS)
        return INTENT_HEADING_SHARE * head + (1 - INTENT_HEADING_SHARE) * body


def query_intent(query: str) -> QueryIntent:
    """Vocabulary the question's words map to; empty when disabled."""
    if not query or not settings.retrieval_intent_expansion_enabled:
        return QueryIntent()
    terms: dict[str, None] = {}
    spread = False
    for query_re, vocabulary, spreads in _INTENT_RULES:
        if query_re.search(query):
            terms.update(dict.fromkeys(vocabulary))
            spread = spread or spreads
    return QueryIntent(tuple(terms), spread)


def _stem(token: str) -> str:
    """Very light suffix stripping so 'stages' / 'prioritised' match 'stage' / 'prioritise'."""
    if len(token) > _MIN_STEM_LEN + 1:
        for suffix in ("ing", "ies", "es", "ed", "s"):
            if token.endswith(suffix):
                token = token[: -len(suffix)] + ("y" if suffix == "ies" else "")
                break
    if len(token) > _MIN_STEM_LEN and token.endswith("e"):
        token = token[:-1]
    return token


def _stems(text: str) -> set[str]:
    return {_stem(tok) for tok in _TOKEN_RE.findall(text.lower())}


def _query_terms(query: str) -> list[str]:
    """Distinct content-term stems of the query, in first-seen order."""
    seen: dict[str, None] = {}
    for tok in _TOKEN_RE.findall(query.lower()):
        if tok in _STOPWORDS or (len(tok) < _MIN_TERM_LEN and not tok.isdigit()):
            continue
        seen.setdefault(_stem(tok), None)
    return list(seen)[:_MAX_QUERY_TERMS]


def named_entities(query: str) -> list[str]:
    """Catalogue entities named in the query, most recently mentioned first."""
    found: list[tuple[int, str]] = []
    for key, query_re, _doc_re in _ENTITY_CATALOGUE:
        matches = list(query_re.finditer(query))
        if matches:
            found.append((matches[-1].start(), key))
    found.sort(key=lambda item: (-item[0], item[1]))
    return [key for _, key in found[:_MAX_QUERY_ENTITIES]]


def _normalise_doc_name(name: str | None) -> str:
    if not name:
        return ""
    stem = _DOC_EXT_RE.sub("", name.strip())
    return " ".join(_NAME_SPLIT_RE.split(stem.lower())).strip()


def entity_documents(
    entities: Sequence[str], doc_names: Mapping[Any, str]
) -> list[str]:
    """Ids (as str) of documents whose name matches a named entity.

    Ordered by entity order, then document name, so results are stable.
    """
    ordered: list[str] = []
    for key in entities:
        matched = sorted(
            (_normalise_doc_name(name), str(doc_id))
            for doc_id, name in doc_names.items()
            if _ENTITY_DOC_RE[key].search(_normalise_doc_name(name))
        )
        for _name, doc_id in matched:
            if doc_id not in ordered:
                ordered.append(doc_id)
    return ordered


def _chunk_heading(chunk: Any) -> str:
    meta = getattr(chunk, "chunk_metadata", None) or {}
    return str(meta.get("section_heading") or "")


def compute_hybrid_signal(
    query: str,
    candidates: Sequence[tuple[Any, float]],
    doc_names: Mapping[Any, str] | None = None,
    *,
    lexical_boost: float | None = None,
    entity_doc_boost: float | None = None,
) -> HybridSignal:
    """Lexical + named-entity boosts for already-retrieved candidates.

    String operations only; O(candidates x query terms). Deterministic: the
    result never depends on set/dict iteration order.
    """
    lex = settings.retrieval_lexical_boost if lexical_boost is None else lexical_boost
    ent = (
        settings.retrieval_entity_doc_boost
        if entity_doc_boost is None
        else entity_doc_boost
    )
    if not settings.retrieval_hybrid_enabled or not candidates or not query:
        return HybridSignal()

    names = doc_names or {}
    entity_docs = entity_documents(named_entities(query), names)
    entity_doc_set = set(entity_docs)

    terms = _query_terms(query)
    intent = query_intent(query)
    expanded = [stem for stem in intent.stems() if stem not in terms]
    texts: list[set[str]] = []
    heads: list[set[str]] = []
    for chunk, _score in candidates:
        heading = _chunk_heading(chunk)
        name = _normalise_doc_name(names.get(chunk.document_id))
        texts.append(_stems(f"{heading} {chunk.content}"))
        heads.append(_stems(f"{heading} {name}"))

    # IDF over the candidate pool: a term found in every candidate says
    # nothing; terms found in none of them cannot discriminate either.
    pool = len(candidates)
    weights: dict[str, float] = {}
    for scale, group in ((1.0, terms), (_EXPANDED_TERM_WEIGHT, expanded)):
        for term in group:
            df = sum(1 for stems in texts if term in stems)
            if df:
                weights[term] = scale * math.log(1.0 + (pool - df + 0.5) / (df + 0.5))
    total = sum(weights.values())

    boosts: dict[Any, float] = {}
    for (chunk, _score), text_stems, head_stems in zip(
        candidates, texts, heads, strict=True
    ):
        boost = 0.0
        if lex > 0 and total > 0:
            body = sum(w for t, w in weights.items() if t in text_stems) / total
            head = sum(w for t, w in weights.items() if t in head_stems) / total
            boost += lex * ((1 - _HEADING_WEIGHT) * body + _HEADING_WEIGHT * head)
        if ent > 0 and str(chunk.document_id) in entity_doc_set:
            boost += ent
            if intent:
                boost += INTENT_ENTITY_WEIGHT * intent.match(
                    _chunk_heading(chunk), chunk.content
                )
        if boost:
            boosts[chunk.id] = boost
    return HybridSignal(
        boosts=boosts, entity_doc_ids=tuple(entity_docs), spread=intent.spread
    )


def entity_reserved_indices(
    ranked: Sequence[tuple[int, tuple[Any, float]]],
    entity_doc_ids: Sequence[str],
    per_doc: int,
    budget: int,
) -> set[int]:
    """Input indices of the best `per_doc` chunks of each entity document.

    `ranked` is (index, (chunk, score)) best first. Only chunks at or above
    the retrieval floor are reserved, and at most `budget` in total.
    """
    reserved: set[int] = set()
    taken: dict[str, int] = {}
    for index, (chunk, score) in ranked:
        doc_id = str(chunk.document_id)
        if (
            doc_id in entity_doc_ids
            and taken.get(doc_id, 0) < per_doc
            and score >= settings.min_retrieval_score
            and len(reserved) < budget
        ):
            taken[doc_id] = taken.get(doc_id, 0) + 1
            reserved.add(index)
    return reserved


def trim_pool(
    candidates: Sequence[tuple[Any, float]],
    signal: HybridSignal,
    limit: int,
) -> list[tuple[Any, float]]:
    """Keep the `limit` best candidates by boosted score for the rerank pool.

    The best chunks of each entity document are guaranteed a place (at most
    half the pool) so a named document cannot be cut off before rerank. The
    result is ordered by boosted score, ties by input order.
    """
    if limit <= 0 or not candidates:
        return []
    ranked = sorted(
        enumerate(candidates),
        key=lambda item: (-signal.rank_key(item[1][0].id, item[1][1]), item[0]),
    )
    reserved = entity_reserved_indices(
        ranked,
        signal.entity_doc_ids,
        max(
            settings.retrieval_entity_min_chunks,
            SPREAD_MAX_CHUNKS if signal.spread else 0,
        ),
        max(1, limit // 2),
    )
    position = {index: pos for pos, (index, _cand) in enumerate(ranked)}
    chosen = [item for item in ranked if item[0] in reserved]
    chosen += [item for item in ranked if item[0] not in reserved]
    chosen = sorted(chosen[:limit], key=lambda item: position[item[0]])
    return [candidate for _index, candidate in chosen]


# ---------------------------------------------------------------------------
# Entity document fetch helpers (see VectorStore._fetch_entity_chunks)
# ---------------------------------------------------------------------------

_SQL_SEP = "[^a-z0-9]+"
_SQL_EDGE_L = "(^|[^a-z0-9])"
_SQL_EDGE_R = "([^a-z0-9]|$)"
# PostgreSQL (case-insensitive) regex per entity, matched against
# "<source_name> <title>"; mirrors the Python document patterns in
# _ENTITY_CATALOGUE. The Python pattern re-checks every row afterwards.
_ENTITY_SQL_RE: dict[str, str] = {
    key: _SQL_EDGE_L + body + _SQL_EDGE_R
    for key, body in {
        "zero trust": f"zero{_SQL_SEP}trust",
        "iso 27001": f"iso{_SQL_SEP}27001",
        "essential eight": f"(e8|essential{_SQL_SEP}(eight|8))",
        "cis": "cis",
        "trust center": f"trust{_SQL_SEP}cent(er|re)",
        "security baseline": f"security{_SQL_SEP}baseline",
        "responsible ai": f"responsible{_SQL_SEP}ai",
    }.items()
}


def entity_sql_pattern(entities: Sequence[str]) -> str:
    """One alternation matching the documents of the given entities."""
    return "|".join(f"({_ENTITY_SQL_RE[key]})" for key in entities)


def entities_missing_from_pool(
    entities: Sequence[str],
    pool: Sequence[tuple[Any, float]],
    doc_names: Mapping[Any, str],
    min_chunks: int,
    intent: QueryIntent | None = None,
) -> list[str]:
    """Named entities whose document is under-represented in the candidate pool.

    That is: fewer than `min_chunks` chunks in the pool or, when the question
    carries an `intent`, no chunk whose section heading matches its vocabulary.
    """
    heading_re = intent.compiled() if intent else None
    missing: list[str] = []
    for key in entities:
        doc_ids = set(entity_documents([key], doc_names))
        chunks = [chunk for chunk, _score in pool if str(chunk.document_id) in doc_ids]
        if len(chunks) < min_chunks or (
            heading_re is not None
            and not any(heading_re.search(_chunk_heading(c)) for c in chunks)
        ):
            missing.append(key)
    return missing


def pick_entity_documents(
    entities: Sequence[str],
    doc_names: Mapping[Any, str],
    best_score: Mapping[str, float],
    cap: int,
) -> list[str]:
    """At most `cap` document ids: best document per entity first, then by score."""
    chosen: list[str] = []
    rest: list[str] = []
    for key in entities:
        docs = sorted(
            entity_documents([key], doc_names),
            key=lambda doc_id: (-best_score.get(doc_id, 0.0), doc_id),
        )
        if docs and docs[0] not in chosen:
            chosen.append(docs[0])
        rest.extend(doc_id for doc_id in docs[1:] if doc_id not in rest)
    rest.sort(key=lambda doc_id: (-best_score.get(doc_id, 0.0), doc_id))
    return (chosen + [doc_id for doc_id in rest if doc_id not in chosen])[:cap]
