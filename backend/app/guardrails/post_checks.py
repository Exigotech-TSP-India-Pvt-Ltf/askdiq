"""Guardrail checks run AFTER generation, before the answer is returned."""

import re
from collections.abc import Iterable

import numpy as np

from app.core.config import get_settings
from app.retrieval.embeddings import current_embedding_stage, embed_texts
from app.schemas.query import SourceChunk

# Every regular expression below runs on model/user text: quantifiers are
# bounded, there are no nested unbounded groups and inputs are length-capped,
# so matching is linear-time (CodeQL ReDoS-safe).
_MAX_ANALYSED_CHARS = 4000

# ---------------------------------------------------------------------------
# Refusal detection
#
# A short answer that declines (the corpus lacks the information) is grounded
# by definition: declining beats hallucinating. The match is deliberately
# narrow: it needs a real negation, the answer must be short and the phrase
# must sit in the first two sentences, so "DeployIQ does include X" is never a
# refusal.
# ---------------------------------------------------------------------------
_NEGATIVE_ANSWER_RE = re.compile(
    r"\b(?:do(?:es)?\s+not|don'?t|doesn'?t|cannot|can'?t|can\s+not|unable\s+to)"
    r"\s+(?:currently\s+|specifically\s+)?"
    r"(?:have|provide|contain|specify|include|mention|cover|list|state|publish|confirm)\b|"
    r"\bno\s+(?:specific\s+)?information\b|"
    r"\bnot\s+(?:yet\s+)?(?:provided|available|specified|claimed|listed|covered"
    r"|mentioned|stated)\b",
    re.IGNORECASE,
)
# Friendlier decline wordings used by the answer style.
_DECLINE_PHRASE_RE = re.compile(
    r"\boutside\s+(?:of\s+)?what\s+i\s+can\s+help\s+with\b|"
    r"\bnot\s+(?:completely\s+|entirely\s+)?(?:certain|sure)\b|"
    r"\b(?:don'?t|do\s+not)\s+have\s+(?:any\s+)?(?:details|information|specifics)\b",
    re.IGNORECASE,
)
_HANDOFF_RE = re.compile(
    r"\b(?:talk|speak|reach\s+out|get\s+in\s+touch)\s+(?:to|with)\s+"
    r"(?:one\s+of\s+)?(?:our|a|the)\s+(?:\w{3,15}\s+)?"
    r"(?:specialists?|team|experts?|consultants?)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_BULLET_PREFIX_RE = re.compile(r"^\s{0,8}(?:[-*•]|\d{1,2}[.)])\s+")
_REFUSAL_MAX_WORDS = 60
_HANDOFF_MAX_WORDS = 25

# Literal fact-anchor check: embeddings capture topic similarity, not whether
# a specific number was actually stated, so a confidently invented $ amount
# or percentage can still pass the semantic check below. Scoped to $/%/currency
# words (not bare numbers) to avoid false positives on word/digit mismatches
# like source "three pillars" vs. answer "3 pillars".
_AMOUNT = r"\d[\d,]{0,15}(?:\.\d{1,4})?"
_CODES = r"(?:AUD|USD|NZD|GBP|EUR)"
_CURRENCY_RE = re.compile(rf"\$\s?{_AMOUNT}")
_PERCENT_RE = re.compile(rf"{_AMOUNT}\s?%")
_CURRENCY_WORD_RE = re.compile(
    rf"\b{_CODES}\s?{_AMOUNT}|(?<![$\d,.]){_AMOUNT}\s?{_CODES}\b|"
    rf"(?<![$\d,.]){_AMOUNT}\s?(?:per|/)\s?(?:month|user|year|seat|device|endpoint)\b",
    re.IGNORECASE,
)


def _leading_sentences(text: str, count: int) -> str:
    parts = _SENTENCE_SPLIT_RE.split(text[:_MAX_ANALYSED_CHARS].strip(), maxsplit=count)
    return " ".join(parts[:count])


def is_refusal(answer: str) -> bool:
    """True when `answer` is a short decline / "not covered" reply with no facts."""
    text = answer.strip()
    words = len(text.split())
    if not text or words > _REFUSAL_MAX_WORDS or _CURRENCY_RE.search(text):
        return False
    lead = _leading_sentences(text, 2)
    if _NEGATIVE_ANSWER_RE.search(lead) or _DECLINE_PHRASE_RE.search(lead):
        return True
    return (
        words <= _HANDOFF_MAX_WORDS
        and not any(ch.isdigit() for ch in text)
        and _HANDOFF_RE.search(lead) is not None
    )


def _normalize_figure(figure: str) -> str:
    return re.sub(r"[\s,]", "", figure).lower()


def _figures(text: str, limit: int) -> set[str]:
    text = text[:limit]
    found = (
        set(_CURRENCY_RE.findall(text))
        | set(_PERCENT_RE.findall(text))
        | set(_CURRENCY_WORD_RE.findall(text))
    )
    return {_normalize_figure(f) for f in found}


def _unsupported_figures(answer: str, source_text: str) -> list[str]:
    """Return $/%/currency-word figures in `answer` absent from the sources."""
    answer = answer[:_MAX_ANALYSED_CHARS]
    answer_figures = (
        set(_CURRENCY_RE.findall(answer))
        | set(_PERCENT_RE.findall(answer))
        | set(_CURRENCY_WORD_RE.findall(answer))
    )
    source_figures = _figures(source_text, _MAX_ANALYSED_CHARS * 4)
    return sorted(
        f for f in answer_figures if _normalize_figure(f) not in source_figures
    )


# ---------------------------------------------------------------------------
# Commercial / assurance claim guard
#
# Each rule is (answer pattern, optional support pattern). A hit in the answer
# is supported only if the sources contain the support pattern (default: the
# very same matched text).
# ---------------------------------------------------------------------------
_UNITS = r"(?:minutes?|hours?|days?|weeks?)"
_SAFE = r"\bsafe(?:ly)?\b"
_GUARANTEE = r"\bguarantee(?:d|s|ing)?\b"
_CERT = r"\bcertif(?:ied|ication|ications)\b"
_COMPLIANT = r"\bcompliant\b|\bcompliance[- ]ready\b"
_ROI = r"\broi\b|\breturn\s+on\s+investment\b"
_SLA = r"\bslas?\b|\bservice\s+level\s+agreements?\b"
_DISCOUNT = r"\bdiscounts?\b|\bpromo(?:tions?|tional)?\b"
_FREE = r"\bfree\b|\bno\s+(?:cost|charge)\b"
_CLAIM_RULES: tuple[tuple[str, str | None], ...] = (
    (
        r"\b(?:(?:is|are|be|remains?|completely|totally|fully|perfectly|entirely)"
        r"\s+safe|safe\s+to\s+\w{3,12}|safely)\b",
        _SAFE,
    ),
    (_GUARANTEE, _GUARANTEE),
    (_CERT, _CERT),
    (_COMPLIANT, _COMPLIANT),
    (r"\brisk[- ]free\b", None),
    (r"\b100\s?percent\b", None),
    (
        r"\b24\s?/\s?7\b|\b24\s?x\s?7\b|\bround[- ]the[- ]clock\b|\b24[ -]?hours?\b",
        None,
    ),
    (_ROI, _ROI),
    (rf"\bwithin\s+(?:the\s+(?:first|next)\s+)?\d{{1,3}}\s{{0,3}}{_UNITS}\b", None),
    (rf"\b(?:in\s+)?{_UNITS}\s+(?:not|instead\s+of|rather\s+than)\s+{_UNITS}\b", None),
    (_SLA, _SLA),
    (r"\buptime\b", r"\buptime\b"),
    (rf"{_DISCOUNT}|\b\d{{1,2}}\s?%\s?off\b", rf"{_DISCOUNT}|\boff\b"),
    (
        r"\bfree\b(?!\s{1,3}(?:of|from|text|form)\b)|\bat\s+no\s+(?:cost|charge)\b",
        _FREE,
    ),
)
_COMPILED_CLAIM_RULES = tuple(
    (re.compile(p, re.IGNORECASE), re.compile(s, re.IGNORECASE) if s else None)
    for p, s in _CLAIM_RULES
)
_MAX_CLAIM_HITS = 50


def _normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def unsupported_claims(answer: str, source_text: str) -> list[str]:
    """Commercial/assurance claims in `answer` that the sources never make."""
    answer = answer[:_MAX_ANALYSED_CHARS]
    source = _normalize_text(source_text[: _MAX_ANALYSED_CHARS * 4])
    unsupported: set[str] = set()
    for pattern, support in _COMPILED_CLAIM_RULES:
        for hit, match in enumerate(pattern.finditer(answer)):
            if hit >= _MAX_CLAIM_HITS:
                break
            phrase = _normalize_text(match.group(0))
            supported = (
                support.search(source) is not None
                if support is not None
                else phrase in source
            )
            if not supported:
                unsupported.add(phrase)
    return sorted(unsupported)


# ---------------------------------------------------------------------------
# Query relevance (nonsense / off-topic questions that still retrieve chunks)
# ---------------------------------------------------------------------------
_WORD_RE = re.compile(r"[a-z]{4,}")
_ANY_WORD_RE = re.compile(r"[a-z]{3,}")
_STOPWORD_TEXT = (
    "about above after again also another because been before being below "
    "between both can't cannot could does doing done down during each either "
    "else ever every from further have having here hers herself himself how's "
    "however into itself just know like made make many more most much must "
    "need only other over said same should since some such take tell than "
    "that their theirs them then there these they this those through today "
    "topic under until upon very want were what what's when where which while "
    "whom whose will with within without would yours please explain describe "
    "details information give show tell help"
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())
_STEM_SUFFIXES = ("ations", "ation", "ings", "ing", "ies", "ied", "es", "ed", "ly", "s")
_MIN_STEM_LEN = 4


def _stem(word: str) -> str:
    for suffix in _STEM_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM_LEN:
            return word[: -len(suffix)]
    return word


def query_content_words(query: str) -> list[str]:
    """Non-stopword words of >= 4 letters (lowercase, de-duplicated, ordered)."""
    words = _WORD_RE.findall(query[:500].lower())
    return list(dict.fromkeys(w for w in words if w not in _STOPWORDS))


def query_has_overlap(query: str, chunks: Iterable[str]) -> bool:
    """Whether any content word (or stem) of `query` occurs in any chunk.

    Queries with no content words cannot be judged and count as overlapping.
    """
    words = query_content_words(query)
    if not words:
        return True
    tokens: set[str] = set()
    for chunk in chunks:
        tokens.update(_ANY_WORD_RE.findall(chunk[: _MAX_ANALYSED_CHARS * 2].lower()))
    stems = {_stem(t) for t in tokens}
    return any(w in tokens or _stem(w) in stems for w in words)


def is_off_topic_by_overlap(
    query: str,
    chunks: Iterable[str],
    max_score: float,
    max_score_limit: float | None = None,
) -> bool:
    """Weak retrieval AND no query word in any chunk -> treat as off-topic."""
    if max_score_limit is None:
        max_score_limit = get_settings().offtopic_overlap_max_score
    return max_score < max_score_limit and not query_has_overlap(query, chunks)


# ---------------------------------------------------------------------------
# Claim-level semantic support
# ---------------------------------------------------------------------------
_MAX_CLAIMS = 12
_MIN_CLAIM_WORDS = 3


def split_claims(answer: str) -> list[str]:
    """Sentences / bullets of `answer` (>= 3 words each), capped at 12."""
    claims: list[str] = []
    for part in _SENTENCE_SPLIT_RE.split(answer[:_MAX_ANALYSED_CHARS]):
        text = _BULLET_PREFIX_RE.sub("", part).strip()
        if len(text.split()) >= _MIN_CLAIM_WORDS:
            claims.append(text)
        if len(claims) >= _MAX_CLAIMS:
            break
    return claims


def supported_claim_ratio(
    claim_embeddings: list[list[float]],
    source_embeddings: list[list[float]],
    threshold: float,
) -> float:
    """Share of claims whose best-matching chunk reaches `threshold`."""
    if not claim_embeddings or not source_embeddings:
        return 0.0
    sources = np.asarray(source_embeddings, dtype=np.float64)
    supported = 0
    for claim in claim_embeddings:
        best = float(_cosine_similarities(claim, sources.tolist()).max())
        supported += best >= threshold
    return supported / len(claim_embeddings)


def _cosine_similarities(query: list[float], matrix: list[list[float]]) -> np.ndarray:
    """Cosine similarity of `query` against each row of `matrix` (0.0 for zero vectors)."""
    q = np.asarray(query, dtype=np.float64)
    m = np.asarray(matrix, dtype=np.float64).reshape(len(matrix), -1)
    denom = np.linalg.norm(m, axis=1) * np.linalg.norm(q)
    return np.divide(m @ q, denom, out=np.zeros(len(m)), where=denom != 0)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    return float(_cosine_similarities(a, [b])[0])


def check_has_sources(
    sources: list[SourceChunk],
    min_score: float,
) -> tuple[bool, str | None]:
    """Ensure retrieval returned sufficiently relevant sources."""

    if not sources:
        return False, "no_retrieved_sources"

    if max(s.score for s in sources) < min_score:
        return False, "low_retrieval_confidence"

    return True, None


def _literal_claim_flag(answer: str, sources: list[SourceChunk]) -> str | None:
    """Flag for an invented figure or unsupported commercial claim, else None."""
    source_text = " ".join(source.content for source in sources)
    if _unsupported_figures(answer, source_text):
        return "unsupported_numeric_claim"
    if unsupported_claims(answer, source_text):
        return "unsupported_claim"
    return None


async def _embed_for_check(
    answer: str, claims: list[str], sources: list[SourceChunk]
) -> tuple[list[float], list[list[float]], list[list[float]]]:
    """Embed answer + claims in ONE call; embed only sources lacking a vector."""
    stage_token = current_embedding_stage.set("guardrail")
    try:
        embedded = await embed_texts([answer, *claims])
        source_embeddings: list[list[float] | None] = [s.embedding for s in sources]
        missing = [i for i, e in enumerate(source_embeddings) if e is None]
        if missing:
            fresh = await embed_texts([sources[i].content for i in missing])
            for index, embedding in zip(missing, fresh, strict=False):
                source_embeddings[index] = embedding
    finally:
        current_embedding_stage.reset(stage_token)
    return embedded[0], embedded[1:], source_embeddings  # type: ignore[return-value]


async def check_groundedness(
    answer: str,
    sources: list[SourceChunk],
    threshold: float,
    min_supported_ratio: float | None = None,
) -> tuple[bool, float, str | None]:
    """Estimate whether the generated answer is supported by the sources.

    Order: refusal (grounded by definition) -> literal $/% figures ->
    commercial/assurance claims -> semantic check. The semantic check embeds
    the answer (and, for multi-claim answers, each sentence/bullet in the same
    batched call) and compares against every chunk: the best chunk must reach
    `threshold` overall, and for multi-claim answers the share of claims whose
    best chunk reaches it must be >= `min_supported_ratio`
    (``settings.groundedness_min_supported_ratio`` by default).

    Source embeddings are reused from ingestion (via SourceChunk.embedding)
    rather than re-embedded here; only sources missing a precomputed embedding
    (e.g. hand-built in a test) fall back to a fresh embed call.
    """

    if not answer.strip():
        return False, 0.0, "empty_answer"

    if is_refusal(answer):
        return True, 1.0, None

    literal_flag = _literal_claim_flag(answer, sources)
    if literal_flag:
        return False, 0.0, literal_flag

    if min_supported_ratio is None:
        min_supported_ratio = get_settings().groundedness_min_supported_ratio

    claims = split_claims(answer)
    check_claims = len(claims) > 1

    try:
        answer_embedding, claim_embeddings, source_embeddings = await _embed_for_check(
            answer, claims if check_claims else [], sources
        )
    except Exception:
        return False, 0.0, "groundedness_check_failed"

    similarities = (
        _cosine_similarities(answer_embedding, source_embeddings)
        if source_embeddings
        else np.zeros(0)
    )

    confidence = float(similarities.max()) if similarities.size else 0.0
    supported = confidence >= threshold and (
        not check_claims
        or supported_claim_ratio(claim_embeddings, source_embeddings, threshold)
        >= min_supported_ratio
    )

    return supported, confidence, None if supported else "possible_hallucination"


async def run_post_checks(
    answer: str,
    sources: list[SourceChunk],
    groundedness_threshold: float,
    min_retrieval_score: float | None = None,
) -> tuple[bool, float, list[str]]:
    """Return (grounded, confidence, flags).

    ``min_retrieval_score`` defaults to ``settings.min_retrieval_score``: the
    same floor the retrieval gate uses, so an answer that passed retrieval is
    never rejected afterwards by a stricter hidden threshold.
    """

    flags: list[str] = []
    if min_retrieval_score is None:
        min_retrieval_score = get_settings().min_retrieval_score

    sources_ok, flag = check_has_sources(
        sources,
        min_retrieval_score,
    )

    if not sources_ok:
        flags.append(flag)
        return False, 0.0, flags

    grounded, confidence, flag = await check_groundedness(
        answer,
        sources,
        groundedness_threshold,
    )

    if not grounded:
        flags.append(flag)

    return grounded, confidence, flags
