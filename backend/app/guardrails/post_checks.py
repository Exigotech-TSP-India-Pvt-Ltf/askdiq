"""Guardrail checks run AFTER generation, before the answer is returned."""

import itertools
import re
from collections.abc import Callable, Iterable
from typing import NamedTuple

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
# narrow: the answer must be short and EVERY sentence must decline or hand off
# (no positive fact, figure, claim word or citation), so "DeployIQ does include
# X" or "X is covered. Talk to a specialist." is never a refusal.
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
_FILLER_MAX_WORDS = 2
# A decline never carries figures, citations or assurance claims: any of these
# means the answer asserts something and must go through the normal checks.
_ASSERTION_RE = re.compile(
    r"[\d%$]|\[\s{0,3}\d{1,3}\s{0,3}\]|"
    r"\bguarantee|\buptime\b|\bslas?\b|\bservice\s+level\b|\bcertif|"
    r"\bcompliant\b|\broi\b|\brisk[- ]free\b|\bdiscounts?\b|\b24\s?/\s?7\b",
    re.IGNORECASE,
)

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


def _sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT_RE.split(text[:_MAX_ANALYSED_CHARS].strip())
    cleaned = (_BULLET_PREFIX_RE.sub("", part).strip() for part in parts)
    return [part for part in cleaned if part]


def is_refusal(answer: str) -> bool:
    """True when `answer` is, in its entirety, a short decline / handoff reply.

    Every sentence must itself decline ("not covered") or hand off to a
    specialist; any positive factual sentence, figure, citation marker or
    assurance claim makes it a real answer.
    """
    text = answer.strip()
    if not text or len(text.split()) > _REFUSAL_MAX_WORDS or _ASSERTION_RE.search(text):
        return False
    declines = 0
    for sentence in _sentences(text):
        if _NEGATIVE_ANSWER_RE.search(sentence) or _DECLINE_PHRASE_RE.search(sentence):
            declines += 1
        elif not (
            _HANDOFF_RE.search(sentence) or len(sentence.split()) <= _FILLER_MAX_WORDS
        ):
            return False
    return declines > 0 or (
        len(text.split()) <= _HANDOFF_MAX_WORDS and _HANDOFF_RE.search(text) is not None
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
_COMPLIANT_SUPPORT = r"\bcompliant\b|\bcompliance\b"
_ROI = r"\broi\b|\breturn\s+on\s+investment\b"
_SLA = r"\bslas?\b|\bservice\s+level\s+agreements?\b"
_DISCOUNT = r"\bdiscounts?\b|\bpromo(?:tions?|tional)?\b"
_SAFELY_SUPPORT = rf"{_SAFE}|\broll(?:s|ed|ing)?[- ]?back(?:s)?\b"
# "free" as a price claim: not "feel free to", "free-form", "agent-free",
# "free of / from / text / form".
_FREE_TERM = r"(?<!feel )(?<![-\w])free\b(?!-|\s{1,3}(?:of|from|text|form)\b)"
_FREE_ANSWER = rf"{_FREE_TERM}|\bat\s+no\s+(?:cost|charge)\b"
_FREE_SOURCE = rf"{_FREE_TERM}|\bno\s+(?:cost|charge)\b"
_WITHIN = (
    r"\bwithin\s+(?:the\s+(?:first|next)\s+)?(?P<n>\d{1,3})\s{0,3}"
    r"(?P<unit>minutes?|hours?|days?|weeks?)\b"
)
# Words too generic to tie a "free" claim in the answer to a source sentence.
_FREE_GENERIC = frozenset(
    {
        "plan", "plans", "tier", "free", "cost", "charge", "package", "product",
        "service", "version", "edition", "offer", "offers", "available", "include",
        "includes", "currently",
    }
)  # fmt: skip
_SHORT_STOPWORDS = frozenset(
    {"the", "and", "for", "are", "our", "you", "can", "has", "its", "all", "any"}
    | {"but", "not", "who", "how", "why", "was"}
)
_NEGATORS = frozenset({"not", "no", "never", "without", "non", "cannot", "nor"})
_NEGATION_WINDOW_CHARS = 40
_NEGATION_WINDOW_WORDS = 3
_CLAUSE_END_RE = re.compile(r"[.!?;:\n]")
_NEGATION_TOKEN_RE = re.compile(r"[a-z']+")
_ANSWER_SENTENCE_RE = re.compile(r"[^.!?\n]+")
_MAX_CLAIM_HITS = 50


class _Sources(NamedTuple):
    """Normalized (lowercase, single-spaced) source text and its sentences."""

    text: str
    sentences: tuple[str, ...]


_Support = Callable[[re.Match[str], str, _Sources], bool]


def _normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def _negated(text: str, start: int) -> bool:
    """A negator within ~3 words before `start` (same clause) voids a match."""
    window = text[max(0, start - _NEGATION_WINDOW_CHARS) : start]
    clause = _CLAUSE_END_RE.split(window)[-1]
    words = _NEGATION_TOKEN_RE.findall(clause)[-_NEGATION_WINDOW_WORDS:]
    return any(w in _NEGATORS or w.endswith("n't") for w in words)


def _positive_hit(pattern: re.Pattern[str], text: str) -> bool:
    hits = itertools.islice(pattern.finditer(text), _MAX_CLAIM_HITS)
    return any(not _negated(text, hit.start()) for hit in hits)


def _regex_support(pattern: str) -> _Support:
    compiled = re.compile(pattern, re.IGNORECASE)
    return lambda _m, _answer, sources: _positive_hit(compiled, sources.text)


def _phrase_support(match: re.Match[str], _answer: str, sources: _Sources) -> bool:
    return _normalize_text(match.group(0)) in sources.text


def _within_support(match: re.Match[str], _answer: str, sources: _Sources) -> bool:
    """`in 48 hours` supports `within 48 hours` (and vice versa)."""
    unit = match.group("unit").lower().rstrip("s")
    pattern = re.compile(
        rf"\b(?:within|in)\s+(?:the\s+(?:first|next)\s+)?{match.group('n')}"
        rf"\s{{0,3}}{unit}s?\b"
    )
    return pattern.search(sources.text) is not None


def _topic_stems(text: str) -> set[str]:
    words = _ANY_WORD_RE.findall(text.lower())
    skip = _STOPWORDS | _FREE_GENERIC | _SHORT_STOPWORDS
    return {_stem(w) for w in words if w not in skip}


def _sentence_at(text: str, position: int) -> str:
    for part in _ANSWER_SENTENCE_RE.finditer(text):
        if part.start() <= position < part.end():
            return part.group(0)
    return ""


_FREE_SOURCE_RE = re.compile(_FREE_SOURCE, re.IGNORECASE)


def _free_support(match: re.Match[str], answer: str, sources: _Sources) -> bool:
    """A source sentence must say "free" about the same plan/service."""
    subject = _topic_stems(_sentence_at(answer, match.start()))
    for sentence in sources.sentences:
        if _positive_hit(_FREE_SOURCE_RE, sentence) and (
            not subject or subject & _topic_stems(sentence)
        ):
            return True
    return False


# (answer pattern, support). Support is a callable, or None = same phrase.
_CLAIM_RULES: tuple[tuple[str, _Support | None], ...] = (
    (
        r"\b(?:(?:is|are|be|remains?|completely|totally|fully|perfectly|entirely)"
        r"\s+safe|safe\s+to\s+\w{3,12})\b",
        _regex_support(_SAFE),
    ),
    (r"\bsafely\b", _regex_support(_SAFELY_SUPPORT)),
    (_GUARANTEE, _regex_support(_GUARANTEE)),
    (_CERT, _regex_support(_CERT)),
    (_COMPLIANT, _regex_support(_COMPLIANT_SUPPORT)),
    (r"\brisk[- ]free\b", None),
    (r"\b100\s?percent\b", None),
    (
        r"\b24\s?/\s?7\b|\b24\s?x\s?7\b|\bround[- ]the[- ]clock\b|\b24[ -]?hours?\b",
        None,
    ),
    (_ROI, _regex_support(_ROI)),
    (_WITHIN, _within_support),
    (rf"\b(?:in\s+)?{_UNITS}\s+(?:not|instead\s+of|rather\s+than)\s+{_UNITS}\b", None),
    (_SLA, _regex_support(_SLA)),
    (r"\buptime\b", _regex_support(r"\buptime\b")),
    (
        rf"{_DISCOUNT}|\b\d{{1,2}}\s?%\s?off\b",
        _regex_support(rf"{_DISCOUNT}|\boff\b"),
    ),
    (_FREE_ANSWER, _free_support),
)
_COMPILED_CLAIM_RULES = tuple(
    (re.compile(pattern, re.IGNORECASE), support or _phrase_support)
    for pattern, support in _CLAIM_RULES
)


def _prepare_sources(source_text: str) -> _Sources:
    raw = source_text[: _MAX_ANALYSED_CHARS * 4]
    sentences = (_normalize_text(p) for p in _SENTENCE_SPLIT_RE.split(raw))
    return _Sources(_normalize_text(raw), tuple(s for s in sentences if s))


def unsupported_claims(answer: str, source_text: str) -> list[str]:
    """Commercial/assurance claims in `answer` that the sources never make."""
    answer = " ".join(answer[:_MAX_ANALYSED_CHARS].split())
    sources = _prepare_sources(source_text)
    unsupported: set[str] = set()
    for pattern, support in _COMPILED_CLAIM_RULES:
        for match in itertools.islice(pattern.finditer(answer), _MAX_CLAIM_HITS):
            if not support(match, answer, sources):
                unsupported.add(_normalize_text(match.group(0)))
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


# Pricing vocabulary is interchangeable for relevance ("how much does it cost?"
# vs a chunk about plans / pricing / fees).
_PRICE_WORDS = frozenset(
    {"cost", "costs", "costing", "price", "prices", "pricing", "priced", "plan", "plans", "fee", "fees"}
)  # fmt: skip
_PRICE_STEM = "price"


def _canon(word: str) -> str:
    return _PRICE_STEM if word in _PRICE_WORDS else _stem(word)


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
    stems = {_canon(t) for t in tokens}
    return any(w in tokens or _canon(w) in stems for w in words)


def is_off_topic_by_overlap(
    query: str,
    chunks: Iterable[str],
    max_score: float,
    max_score_limit: float | None = None,
    *,
    follow_up: bool = False,
) -> bool:
    """Weak retrieval AND no query word in any chunk -> treat as off-topic.

    Follow-ups ("and for the Pro plan?") lean on the conversation, so their own
    words say little about relevance: the rule never applies to them.
    """
    if follow_up:
        return False
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

    Order: literal $/% figures -> commercial/assurance claims -> refusal
    (a pure decline is grounded by definition) -> semantic check. The semantic check embeds
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

    literal_flag = _literal_claim_flag(answer, sources)
    if literal_flag:
        return False, 0.0, literal_flag

    if is_refusal(answer):
        return True, 1.0, None

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
