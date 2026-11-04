"""Nearest-neighbour intent classifier over labelled example utterances.

The visitor's (normalised) message is embedded with the app's own embedding
function (the same text retrieval embeds, so the embedding cache serves the
second call) and compared with the example utterances in
``app.guardrails.intent_examples``. Example vectors are embedded once per
process and embedding model, in the background, and cached. The nearest
example's label is the intent.

A canned reply is allowed only when the result is ``pure``: the nearest label
is not QUESTION, it wins clearly (score, and margin over the nearest QUESTION
example), the message is short, and it names nothing from the knowledge-base
catalogue (DeployIQ, Zero Trust, Essential Eight, CIS, ISO, packages,
Microsoft 365, MFA, data, ...). Anything else is answered from the knowledge
base, so a canned reply never hijacks a real product question. BUYING is
never canned here; it is reported (``current_intent``) for the later stages.

The classifier fails open: while the examples are still being embedded, or
when embeddings are unavailable, the message is a QUESTION (retrieval
decides).
"""

from __future__ import annotations

import asyncio
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass

import numpy as np

from app.core.logging_config import get_logger
from app.guardrails.intent_examples import (
    ASSURANCE,
    EXAMPLES,
    INCIDENT,
    is_follow_up_fragment,
    names_catalogue_term,
    names_contact_detail,
)
from app.harness.decision import Intent, IntentResult
from app.model_settings.service import current_model_settings
from app.retrieval import embeddings

logger = get_logger(__name__)

# Calibrated on text-embedding-3-small with real calls (phase-3 live matrix).
MIN_SCORE = 0.50  # nearest non-question example must be at least this close
MIN_MARGIN = 0.04  # ... and this much closer than the nearest QUESTION example
BUYING_MIN_SCORE = 0.45  # buying wording plus this close to a buying example
MAX_PURE_WORDS = 16  # a longer message is never "only" small talk
MAX_CLASSIFY_CHARS = 300  # longer input is a QUESTION without an embed call
QUERY_TIMEOUT_S = 3.0
WARMUP_WAIT_S = 2.0  # a request waits at most this long for the first build
WARMUP_TIMEOUT_S = 60.0
FAILURE_BACKOFF_S = 30.0
_STAGE = "guardrail"

QUESTION_RESULT = IntentResult(Intent.QUESTION)

current_intent: ContextVar[IntentResult | None] = ContextVar(
    "current_intent", default=None
)


def get_current_intent() -> IntentResult | None:
    """The intent of the request being handled (None before classification)."""
    return current_intent.get()


@dataclass(frozen=True, slots=True)
class IntentMatch:
    """The classifier's verdict plus what the orchestrator needs to act on it."""

    result: IntentResult
    # Canned reply to give (see intent_examples); set only when result.pure.
    reply_key: str | None = None
    nearest: str = ""
    question_score: float = 0.0
    ms: float = 0.0


# Purchase wording; BUYING needs it (the embedding alone confuses "what is
# CIS" with "what does a CIS assessment cost").
_BUYING_CUE_RE = re.compile(
    r"\b(?:prices?|priced|pricing|costs?|costing|quotes?|quotation|buy|buying|"
    r"purchas(?:e|es|ed|ing)|sign\s{0,2}(?:me\s{1,3}|us\s{1,3})?up|subscribe|"
    r"demo|trial|book(?:ing)?|engage|how\s{1,3}much|get\s{1,3}started|"
    r"next\s{1,3}steps?|order|budget|afford(?:able)?|rates?|fees?|"
    r"go\s{1,3}ahead|onboard(?:ing)?)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[^\W_]{1,40}")
# Guarantee wording; the ASSURANCE reply needs it (bare "security" is a topic,
# not a request for a guarantee).
_ASSURANCE_CUE_RE = re.compile(
    r"\b(?:100\s{0,2}%|100\s{1,3}percent|guarantee[sd]?|promise[sd]?|"
    r"unhackable|hack[\s-]{0,2}proof|bullet[\s-]{0,2}proof|completely|totally|"
    r"fully|absolutely|perfectly|never\s{1,3}(?:be\s{1,3})?(?:hacked|breached))",
    re.IGNORECASE,
)


def has_buying_cue(text: str) -> bool:
    return _BUYING_CUE_RE.search(text[:MAX_CLASSIFY_CHARS]) is not None


def _names_kb_answerable(text: str, reply_key: str | None) -> bool:
    """True when the knowledge base answers the message, so no canned reply:
    a catalogue term, a contact detail / office / location (except for an
    incident: "our email was compromised"), a "what about X?" follow-up, or
    an assurance reply without guarantee wording."""
    if names_catalogue_term(text) or is_follow_up_fragment(text):
        return True
    if reply_key != INCIDENT and names_contact_detail(text):
        return True
    return (
        reply_key == ASSURANCE
        and _ASSURANCE_CUE_RE.search(text[:MAX_CLASSIFY_CHARS]) is None
    )


# ---------------------------------------------------------------------------
# Example index (embedded once per process and embedding model)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Index:
    model: str
    matrix: np.ndarray  # (n, dim), rows L2-normalised
    intents: tuple[Intent, ...]
    replies: tuple[str | None, ...]
    texts: tuple[str, ...]


@dataclass(slots=True)
class _State:
    index: _Index | None = None
    task: asyncio.Task | None = None
    task_model: str = ""
    backoff_until: float = 0.0


_state = _State()


def _unit_rows(vectors: list[list[float]]) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def build_index(model: str, vectors: list[list[float]]) -> _Index:
    """Index the examples from their vectors (same order as ``EXAMPLES``)."""
    return _Index(
        model=model,
        matrix=_unit_rows(vectors),
        intents=tuple(row[0] for row in EXAMPLES),
        replies=tuple(row[1] for row in EXAMPLES),
        texts=tuple(row[2] for row in EXAMPLES),
    )


async def _build(model: str) -> _Index:
    started = time.perf_counter()
    vectors = await asyncio.wait_for(
        embeddings.embed_texts([row[2] for row in EXAMPLES], stage=_STAGE),
        timeout=WARMUP_TIMEOUT_S,
    )
    index = build_index(model, vectors)
    _state.index = index
    logger.info(
        "intent_index_built",
        examples=len(EXAMPLES),
        model=model,
        ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return index


def warm_up() -> asyncio.Task:
    """Start (or return) the background build of the example vectors.

    Safe to call at startup and on every request: one build per model.
    """
    model = current_model_settings().embedding_model
    task = _state.task
    if (
        task is None
        or _state.task_model != model
        or (task.done() and (task.cancelled() or task.exception() is not None))
    ):
        task = asyncio.get_running_loop().create_task(_build(model))
        task.add_done_callback(_log_build_failure)
        _state.task, _state.task_model = task, model
    return task


def _log_build_failure(task: asyncio.Task) -> None:
    """A failed build is logged (and retried on the next request), never raised."""
    if not task.cancelled() and task.exception() is not None:
        logger.warning("intent_index_build_failed", error=str(task.exception())[:200])


async def _get_index() -> _Index | None:
    """The example index, or None while the first build is still running."""
    model = current_model_settings().embedding_model
    if _state.index is not None and _state.index.model == model:
        return _state.index
    task = warm_up()
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=WARMUP_WAIT_S)
    except TimeoutError:
        logger.info("intent_index_warming")
        return None


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text[:MAX_CLASSIFY_CHARS]))


def _label_scores(sims: np.ndarray, index: _Index) -> dict[Intent, tuple[float, int]]:
    """Best (similarity, example row) per label."""
    best: dict[Intent, tuple[float, int]] = {}
    for row, (intent, sim) in enumerate(zip(index.intents, sims, strict=True)):
        if intent not in best or sim > best[intent][0]:
            best[intent] = (float(sim), row)
    return best


def nearest_intent(text: str, vector: list[float], index: _Index) -> IntentMatch:
    """Pure decision from one query vector (unit tests feed fake vectors)."""
    query = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(query)) or 1.0
    labels = _label_scores(index.matrix @ (query / norm), index)
    intent, (score, row) = max(labels.items(), key=lambda item: item[1][0])
    question_score = labels.get(Intent.QUESTION, (0.0, 0))[0]
    nearest = index.texts[row]

    buying_score = labels.get(Intent.BUYING, (0.0, 0))[0]
    if (intent in (Intent.BUYING, Intent.QUESTION) and has_buying_cue(text)) and (
        buying_score >= BUYING_MIN_SCORE
    ):
        return IntentMatch(
            IntentResult(Intent.BUYING, buying_score),
            nearest=nearest,
            question_score=question_score,
        )
    if intent in (Intent.QUESTION, Intent.BUYING):
        return IntentMatch(
            IntentResult(Intent.QUESTION, question_score),
            nearest=nearest,
            question_score=question_score,
        )
    clear = score >= MIN_SCORE and score - question_score >= MIN_MARGIN
    pure = (
        clear
        and _word_count(text) <= MAX_PURE_WORDS
        and index.replies[row] is not None
        and not _names_kb_answerable(text, index.replies[row])
    )
    return IntentMatch(
        IntentResult(intent if clear else Intent.QUESTION, score, pure),
        reply_key=index.replies[row] if pure else None,
        nearest=nearest,
        question_score=question_score,
    )


async def _classify(text: str) -> IntentMatch:
    if not text or len(text) > MAX_CLASSIFY_CHARS:
        return IntentMatch(QUESTION_RESULT)
    if time.monotonic() < _state.backoff_until:
        return IntentMatch(QUESTION_RESULT)
    try:
        index = await _get_index()
        if index is None:
            return IntentMatch(QUESTION_RESULT)
        vector = await asyncio.wait_for(
            embeddings.embed_text(text, stage=_STAGE), timeout=QUERY_TIMEOUT_S
        )
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        # Fail open: the message is a QUESTION and retrieval decides.
        _state.backoff_until = time.monotonic() + FAILURE_BACKOFF_S
        logger.warning("intent_classifier_unavailable", error=str(exc)[:200])
        return IntentMatch(QUESTION_RESULT)
    return nearest_intent(text, vector, index)


async def classify_intent(text: str) -> IntentMatch:
    """Classify ``text`` (already normalised); never raises.

    The result is also stored on ``current_intent`` for later stages.
    """
    started = time.perf_counter()
    match = await _classify(text.strip())
    ms = (time.perf_counter() - started) * 1000
    current_intent.set(match.result)
    logger.info(
        "intent_classified",
        intent=match.result.intent.value,
        score=round(match.result.score, 3),
        question_score=round(match.question_score, 3),
        pure=match.result.pure,
        nearest=match.nearest[:60],
        ms=round(ms, 1),
    )
    return IntentMatch(
        match.result, match.reply_key, match.nearest, match.question_score, ms
    )
