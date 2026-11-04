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
base, so a canned reply never hijacks a real product question. Each reply
also needs what it is for (an identity reply a message about the assistant,
an incident reply the visitor's own incident, ...). BUYING is never canned
here; it is reported (``current_intent``) for the later stages. A callback
ask carrying the visitor's own phone number or email gets the callback reply
without an embedding call.

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

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.governance.pii import redact_pii
from app.guardrails.intent_examples import (
    ASSURANCE,
    CALLBACK,
    CALLBACK_DETAILS,
    EXAMPLES,
    IDENTITY,
    INCIDENT,
    MANAGER,
    PERSONA_CREATOR,
    PERSONA_LANGUAGES,
    PERSONA_MODEL,
    PERSONA_PRIVACY,
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
    # "book a demo" is buying, "confirm a booking" is not; "how much is
    # Frontier?" is a price ask, "by how much did ransomware drop?" is not.
    r"demo|trial|book|engage|get\s{1,3}started|"
    r"(?<!by\s)how\s{1,3}much\s{1,3}(?:is|are|does|do|would|will|for|to|it|per)|"
    r"next\s{1,3}steps?|order|budget|afford(?:able)?|rates?|fees?|"
    r"go\s{1,3}ahead|onboard(?:ing)?)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[^\W_]{1,40}")
# Explicit price / quote / purchase wording. A short ask carrying it ("quote",
# "quote pls", "pricing for Frontier") is buying even when the embedding of a
# two-word message sits nowhere near a buying example (0.29).
_EXPLICIT_BUYING_CUE_RE = re.compile(
    r"\b(?:prices?|priced|pricing|costs?|costing|quotes?|quotation|buy|buying|"
    r"purchas(?:e|es|ed|ing)|sign\s{0,2}(?:me\s{1,3}|us\s{1,3})?up|subscribe|"
    r"(?<!by\s)how\s{1,3}much\s{1,3}(?:is|are|does|do|would|will|for|to|it|per)|"
    r"order|budget|afford(?:able)?|rates?|fees?)\b",
    re.IGNORECASE,
)
SHORT_BUYING_MAX_WORDS = 6
# An information question about booking / a consultation / getting started
# ("Who will I speak with if I book a session?", "what is the booking page
# for?"): answered from the booking / get-started pages, not a buying ask,
# unless it also carries explicit price / quote / purchase wording.
_INFO_QUESTION_RE = re.compile(
    r"^\W{0,3}(?:who|whom|whose|what|what's|whats|when|where|which|why|"
    r"how\s{1,3}(?:long|does|do|is|are|many|will|would|can)|is|are|does|do|"
    r"will|would|should)\b",
    re.IGNORECASE,
)
# Guarantee wording; the ASSURANCE reply needs it (bare "security" is a topic,
# not a request for a guarantee).
_ASSURANCE_CUE_RE = re.compile(
    r"\b(?:100\s{0,2}%|100\s{1,3}percent|guarantee[sd]?|promise[sd]?|"
    r"unhackable|hack[\s-]{0,2}proof|bullet[\s-]{0,2}proof|completely|totally|"
    r"fully|absolutely|perfectly|never\s{1,3}(?:be\s{1,3})?(?:hacked|breached))",
    re.IGNORECASE,
)


# ... and a security object: "do you guarantee uninterrupted delivery?" is a
# terms-page question, not a request for a security guarantee.
_ASSURANCE_OBJECT_RE = re.compile(
    r"\b(?:secure|security|safe|safety|hack(?:ed|s|ing)?|unhackable|"
    r"hack[\s-]{0,2}proof|bullet[\s-]{0,2}proof|breach(?:ed|es)?|attack(?:ed|s)?|"
    r"protected|protection|risk[\s-]{0,2}free|invulnerable|impenetrable)\b",
    re.IGNORECASE,
)
# Who the assistant identity / persona replies are for: a message about "you"
# or "this chat". "When AI is used, who stays accountable?" is about AI in the
# service (Responsible AI page), not about the assistant.
_ADDRESSES_ASSISTANT_RE = re.compile(
    r"\b(?:you|u|ya|your|ur|yours|yourself|this|i|me|am|my|our|we|us)\b",
    re.IGNORECASE,
)
# A live incident is the visitor's own ("we got hacked", "help, our email was
# compromised"); "how does ransomware encrypt files?" is general knowledge.
_OWN_INCIDENT_RE = re.compile(
    r"\b(?:we|we've|we're|i|i've|i'm|our|my|us|me|help|urgent|right\s{1,3}now)\b",
    re.IGNORECASE,
)
# A manager hand-off is a request ("let me speak to your supervisor"); "my
# boss hates me" is a personal matter (off-topic).
_MANAGER_ASK_RE = re.compile(
    r"\b(?:speak|talk|chat|escalate|transfer|connect|put\s{1,3}me|get\s{1,3}me|"
    r"want|need|see|let\s{1,3}me|refer)\b",
    re.IGNORECASE,
)
# An explicit purchase ask ("I want to purchase Frontier for 200 users") is
# buying whatever the embedding score.
_PURCHASE_ASK_RE = re.compile(
    r"\b(?:(?:i|we)(?:'d|\s{1,3}would)\s{1,3}(?:like|love)|(?:i|we)\s{1,3}"
    r"(?:want|wanna|need|plan|intend|wish)|(?:i'm|we're|i\s{1,3}am|we\s{1,3}are)"
    r"\s{1,3}(?:ready|looking|keen|planning)|(?:can|could|may|how\s{1,3}do)\s{1,3}"
    r"(?:i|we))\s{1,3}(?:to\s{1,3})?(?:buy|purchase|order|subscribe|sign\s{1,3}up)\b",
    re.IGNORECASE,
)
# A callback ask ("call me", "can someone ring me back") ...
_CALLBACK_ASK_RE = re.compile(
    r"\b(?:call|ring|phone|text|sms|e-?mail|contact|reach)\s{1,3}(?:me|us)\b"
    r"|\bcall\s{0,2}back\b|\bring\s{1,3}(?:me|us)\s{1,3}back\b"
    r"|\bgive\s{1,3}(?:me|us)\s{1,3}a\s{1,3}(?:call|ring|bell)\b"
    r"|\bget\s{1,3}(?:back\s{1,3}to|in\s{1,3}touch\s{1,3}with)\s{1,3}(?:me|us)\b"
    r"|\b(?:someone|somebody|your\s{1,3}team|you)\s{1,3}(?:please\s{1,3})?"
    r"(?:call|ring|phone|contact|email)\b",
    re.IGNORECASE,
)
_ABOUT_ASSISTANT_KEYS = frozenset(
    {IDENTITY, PERSONA_CREATOR, PERSONA_MODEL, PERSONA_LANGUAGES, PERSONA_PRIVACY}
)
# What each persona reply answers: "what happens to the stuff I type into this
# chatbot?" is near "who made this chatbot" but asks nothing about a maker.
_PERSONA_CUE_RES: dict[str, re.Pattern[str]] = {
    PERSONA_CREATOR: re.compile(
        r"\b(?:who|made|makes?|built|builds?|created|creates?|developed|develops?|"
        r"programmed|coded|owns?|owned|behind|work(?:s|ing)?\s{1,3}for|company|"
        r"creators?|makers?|developers?|vendors?)\b",
        re.IGNORECASE,
    ),
    PERSONA_MODEL: re.compile(
        r"\b(?:models?|llms?|ai|gpt|chat\s{0,2}gpt|openai|claude|gemini|engine|"
        r"powers?|powered|built\s{1,3}on|based\s{1,3}on|running\s{1,3}on|"
        r"language)\b",
        re.IGNORECASE,
    ),
    PERSONA_LANGUAGES: re.compile(
        r"\b(?:languages?|speak|speaks|multilingual|english|spanish|french|german|"
        r"italian|chinese|mandarin|japanese|korean|hindi|arabic|vietnamese|"
        r"portuguese)\b",
        re.IGNORECASE,
    ),
    PERSONA_PRIVACY: re.compile(
        r"\b(?:private|privacy|confidential|stored?|saved?|record(?:ed|s)?|"
        r"logged|logs?|kept|keep|reads?|see|sees|shared?|monitor(?:ed)?|secure|"
        r"encrypted|anonymous)\b",
        re.IGNORECASE,
    ),
}


def has_buying_cue(text: str) -> bool:
    """Purchase wording (price, quote, buy, demo, sign up, ...)."""
    return _BUYING_CUE_RE.search(text[:MAX_CLASSIFY_CHARS]) is not None


def has_explicit_buying_cue(text: str) -> bool:
    """Price, quote or purchase wording (not demo / booking / get started)."""
    return _EXPLICIT_BUYING_CUE_RE.search(text[:MAX_CLASSIFY_CHARS]) is not None


def is_booking_info_question(text: str) -> bool:
    """A question about booking, a demo or getting started with no price,
    quote or purchase wording: information, not a buying ask."""
    capped = text[:MAX_CLASSIFY_CHARS]
    return (
        _INFO_QUESTION_RE.search(capped) is not None
        and has_buying_cue(capped)
        and not has_explicit_buying_cue(capped)
    )


def is_purchase_ask(text: str) -> bool:
    """An explicit "I want to buy / purchase / order ..." ask."""
    return _PURCHASE_ASK_RE.search(text[:MAX_CLASSIFY_CHARS]) is not None


def _gives_own_contact_detail(text: str) -> bool:
    """The message carries a phone number or email that is not the company's
    own (PII_ALLOWLIST)."""
    found = redact_pii(text, allowlist=get_settings().pii_allowlist)[1]
    return "phone" in found or "email" in found


def callback_with_details(text: str) -> bool:
    """A callback ask that carries the visitor's own phone number or email
    ("My name is John, 0412 345 678, can someone call me about Essential
    Eight?"): the callback reply, whatever topic it names."""
    capped = text[:MAX_CLASSIFY_CHARS]
    return _CALLBACK_ASK_RE.search(capped) is not None and _gives_own_contact_detail(
        capped
    )


def _key_mismatch(text: str, reply_key: str | None) -> bool:
    """True when the message lacks what the reply is for: an identity / persona
    reply needs a message about the assistant, an incident reply the
    visitor's own incident, a manager reply a hand-off request, an assurance
    reply guarantee wording about security."""
    capped = text[:MAX_CLASSIFY_CHARS]
    if reply_key in _ABOUT_ASSISTANT_KEYS:
        cue = _PERSONA_CUE_RES.get(reply_key)
        return _ADDRESSES_ASSISTANT_RE.search(capped) is None or (
            cue is not None and cue.search(capped) is None
        )
    if reply_key == CALLBACK:
        # "what's the best number to ring you on" asks for our number.
        return _CALLBACK_ASK_RE.search(capped) is None
    if reply_key == INCIDENT:
        return _OWN_INCIDENT_RE.search(capped) is None
    if reply_key == MANAGER:
        return _MANAGER_ASK_RE.search(capped) is None
    if reply_key == ASSURANCE:
        return (
            _ASSURANCE_CUE_RE.search(capped) is None
            or _ASSURANCE_OBJECT_RE.search(capped) is None
        )
    return False


def _names_kb_answerable(text: str, reply_key: str | None) -> bool:
    """True when the knowledge base answers the message, so no canned reply:
    a catalogue term, a contact detail / office / location (except for an
    incident: "our email was compromised"), a "what about X?" follow-up, or
    a message that lacks what the reply is for (``_key_mismatch``)."""
    if names_catalogue_term(text) or is_follow_up_fragment(text):
        return True
    if reply_key != INCIDENT and names_contact_detail(text):
        return True
    return _key_mismatch(text, reply_key)


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


def _buying_wording(text: str, buying_score: float) -> bool:
    """Buying wording near a buying example, not a booking info question."""
    return (
        has_buying_cue(text)
        and buying_score >= BUYING_MIN_SCORE
        and not is_booking_info_question(text)
    )


def _short_price_ask(text: str, *, open_label: bool) -> bool:
    """A short ask with explicit price / quote / purchase wording ("quote",
    "quote pls"): buying at any score, unless it clearly sits nearest a
    canned label (``open_label`` False)."""
    return (
        open_label
        and has_explicit_buying_cue(text)
        and _word_count(text) <= SHORT_BUYING_MAX_WORDS
    )


def nearest_intent(text: str, vector: list[float], index: _Index) -> IntentMatch:
    """Pure decision from one query vector (unit tests feed fake vectors)."""
    query = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(query)) or 1.0
    labels = _label_scores(index.matrix @ (query / norm), index)
    intent, (score, row) = max(labels.items(), key=lambda item: item[1][0])
    question_score = labels.get(Intent.QUESTION, (0.0, 0))[0]
    nearest = index.texts[row]

    buying_score = labels.get(Intent.BUYING, (0.0, 0))[0]
    retrieved = intent in (Intent.BUYING, Intent.QUESTION)
    clear = score >= MIN_SCORE and score - question_score >= MIN_MARGIN
    # "quote" sits nearest an unclear "bye" (0.41): a question either way, so
    # the short price-ask rule applies to it too.
    if (
        is_purchase_ask(text)
        or _short_price_ask(text, open_label=retrieved or not clear)
        or (retrieved and _buying_wording(text, buying_score))
    ):
        return IntentMatch(
            IntentResult(Intent.BUYING, buying_score),
            nearest=nearest,
            question_score=question_score,
        )
    if retrieved:
        return IntentMatch(
            IntentResult(Intent.QUESTION, question_score),
            nearest=nearest,
            question_score=question_score,
        )
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
    if callback_with_details(text):
        # Deterministic: no embedding call, and it works while warming up.
        return IntentMatch(
            IntentResult(Intent.CONTACT, 1.0, True),
            reply_key=CALLBACK_DETAILS,
            nearest="callback with contact details",
        )
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
