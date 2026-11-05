"""Extended LLM-as-judge (opt-in via ``--judge``): claim-level groundedness,
answer-correctness sub-scores, helpfulness/clarity and contradictions.

One call per case. ``groundedness`` is computed in code as
supported claims / total verifiable claims. Never raises; None on failure.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.evaluation.judge import (
    _MAX_CHUNK_CHARS,
    _MAX_CHUNKS,
    _clamp_score,
    _client_for,
    _extract_json,
    _judge_deployment,
)
from app.harness.prompt_catalog import brand_values, deep_judge_prompt

logger = logging.getLogger(__name__)

CORRECTNESS_AXES = (
    "factual_accuracy",
    "numerical_accuracy",
    "dates_and_names",
    "interpretation_of_conditions",
    "correct_conclusions",
    "absence_of_contradictions",
)
HELPFULNESS_AXES = (
    "conciseness",
    "readability",
    "appropriate_detail",
    "clear_structure",
    "actionability",
    "professional_tone",
    "no_repetition",
)

# Text lives in app.harness.prompt_catalog (shared rules composed in).
DEEP_JUDGE_PROMPT = deep_judge_prompt(CORRECTNESS_AXES, HELPFULNESS_AXES)

# Claim post-filter (judge false positives), see :func:`refine_claims`.
# A claim must share at least this share of its content stems with the answer
# (else the judge invented it, e.g. from the question) ...
MIN_ANSWER_COVERAGE = 0.5
# ... and an "unsupported" claim sharing this share with ONE sentence of a
# returned source (same numbers, same negation) is a judge miss, not a fabrication.
MIN_SOURCE_COVERAGE = 0.8
MIN_SOURCE_STEMS = 2
_STEM_LEN = 6
_PLURAL_MIN_LEN = 5  # "stays" -> "stay", not "has" / "gas"
_MIN_WORD_LEN = 3
_SELF = "_self_"  # the business: its brand names, "we", "our"
_USER = "_user_"  # the customer: "you", "your"
_PRONOUNS = {
    **dict.fromkeys(("we", "our", "ours", "us"), _SELF),
    **dict.fromkeys(("you", "your", "yours"), _USER),
}
_TOKEN_RE = re.compile(rf"{_SELF}|[a-z0-9]+")
_SPLIT_RE = re.compile(r"[.!?;\n]+")
_RSQUO = chr(0x2019)
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "here",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "me",
        "might",
        "more",
        "most",
        "must",
        "my",
        "of",
        "on",
        "or",
        "over",
        "own",
        "she",
        "should",
        "so",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "too",
        "up",
        "very",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "also",
        "any",
        "all",
        "each",
        "both",
        "other",
        "some",
        "only",
        "just",
        "about",
        "via",
        "per",
    ]
)
_NEGATIONS = frozenset(
    [
        "not",
        "no",
        "never",
        "nor",
        "none",
        "without",
        "cannot",
        "cant",
        "dont",
        "doesnt",
        "didnt",
        "isnt",
        "arent",
        "wasnt",
        "werent",
        "wont",
        "wouldnt",
        "shouldnt",
        "couldnt",
    ]
)


@dataclass
class DeepJudgeScores:
    claims: list[dict] = field(default_factory=list)
    correctness: dict[str, int | None] = field(default_factory=dict)
    helpfulness: dict[str, int] = field(default_factory=dict)
    contradictions: list[str] = field(default_factory=list)
    reason: str = ""
    dropped_claims: list[str] = field(default_factory=list)

    @property
    def groundedness(self) -> float | None:
        """Supported answer claims / total verifiable answer claims."""

        if not self.claims:
            return None
        return sum(1 for c in self.claims if c.get("supported")) / len(self.claims)

    @property
    def claim_citation_need(self) -> int:
        return sum(1 for c in self.claims if c.get("needs_citation"))

    def mean_axis(self, group: dict) -> float | None:
        vals = [v for v in group.values() if v is not None]
        return sum(vals) / len(vals) if vals else None

    def as_dict(self) -> dict:
        return {
            "groundedness": self.groundedness,
            "claims": self.claims,
            "correctness": self.correctness,
            "correctness_mean": self.mean_axis(self.correctness),
            "helpfulness": self.helpfulness,
            "helpfulness_mean": self.mean_axis(self.helpfulness),
            "contradictions": self.contradictions,
            "reason": self.reason,
            "dropped_claims": self.dropped_claims,
        }


def build_deep_prompt(
    question: str, chunks: list[str], answer: str, key_facts: list[str] | None
) -> str:
    context = "\n\n".join(
        f"[{i}] {c[:_MAX_CHUNK_CHARS]}" for i, c in enumerate(chunks[:_MAX_CHUNKS], 1)
    )
    facts = ""
    if key_facts:
        facts = "EXPECTED FACTS:\n" + "\n".join(f"- {f}" for f in key_facts) + "\n\n"
    return f"QUESTION:\n{question}\n\nCONTEXT:\n{context}\n\n{facts}ANSWER:\n{answer}\n"


def parse_deep_output(text: str) -> DeepJudgeScores | None:
    data = _extract_json(text)
    if data is None:
        return None
    try:
        claims = [
            {
                "claim": str(c.get("claim", "")),
                "supported": bool(c.get("supported")),
                "needs_citation": bool(c.get("needs_citation", True)),
            }
            for c in (data.get("claims") or [])
            if isinstance(c, dict)
        ]
        corr = {
            k: (
                None
                if (data.get("correctness") or {}).get(k) is None
                else _clamp_score(data["correctness"][k])
            )
            for k in CORRECTNESS_AXES
        }
        help_ = {
            k: _clamp_score(data["helpfulness"][k])
            for k in HELPFULNESS_AXES
            if (data.get("helpfulness") or {}).get(k) is not None
        }
        return DeepJudgeScores(
            claims=claims,
            correctness=corr,
            helpfulness=help_,
            contradictions=[str(c) for c in (data.get("contradictions") or [])],
            reason=str(data.get("reason", "")),
        )
    except (TypeError, ValueError):
        return None


def brand_phrases(names: list[str]) -> tuple[str, ...]:
    """Lower-case brand spellings: as set, and camel-split ("DeployIQ", "Deploy IQ")."""
    out: set[str] = set()
    for name in names:
        out.add(" ".join(name.lower().split()))
        out.add(" ".join(_CAMEL_RE.sub(" ", name).lower().split()))
    return tuple(sorted((p for p in out if p), key=len, reverse=True))


def _tokens(text: str, brands: tuple[str, ...] = ()) -> list[str]:
    text = (text or "").lower().replace(_RSQUO, "").replace("'", "")
    text = " ".join(text.split())
    for phrase in brands:
        text = text.replace(phrase.replace("'", ""), f" {_SELF} ")
    return [_PRONOUNS.get(t, t) for t in _TOKEN_RE.findall(text)]


def _stem(word: str) -> str:
    if word.isdigit() or word in {_SELF, _USER}:
        return word
    if len(word) >= _PLURAL_MIN_LEN and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return word[:_STEM_LEN]


def content_stems(text: str, brands: tuple[str, ...] = ()) -> set[str]:
    """Stems of the content words of ``text``; the brand and "we" are one word."""
    return {
        _stem(t)
        for t in _tokens(text, brands)
        if t not in _STOPWORDS and (len(t) >= _MIN_WORD_LEN or t.isdigit())
    }


def _numbers(text: str) -> set[str]:
    return {t for t in _tokens(text) if any(ch.isdigit() for ch in t)}


def _negated(text: str) -> bool:
    return any(t in _NEGATIONS for t in _tokens(text))


def coverage(stems: set[str], text_stems: set[str]) -> float:
    return len(stems & text_stems) / len(stems) if stems else 1.0


def in_sources(claim: str, chunks: list[str], brands: tuple[str, ...] = ()) -> bool:
    """``claim`` is (near-)verbatim in ONE sentence of a returned source.

    Most of its content stems are in that sentence, every number in it is
    there, the sentence is negated exactly when the claim is, and it names
    the same actor (the brand / "we" and "you" are words of their own).
    """
    stems = content_stems(claim, brands)
    if len(stems) < MIN_SOURCE_STEMS:
        return False
    numbers, negated = _numbers(claim), _negated(claim)
    actors = stems & {_SELF, _USER}
    return any(
        coverage(stems, sent) >= MIN_SOURCE_COVERAGE
        and actors <= sent
        and numbers <= _numbers(sentence)
        and _negated(sentence) == negated
        for chunk in chunks
        for sentence in _SPLIT_RE.split(chunk or "")
        if (sent := content_stems(sentence, brands))
    )


def in_answer(
    claim: str, answer: str, question: str = "", brands: tuple[str, ...] = ()
) -> bool:
    """The answer actually makes ``claim``.

    Judged on what the claim adds to the question (a terse answer, "- Pilot",
    is restated with the question's words); a claim that only echoes the
    question is judged on all its words.
    """
    stems = content_stems(claim, brands)
    new = stems - content_stems(question, brands)
    if not stems:
        return True
    return coverage(new or stems, content_stems(answer, brands)) >= MIN_ANSWER_COVERAGE


def refine_claims(
    scores: DeepJudgeScores,
    answer: str,
    chunks: list[str],
    question: str = "",
    brands: tuple[str, ...] = (),
) -> DeepJudgeScores:
    """Remove two judge false positives from ``scores`` (in place).

    * a claim the answer does not make (invented from the question, or a
      fixed line re-read as a claim) is dropped (``dropped_claims``);
    * an "unsupported" claim that is near-verbatim in a returned source is
      marked supported (``supported_by: "source_overlap"``).
    """
    kept: list[dict] = []
    for claim in scores.claims:
        text = claim.get("claim", "")
        if not in_answer(text, answer, question, brands):
            scores.dropped_claims.append(text)
            continue
        if not claim.get("supported") and in_sources(text, chunks, brands):
            claim_out = {**claim, "supported": True, "supported_by": "source_overlap"}
            kept.append(claim_out)
            continue
        kept.append(claim)
    scores.claims = kept
    return scores


def _brands() -> tuple[str, ...]:
    """Brand spellings from the BRAND_* settings (empty if settings fail)."""
    try:
        return brand_phrases(list(brand_values().values()))
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught  # settings optional
        return ()


async def _deep(question, chunks, answer, key_facts) -> DeepJudgeScores | None:
    settings = get_settings()
    deployment = _judge_deployment()
    client = _client_for(deployment)
    common = {
        "model": deployment,
        "messages": [
            {"role": "system", "content": DEEP_JUDGE_PROMPT},
            {
                "role": "user",
                "content": build_deep_prompt(question, chunks, answer, key_facts),
            },
        ],
        "reasoning_effort": settings.llm_reasoning_effort,
    }
    for json_mode in (True, False):
        try:
            kwargs = dict(common)
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = await client.chat.completions.create(**kwargs)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # retry w/o JSON mode
            logger.warning(
                "deep_judge_call_failed json_mode=%s error=%s", json_mode, exc
            )
            continue
        scores = parse_deep_output(
            (resp.choices[0].message.content if resp.choices else "") or ""
        )
        if scores is not None:
            return refine_claims(scores, answer, chunks, question, _brands())
    return None


async def deep_judge(
    question, chunks, answer, key_facts=None
) -> DeepJudgeScores | None:
    try:
        return await _deep(question, chunks, answer, key_facts)
    except Exception as exc:  # pylint: disable=broad-exception-caught  # never raises
        logger.warning("deep_judge_failed error=%s", exc)
        return None
