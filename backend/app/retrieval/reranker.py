# pylint: disable=too-many-lines
"""STEP 5 reranker over the broad cosine-recall candidate pool.

Chosen algorithm: cross-encoder reranking, scoring (query, chunk) pairs with
`Xenova/ms-marco-MiniLM-L-6-v2` via `onnxruntime` (app/retrieval/cross_encoder.py). This is the
PREFERRED option for a small local corpus (per the task spec) without adding
a torch/sentence-transformers dependency — `onnxruntime` + `tokenizers` are
already in requirements.txt. MMR is intentionally not layered on top: dedupe
by chunk id (STEP 2) plus cross-document quota (STEP 3) already provide the
diversity MMR would add, on a corpus this small.

The cross-encoder orders the candidate pool, and its raw logit per chunk is
kept (``logits_out``): the retrieval relevance gate is calibrated on that
logit (see ``retrieval_ce_covered_min`` in app/core/config.py), not on
cosine. The reported ``score`` stays the cosine similarity. Hybrid
lexical/entity evidence is only a capped tie-breaker on top of the logit, so
a named document can never push the real answer document out.
"""

import asyncio
import logging
import math
import re
import threading
import time
import weakref
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, TypeVar

from app.core.config import get_settings
from app.models.db_models import Chunk

logger = logging.getLogger(__name__)

settings = get_settings()

# Optional per-request sink: the evaluation harness sets a list here and each
# rerank call appends its duration in ms (None outside evaluation).
rerank_timing_sink: ContextVar[list[float] | None] = ContextVar(
    "rerank_timing_sink", default=None
)


# One asyncio.Semaphore per event loop: excess reranks wait on the loop, not
# inside a pool thread (a thread blocked on a slot is a thread lost).
_loop_slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)
_encoder_lock = threading.Lock()
_T = TypeVar("_T")


def _rerank_slots() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    slots = _loop_slots.get(loop)
    if slots is None:
        slots = asyncio.Semaphore(max(1, settings.rerank_max_concurrency))
        _loop_slots[loop] = slots
    return slots


async def run_in_rerank_slot(func: Callable[..., _T], /, *args: Any) -> _T:
    """Run CPU-bound rerank work in a worker thread, at most
    ``rerank_max_concurrency`` at once (callers over the cap wait on the loop)."""
    async with _rerank_slots():
        return await asyncio.to_thread(func, *args)


async def rerank_async(
    query: str,
    candidates: list[tuple[Chunk, float]],
    boosts: Sequence[float] | None = None,
    logits_out: dict[Any, float] | None = None,
) -> list[tuple[Chunk, float]]:
    """``rerank`` off the event loop, inside a rerank slot."""
    if not settings.enable_reranker or len(candidates) <= 1:
        return candidates
    return await run_in_rerank_slot(rerank, query, candidates, boosts, logits_out)


@lru_cache(maxsize=1)
def _load_encoder():
    from app.retrieval.cross_encoder import TextCrossEncoder  # noqa: PLC0415  # pylint: disable=import-outside-toplevel  # heavy model dependency, loaded on first use

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
    except Exception as exc:  # pylint: disable=broad-exception-caught  # startup preload must never crash the app
        logger.error("reranker_preload status=failed error=%s", exc)


def is_reranker_loaded() -> bool:
    """Return True if the reranker model weights are loaded in memory."""
    # pylint mis-infers the lru_cache wrapper's cache_info() signature.
    return _load_encoder.cache_info().currsize > 0  # pylint: disable=too-many-function-args


def rerank(
    query: str,
    candidates: list[tuple[Chunk, float]],
    boosts: Sequence[float] | None = None,
    logits_out: dict[Any, float] | None = None,
) -> list[tuple[Chunk, float]]:
    """Reorder (chunk, cosine_score) candidates by cross-encoder relevance.

    The cosine score is preserved (only the order changes); the raw
    cross-encoder logit of every candidate goes into ``logits_out`` (chunk id
    -> logit) for the relevance gate. Falls back to the original cosine
    ordering (and no logits) if the reranker model can't be loaded, so
    retrieval never hard-fails.

    `boosts` (one per candidate, from the lexical/entity hybrid signal) adds
    at most ``BOOST_MAX_LOGITS`` to a logit: a tie-breaker, never a trump.
    """

    if not settings.enable_reranker or len(candidates) <= 1:
        return candidates

    try:
        encoder = _get_encoder()
        documents = [chunk.content for chunk, _ in candidates]
        # Slot wait happens before the thread starts (run_in_rerank_slot):
        # this is pure compute, excluding queue wait.
        started = time.perf_counter()
        raw_scores = list(
            encoder.rerank(query, documents, batch_size=settings.rerank_batch_size)
        )
        compute_ms = (time.perf_counter() - started) * 1000
        sink = rerank_timing_sink.get()
        if sink is not None:
            sink.append(compute_ms)
    except Exception:  # pylint: disable=broad-exception-caught  # retrieval never hard-fails
        logger.warning(
            "Reranker unavailable, falling back to cosine order", exc_info=True
        )
        return candidates

    keys = fused_rerank_keys(raw_scores, boosts)
    if logits_out is not None:
        for (chunk, _score), logit in zip(candidates, raw_scores, strict=True):
            logits_out[chunk.id] = float(logit)
    order = sorted(range(len(candidates)), key=lambda i: keys[i], reverse=True)

    return [candidates[i] for i in order]


# Function words of the languages visitors write in besides English (es, fr,
# de, pt, it, nl). Words shared with English ("a", "in", "is", "die", "as")
# are left out.
_FOREIGN_WORDS = frozenset(
    [
        "el",
        "la",
        "los",
        "las",
        "del",
        "que",
        "qué",
        "es",
        "cómo",
        "como",
        "cuál",
        "cuáles",
        "cuánto",
        "cuanto",
        "para",
        "por",
        "con",
        "una",
        "uno",
        "y",
        "lo",
        "se",
        "su",
        "sus",
        "hay",
        "tiene",
        "tienen",
        "puedo",
        "ofrecen",
        "le",
        "les",
        "des",
        "du",
        "est",
        "sont",
        "quel",
        "quelle",
        "quels",
        "quelles",
        "comment",
        "pour",
        "avec",
        "et",
        "ou",
        "où",
        "combien",
        "vous",
        "nous",
        "qui",
        "est-ce",
        "votre",
        "vos",
        "der",
        "das",
        "ist",
        "sind",
        "wie",
        "welche",
        "welcher",
        "für",
        "mit",
        "und",
        "ein",
        "eine",
        "einen",
        "dauert",
        "kostet",
        "gibt",
        "ihr",
        "ihre",
        "sie",
        "bietet",
        "können",
        "kann",
        "são",
        "quais",
        "uma",
        "não",
        "você",
        "vocês",
        "oferecem",
        "quanto",
        "custa",
        "qual",
        "il",
        "di",
        "che",
        "sono",
        "gli",
        "della",
        "quali",
        "quanto",
        "offrite",
        "het",
        "een",
        "zijn",
        "hoe",
        "wat",
        "voor",
        "jullie",
    ]
)
_ENGLISH_TEXT = """about and any are can could did do does for has have how i is
    it its my of or our should that the there these this those to we what when
    where which who why will with would you your"""
_ENGLISH_WORDS = frozenset(_ENGLISH_TEXT.split())
_WORD_TOKEN_RE = re.compile(
    r"[^\W\d_][^\W\d_'\N{RIGHT SINGLE QUOTATION MARK}-]*", re.UNICODE
)
_LATIN_RE = re.compile(r"[A-Za-zÀ-ɏ]")


def looks_english(text: str) -> bool:
    """Cheap language check for the English-only cross-encoder: False for a
    non-Latin script or a question whose function words are mostly another
    language's ("¿Cuáles son ...", "Wie lange dauert ..."). Defaults to True."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return True
    latin = sum(1 for ch in letters if _LATIN_RE.match(ch))
    if latin < len(letters) / 2:
        return False
    words = [w.lower() for w in _WORD_TOKEN_RE.findall(text)]
    foreign = sum(1 for w in words if w in _FOREIGN_WORDS)
    english = sum(1 for w in words if w in _ENGLISH_WORDS)
    return not (foreign >= 2 and foreign > english)  # noqa: PLR2004


# Hybrid boosts are on the cosine scale (0..~0.5). At most BOOST_CAP of it
# counts, mapped to at most BOOST_MAX_LOGITS cross-encoder logits: enough to
# break a near tie in favour of the named document, never enough to lift an
# irrelevant chunk over the one that answers (answer chunks lead by 4+).
BOOST_CAP = 0.20
BOOST_MAX_LOGITS = 1.5


def fused_rerank_keys(
    raw_scores: Sequence[float], boosts: Sequence[float] | None
) -> list[float]:
    """Cross-encoder logits plus a capped hybrid boost (in logit units)."""
    if not boosts or not any(boosts) or len(boosts) != len(raw_scores):
        return [float(score) for score in raw_scores]
    return [
        float(score) + BOOST_MAX_LOGITS * min(max(boost, 0.0), BOOST_CAP) / BOOST_CAP
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
        ("zero trust", r"\bzero[\s_-]{0,3}trust\b", r"\bzero trust\b"),
        ("iso 27001", r"\biso[\s_/-]{0,3}(?:iec[\s_-]{0,3})?27001\b", r"\biso 27001\b"),
        ("iso 42001", r"\biso[\s_/-]{0,3}(?:iec[\s_-]{0,3})?42001\b", r"\biso 42001\b"),
        (
            "essential eight",
            r"\bessential[\s_-]{0,3}(?:eight|8)\b|\be8\b",
            r"\be8\b|\bessential (?:eight|8)\b",
        ),
        ("cis", r"\bcis\b", r"\bcis\b"),
        (
            "security baseline",
            r"\bsecurity[\s_-]{0,3}baseline\b",
            r"\bsecurity baseline\b",
        ),
        (
            "packages",
            r"\bpackages?\b|\btiers?\b|\b(?:baseline|foundation|strengthen|frontier)"
            r"[\s_-]{0,3}(?:package|tier|plan)s?\b|\bfrontier\b",
            r"\bsecurity packages\b|^packages\b",
        ),
        (
            # Bare tier names ("How long does Baseline take?", "Fortify"): their
            # cards are on the home page and the packages page. Never overlaps
            # "Security Baseline" or "<tier> package" (other entities' spans);
            # Foundation / Strengthen only capitalised (common English words).
            "tiers",
            r"(?<!security\s)(?<!security[_-])\bbaseline\b"
            r"(?![\s_-]{0,3}(?:packages?|tiers?|plans?|assessments?)\b)"
            r"|\b(?-i:Foundation|Strengthen)\b"
            r"(?![\s_-]{0,3}(?:packages?|tiers?|plans?|benchmarks?)\b)"
            r"|\bfortify\b",
            r"^home md\b|\bsecurity packages\b|^packages\b",
        ),
        ("secure ai", r"\bsecure[\s_-]{0,3}ai\b", r"\bsecure ai\b"),
        ("responsible ai", r"\bresponsible[\s_-]{0,3}ai\b", r"\bresponsible ai\b"),
        (
            "platform",
            r"\b(?:deploy[\s_-]{0,3}iq[\s_-]{0,3})?platform\b",
            r"\bdeploy iq platform\b|^platform\b",
        ),
    )
)
# Site pages a question can scope itself to ("on the Get Started page", "in
# your privacy policy", "your vision"). Retrieval-only: never a service
# subject (entity_mentions ignores them), but the named page is seeded into
# the candidate pool and its chunks lead the context (page_scope).
_PAGE_CATALOGUE: tuple[tuple[str, re.Pattern[str], re.Pattern[str]], ...] = tuple(
    (key, re.compile(query_re, re.IGNORECASE), re.compile(doc_re, re.IGNORECASE))
    for key, query_re, doc_re in (
        (
            "page:get started",
            r"\bget[\s_-]{0,3}started\s{1,3}(?:page|section|screen)\b"
            r"|\b(?:on|in|from)\s{1,3}(?:the\s{1,3}|your\s{1,3})?get[\s_-]{0,3}started\b",
            r"^get started\b",
        ),
        (
            "page:home",
            r"\bhome[\s-]{0,3}page\b|\blanding\s{1,3}page\b|\bmain\s{1,3}page\b",
            r"^home(?: md)?\b",
        ),
        (
            "page:our vision",
            r"\b(?:our|your|its|deploy\s{0,3}iq'?s?|company'?s?)\s{1,3}vision\b"
            r"|\bvision\s{1,3}(?:page|statement)\b",
            r"^our vision\b",
        ),
        (
            "page:privacy",
            r"\bprivacy\s{1,3}(?:policy|statement|notice|page)\b",
            r"^privacy\b",
        ),
        (
            "page:terms",
            r"\bterms\s{0,3}(?:&|and)\s{0,3}conditions\b|\bterms\s{1,3}of\s{1,3}(?:use|service)\b"
            r"|\b(?:your|the)\s{1,3}terms\b|\bt\s{0,2}&\s{0,2}cs?\b|\btncs?\b",
            r"^terms\b",
        ),
        (
            "page:about us",
            r"\babout(?:[\s-]{1,3}us)?\s{1,3}page\b",
            r"^about us\b",
        ),
        (
            "page:contact us",
            r"\bcontact(?:[\s-]{1,3}us)?\s{1,3}(?:page|form)\b",
            r"^contact us\b",
        ),
        (
            "page:services",
            r"\bservices\s{1,3}page\b",
            r"^services\b",
        ),
    )
)
# Site path of each page key (a pasted page URL scopes the question too).
PAGE_ROUTES: dict[str, str] = {
    "page:get started": "/get-started",
    "page:home": "/",
    "page:our vision": "/our-vision",
    "page:privacy": "/privacy",
    "page:terms": "/terms",
    "page:about us": "/about-us",
    "page:contact us": "/contact-us",
    "page:services": "/services",
}
_ENTITY_DOC_RE = {key: doc_re for key, _query_re, doc_re in _ENTITY_CATALOGUE} | {
    key: doc_re for key, _query_re, doc_re in _PAGE_CATALOGUE
}


@dataclass(frozen=True)
class HybridSignal:
    """Rank adjustments for one retrieval candidate pool."""

    boosts: dict[Any, float] = field(default_factory=dict)  # chunk id -> boost
    entity_doc_ids: tuple[str, ...] = ()  # documents named by the query
    spread: bool = False  # "what does X cover": one chunk per section heading
    scoped_doc_ids: tuple[str, ...] = ()  # pages the question scopes itself to
    heading_hits: frozenset = frozenset()  # chunks whose heading the question names

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
                "assure",
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


_RARE_WORD_MIN_LEN = 5


def rare_word_stems(
    query: str, frequencies: Mapping[str, int], max_df: int
) -> list[str]:
    """Stems of the question's content words that 1..``max_df`` KB chunks use
    (by the word's own count, else the most-used word sharing its stem)."""
    stems: dict[str, None] = {}
    for token in _TOKEN_RE.findall((query or "").lower()):
        if len(token) < _RARE_WORD_MIN_LEN or token in _STOPWORDS:
            continue
        stem = _stem(token)
        df = frequencies.get(token) or max(
            (n for word, n in frequencies.items() if word.startswith(stem)), default=0
        )
        if 0 < df <= max_df:
            stems.setdefault(stem, None)
    return list(stems)[:_MAX_QUERY_ENTITIES]


def named_entities(query: str) -> list[str]:
    """Catalogue entities named in the query, most recently mentioned first."""
    found: list[tuple[int, str]] = []
    for key, query_re, _doc_re in _ENTITY_CATALOGUE:
        matches = list(query_re.finditer(query))
        if matches:
            found.append((matches[-1].start(), key))
    found.sort(key=lambda item: (-item[0], item[1]))
    entities = [key for _, key in found[:_MAX_QUERY_ENTITIES]]
    pages = [key for key in named_pages(query) if key not in entities]
    return (pages + entities)[: _MAX_QUERY_ENTITIES + len(pages)]


_PAGE_WORD_RE = re.compile(
    r"^(?:[\s'\N{RIGHT SINGLE QUOTATION MARK}]{0,3}[\w-]{1,20}){0,3}?\s{0,3}"
    r"(?:web\s{1,3})?page\b",
    re.IGNORECASE,
)


def named_pages(query: str) -> list[str]:
    """Pages the question scopes itself to: a site page by name ("on the Get
    Started page", "your privacy policy") or a catalogue page called a page
    ("the Zero Trust page"). Keys as in ``named_entities``."""
    if not query:
        return []
    keys = [key for key, query_re, _doc in _PAGE_CATALOGUE if query_re.search(query)]
    for key, query_re, _doc_re in _ENTITY_CATALOGUE:
        if key not in keys and any(
            _PAGE_WORD_RE.match(query[m.end() : m.end() + 40])
            for m in query_re.finditer(query)
        ):
            keys.append(key)
    return keys


def entity_mentions(text: str) -> list[tuple[int, int, str]]:
    """Every catalogue entity mention in ``text``: (start, end, key), by position."""
    found = [
        (match.start(), match.end(), key)
        for key, query_re, _doc_re in _ENTITY_CATALOGUE
        for match in query_re.finditer(text)
    ]
    found.sort()
    return found


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


def compute_hybrid_signal(  # pylint: disable=too-many-locals
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
    scoped = entity_documents(named_pages(query), names)
    section = section_number_re(query)

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
        if section is not None and section.search(
            f"{_chunk_heading(chunk)}\n{chunk.content}"
        ):
            boost += BOOST_CAP
        if boost:
            boosts[chunk.id] = boost
    return HybridSignal(
        boosts=boosts,
        entity_doc_ids=tuple(entity_docs),
        spread=intent.spread,
        scoped_doc_ids=tuple(scoped),
        heading_hits=frozenset(
            chunk.id
            for chunk, _score in candidates
            if heading_named(query, _chunk_heading(chunk))
        ),
    )


# Rank bonus (logits) for a chunk whose section heading the question names.
HEADING_HIT_LOGITS = 3.0
_HEADING_MIN_CHARS = 4
_HEADING_WORD_MIN_CHARS = 5  # one-word headings: "Frontier", never "Data"
_GENERIC_HEADINGS = frozenset(
    [
        "overview",
        "introduction",
        "faq",
        "faqs",
        "frequently",
        "asked",
        "questions",
        "definitions",
        "contact",
        "us",
        "about",
        "summary",
        "key",
        "features",
        "benefits",
        "outcomes",
        "cta",
        "services",
    ]
)


def heading_named(query: str, heading: str) -> bool:
    """The question names this section by its heading: a multi-word heading
    as a phrase, or a one-word heading as a capitalised name of its own
    ("the Frontier package", never the "Baseline" of "Security Baseline")."""
    words = _TOKEN_RE.findall(heading.lower())
    if not words or len(heading) < _HEADING_MIN_CHARS:
        return False
    if " ".join(words) in _GENERIC_HEADINGS or all(w in _STOPWORDS for w in words):
        return False
    if len(words) > 1:
        phrase = r"\W{1,3}".join(re.escape(w) for w in words)
        return re.search(rf"\b{phrase}\b", query, re.IGNORECASE) is not None
    if len(words[0]) < _HEADING_WORD_MIN_CHARS:
        return False
    for match in re.finditer(rf"\b{re.escape(words[0])}\b", query, re.IGNORECASE):
        before = query[: match.start()].split()
        prev = before[-1] if before else ""
        # Part of a longer name ("Security Baseline", "Zero Trust Foundation").
        longer = prev.lower() == "security" or (
            len(before) > 1 and prev[:1].isupper() and prev.lower() not in _STOPWORDS
        )
        if not longer:
            return True
    return False


# "section 2.2" / "clause 5": the numbered section a question names.
_SECTION_ASK_RE = re.compile(
    r"\b(?:section|clause|paragraph)\s{0,3}(\d{1,2}(?:\.\d{1,2}){0,2})\b",
    re.IGNORECASE,
)


def section_number_re(query: str) -> re.Pattern[str] | None:
    """Matches the numbered heading ("2.2 Information ...") of the section the
    question names, at a line start or after a breadcrumb; None if none named."""
    match = _SECTION_ASK_RE.search(query or "")
    if match is None:
        return None
    number = re.escape(match.group(1))
    return re.compile(
        rf"(?:^|\n|\N{{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}}\s{{0,3}}|\s{{2}})"
        rf"{number}\.?\s{{1,3}}[A-Z]"
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
        "iso 42001": f"iso{_SQL_SEP}42001",
        "essential eight": f"(e8|essential{_SQL_SEP}(eight|8))",
        "cis": "cis",
        "security baseline": f"security{_SQL_SEP}baseline",
        "packages": f"security{_SQL_SEP}packages",
        "tiers": f"(home{_SQL_SEP}md|security{_SQL_SEP}packages)",
        "secure ai": f"secure{_SQL_SEP}ai",
        "responsible ai": f"responsible{_SQL_SEP}ai",
        "platform": f"deploy{_SQL_SEP}iq{_SQL_SEP}platform",
    }.items()
} | {
    # Site pages, by file name at the start of "<source_name> <title>".
    key: f"^{body}{_SQL_EDGE_R}"
    for key, body in {
        "page:get started": f"get{_SQL_SEP}started",
        "page:home": f"home{_SQL_SEP}md",
        "page:our vision": f"our{_SQL_SEP}vision",
        "page:privacy": "privacy",
        "page:terms": "terms",
        "page:about us": f"about{_SQL_SEP}us",
        "page:contact us": f"contact{_SQL_SEP}us",
        "page:services": "services",
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
