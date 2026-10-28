"""Clarifying questions for genuinely ambiguous queries (outcome ``clarify``).

Cheap gates decide whether the model may ask instead of answer, and the
model makes the final call in the same generation request (no extra LLM call):

1. ``clarify_eligible`` (rules, before retrieval): only a short,
   underspecified, standalone question qualifies, and never more than
   ``clarify_max_consecutive`` times in a row in one session. Greetings,
   blocked and off-topic queries never get here. Eligible questions are
   retrieved wider (more documents) so the options can cover the real intents.
2. ``plan_clarification``: the retrieved evidence must spread over at least
   ``clarify_min_options`` distinct topics (document + section) with similar
   scores; otherwise the question is just answered.
3. The answer call then gets a dedicated clarification brief and a strict JSON
   schema (``ANSWER_OR_CLARIFY``): the model either answers or asks 1 to
   ``clarify_max_questions`` questions at once, each option citing the topic
   ids (retrieved chunks) that can answer it.

``parse_decision`` validates the model's questions server-side: an option
whose sources are not in the retrieved set is dropped, a question left with
fewer than two options is dropped, and with no question left the query is
answered directly. At most one option per question is tagged ``recommended``
by ``recommend_option``: a deterministic margin rule over the retrieval
scores already computed (never the model, so document text cannot steer it).
The follow-up turn carries all answers at once;
``resolve_clarification_reply`` rewrites the original question plus those
answers into one explicit query, and the chosen options' documents are
boosted when it is answered.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from app.core.config import get_settings
from app.guardrails.conversational import detect_conversational_intent
from app.retrieval.retriever import decompose_query
from app.schemas.query import (
    ChatTurn,
    Clarification,
    ClarificationAnswer,
    ClarificationOption,
    ClarificationQuestion,
    SourceChunk,
)

CLARIFY_OUTCOME = "clarify"
DEFAULT_HEADER = "Topic"
REPLY_HINT = "Pick one, or tell me a bit more."

_MAX_HEADER_CHARS = 12
_MAX_LABEL_CHARS = 60
_MAX_LABEL_WORDS = 6
_MAX_DESCRIPTION_CHARS = 200
_MAX_QUESTION_CHARS = 300
_MAX_PREAMBLE_CHARS = 300
_MAX_QUERY_CHARS = 2000
_MAX_OTHER_CHARS = 300
_MAX_OPTION_SOURCES = 4
_EXCERPT_CHARS = 220
_SCORE_EPSILON = 1e-9  # float noise: 0.65 - 0.60 must count as a 0.05 lead
RECOMMENDED_SUFFIX = " (recommended)"
NOT_SURE_LABEL = "Not sure, help me choose"
_NOT_SURE_HINT = (
    "not sure which fits, so recommend the best fit for my goal and current "
    "maturity, or suggest I talk to a specialist"
)
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'&+-]*")
_ANSWER_SPLIT_RE = re.compile(r"[,;\n·]+")
# Brand / company names: a question that names them is specific, not vague.
BRAND_RE = re.compile(
    r"\b(?:exigo(?: tech)?|deploy[ _-]{0,3}(?:iq|qi))\b", re.IGNORECASE
)
# Company facts (who / where / when, headquarters, contact details...) are
# answered from the knowledge base, never turned into a "which service?" card.
COMPANY_FACT_RE = re.compile(
    r"^(?:who|where|when)\b|\b(?:ceo|cto|founder|founded|owner|owned|employees|"
    r"headquarter\w{0,6}|hq|located|location|address|phone|telephone|contact|"
    r"e-?mail|call you|offices?|abn)\b",
    re.IGNORECASE,
)
# "How does it work?" with no history in a one-product assistant: answered
# generally about the product, not by asking which service is meant.
GENERIC_PRODUCT_RE = re.compile(
    r"^(?:(?:so|ok|okay|and) )?(?:how (?:does|do) (?:it|this|that) work|"
    r"what (?:does|do) (?:it|this|that) do|what (?:is|are) (?:it|this|that)|"
    r"(?:is|are) (?:it|this|that) (?:safe|secure|free|legit|reliable)|"
    r"does (?:it|this) work)\W{0,5}$",
    re.IGNORECASE,
)
# "Is that product safe to purchase?": about buying, answered (a general answer
# or the last discussed product), never "which DeployIQ service?". Literal
# alternatives only, each optional part is bounded (no nested quantifiers).
PURCHASE_TRUST_RE = re.compile(
    r"^(?:(?:so|ok|okay|and) )?(?:is|are|would|will) (?:(?:that|this|the|it) )?"
    r"(?:(?:product|service|solution|offering|tool) )?"
    r"(?:safe|secure|reliable|legit|trustworthy|worth it)"
    r"(?: (?:to|for me to))?(?: (?:purchase|buy|use|trust|get|buying|purchasing))?"
    r"\W{0,5}$",
    re.IGNORECASE,
)
# "What is X?" / "Explain X": a definition request that names its subject.
_DEFINITION_LEAD_RE = re.compile(
    r"^(?:(?:so|ok|okay|and|please) )?(?:what(?: is|'s|s| are)|define|explain|"
    r"describe|tell me about)\b",
    re.IGNORECASE,
)
# A security incident in progress: answered (what to do now), never a card.
INCIDENT_RE = re.compile(
    r"\b(?:hacked|breached|compromised|ransomware|phished|malware|data breach)\b",
    re.IGNORECASE,
)
# The vague "what are you hoping to get done?" card (ambiguity.GOAL): its header,
# option labels and the fixed question each pick is folded into (the model never
# writes either). A pick replaces the vague original.
GOAL_HEADER = "Goal"
GOAL_QUESTION = "What are you hoping to get done?"
GOAL_OVERVIEW = "Understand what you do"
GOAL_AUDIT = "Get audit ready"
GOAL_SECURITY = "Fix or tighten security"
GOAL_PRICING = "Pricing or talk to someone"
GOAL_AUDIT_QUERY = (
    "How does DeployIQ help an organisation get audit ready: which frameworks "
    "(Essential Eight, ISO 27001, CIS) does it cover and what evidence and timeline?"
)
GOAL_QUERIES: dict[str, str] = {
    GOAL_OVERVIEW: (
        "What does DeployIQ do and which services does it offer to "
        "organisations like yours?"
    ),
    GOAL_AUDIT: GOAL_AUDIT_QUERY,
    GOAL_SECURITY: (
        "How does DeployIQ assess and improve Microsoft 365 security: Security "
        "Baseline Assessment, Zero Trust, CIS Benchmark?"
    ),
    GOAL_PRICING: (
        "How do I get pricing and talk to a DeployIQ specialist: engagement "
        "model, timelines, contact?"
    ),
}


def is_pricing_pick(query: str) -> bool:
    """The folded query of the Goal card's pricing pick (context may follow).

    Answered by a fixed reply (the knowledge base publishes no prices).
    """
    return query.startswith(GOAL_QUERIES[GOAL_PRICING])


# Words of a vague opener that carry no context worth keeping next to a pick.
_VAGUE_WORDS = frozenset(
    [
        "need",
        "want",
        "help",
        "advice",
        "something",
        "stuff",
        "know",
        "idk",
        "confused",
        "sure",
        "idea",
        "question",
        "start",
        "best",
        "recommend",
        "options",
        "option",
        "compare",
        "tell",
        "things",
        "thing",
        "have",
        "looking",
        "look",
        "into",
        "improve",
        "get",
        "started",
        "should",
        "buy",
        "am",
        "not",
        "no",
        "dunno",
        "unsure",
        "lost",
        "assistance",
        "guidance",
        "support",
    ]
)
_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "can",
        "do",
        "does",
        "for",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "please",
        "tell",
        "the",
        "to",
        "we",
        "what",
        "whats",
        "what's",
        "which",
        "who",
        "why",
        "with",
        "you",
        "your",
    }
)

_STRING = {"type": "string"}
# Structured output for a gated answer call (Azure OpenAI strict JSON schema).
# Lengths/counts are not expressible in strict mode; parse_decision enforces them.
ANSWER_OR_CLARIFY: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "answer_or_clarify",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "answer", "preamble", "questions"],
            "properties": {
                "action": {"type": "string", "enum": ["answer", "clarify"]},
                "answer": _STRING,
                "preamble": _STRING,
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["header", "question", "multi_select", "options"],
                        "properties": {
                            "header": _STRING,
                            "question": _STRING,
                            "multi_select": {"type": "boolean"},
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["label", "description", "source_ids"],
                                    "properties": {
                                        "label": _STRING,
                                        "description": _STRING,
                                        "source_ids": {
                                            "type": "array",
                                            "items": _STRING,
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


@dataclass(frozen=True, slots=True)
class ClarifyCandidate:
    """A distinct retrieved topic (document + section) the question could be about."""

    id: str
    label: str
    document_id: str
    source_name: str | None
    section: str | None
    score: float
    excerpt: str = ""


@dataclass(frozen=True, slots=True)
class LlmDecision:
    """The gated answer call's result: an answer, or questions back.

    ``answer == ""`` and no clarification means the model asked but nothing
    was grounded: the caller answers directly instead.
    """

    answer: str
    clarification: Clarification | None = None


@dataclass(frozen=True, slots=True)
class ResolvedReply:
    """Answers to a clarification, folded into one explicit question."""

    query: str
    original_query: str
    answers: list[dict[str, object]] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    free_text: bool = False

    def as_details(self) -> dict[str, object]:
        return {
            "original_query": self.original_query,
            "answers": self.answers,
            "document_ids": self.document_ids,
            "free_text": self.free_text,
        }


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def content_word_count(query: str) -> int:
    """Words that carry meaning (stopwords such as "what is the" excluded)."""
    words = (w.lower() for w in _WORD_RE.findall(query))
    return sum(1 for w in words if w not in _STOPWORDS)


def clarification_streak(history: list[ChatTurn] | None) -> int:
    """How many of the latest assistant turns in a row were clarifications."""
    streak = 0
    for turn in reversed(history or []):
        if turn.role != "assistant":
            continue
        if turn.outcome != CLARIFY_OUTCOME:
            break
        streak += 1
    return streak


def _scan(query: str) -> str:
    """Whitespace-collapsed, length-bounded user text (before any regex)."""
    return " ".join(query.split())[:_MAX_QUERY_CHARS]


def _names_known_entity(query: str) -> bool:
    """The question names the company/brand, or a service plus something about it.

    A bare topic ("E8?", "zero trust?") names a service but asks nothing about
    it yet, so it may still be clarified; "Exigo Tech address" or "E8 pricing"
    is already specific.
    """
    # Lazy: ambiguity imports this module at load time.
    from app.harness import ambiguity  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    scan = _scan(query)
    if BRAND_RE.search(scan):
        return True
    rest = ambiguity.strip_offerings(scan)
    if rest == scan:
        return False
    return content_word_count(rest) > 0 or _DEFINITION_LEAD_RE.match(scan) is not None


def is_generic_product_question(query: str) -> bool:
    """A generic one-product or purchase-trust question: answered, not asked back."""
    scan = _scan(query)
    return (
        GENERIC_PRODUCT_RE.match(scan) is not None
        or PURCHASE_TRUST_RE.match(scan) is not None
    )


def is_company_or_generic(query: str) -> bool:
    """Company-fact and generic one-product questions are answered, not asked back."""
    scan = _scan(query)
    return (
        COMPANY_FACT_RE.search(scan) is not None
        or is_generic_product_question(scan)
        or INCIDENT_RE.search(scan) is not None
    )


def clarify_eligible(
    query: str, history: list[ChatTurn] | None, *, follow_up: bool
) -> bool:
    """Rule gate (no I/O): may this question be answered with questions back?

    Only bare topics, pronoun questions and cost/time questions qualify: a
    question that names the company or a service, or asks a company fact
    (who/where/when, headquarters, contact details), is answered directly.
    """
    settings = get_settings()
    if not settings.clarify_enabled:
        return False
    if clarification_streak(history) >= settings.clarify_max_consecutive:
        return False
    # A follow-up is resolved by the conversation, not by asking again.
    if follow_up and history:
        return False
    if not 0 < content_word_count(query) <= settings.clarify_max_query_words:
        return False
    if is_company_or_generic(query) or _names_known_entity(query):
        return False
    return len(decompose_query(query)) <= 1


def humanize_source_name(name: str) -> str:
    stem = PurePosixPath(name.replace("\\", "/")).name
    stem = re.sub(r"\.(md|markdown|txt|pdf|docx?|html?)$", "", stem, flags=re.I)
    text = re.sub(r"[_\-]+", " ", stem).strip()
    if text and text == text.lower():
        text = text[:1].upper() + text[1:]
    return text or name


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def metadata_text(metadata: dict, *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def select_candidates(sources: list[SourceChunk]) -> list[ClarifyCandidate]:
    """Distinct topics (document + section) scoring close to the best one."""
    settings = get_settings()
    if not sources:
        return []
    ranked = sorted(sources, key=lambda s: s.score, reverse=True)
    floor = max(ranked[0].score - settings.clarify_score_margin, 0.0)
    limit = settings.clarify_retrieval_max_documents + settings.clarify_max_options

    picked: list[ClarifyCandidate] = []
    seen_topics: set[tuple[str, str]] = set()
    for source in ranked:
        if source.score < floor or len(picked) >= limit:
            break
        metadata = source.metadata or {}
        source_name = metadata_text(metadata, "source_name", "file_name", "title")
        section = metadata_text(metadata, "section_heading", "heading")
        topic = (source.document_id, (section or "").lower())
        if topic in seen_topics:
            continue
        seen_topics.add(topic)
        doc_label = humanize_source_name(source_name) if source_name else None
        label = " / ".join(p for p in (doc_label, section) if p)
        if not label:
            continue
        picked.append(
            ClarifyCandidate(
                id=f"T{len(picked) + 1}",
                label=_clip(label, 120),
                document_id=source.document_id,
                source_name=source_name,
                section=section,
                score=source.score,
                excerpt=_clip(source.content, _EXCERPT_CHARS),
            )
        )
    return picked


def plan_clarification(
    query: str,
    sources: list[SourceChunk],
    history: list[ChatTurn] | None,
    *,
    follow_up: bool,
) -> list[ClarifyCandidate]:
    """Topics to offer the model, or ``[]`` when the query must just be answered."""
    settings = get_settings()
    if not clarify_eligible(query, history, follow_up=follow_up):
        return []
    candidates = select_candidates(sources)
    # Low scores mean the evidence is irrelevant ("What is a cat?"): nothing
    # to choose between, so the question is just answered.
    if not candidates or candidates[0].score < settings.clarify_min_gate_score:
        return []
    distinct_docs = {c.document_id for c in candidates}
    enough = len(candidates) >= settings.clarify_min_options
    # One document with several sections rarely means several intents.
    return candidates if enough and len(distinct_docs) >= 2 else []  # noqa: PLR2004


def clarify_instruction(candidates: list[ClarifyCandidate]) -> str:
    """Dedicated clarification brief for the gated call (reply: ANSWER_OR_CLARIFY)."""
    settings = get_settings()
    topics = "\n".join(f'{c.id}: {c.label} - "{c.excerpt}"' for c in candidates)
    return (
        "Clarification brief. The question is short and the context covers "  # nosec B608 - LLM prompt text, not SQL
        "these distinct topics (id: document / section - excerpt):\n"
        f"{topics}\n\n"
        "Reply in the required JSON.\n"
        "- If the question clearly fits one topic, or only one intent is "
        'supported by the context, set action to "answer", write the full '
        'answer in answer, preamble "" and questions [].\n'
        "- Only if it is genuinely ambiguous between intents the context can "
        'answer, set action to "clarify" and answer "". Write preamble as one '
        "short, friendly sentence showing you understood the question. Ask as "
        f"few questions as needed (1 to {settings.clarify_max_questions}), all "
        "at once.\n"
        "- If the question names a company or entity (for example the company "
        "or a service by name), or is a general company fact (who, where, "
        "when, headquarters, contact details, ownership), set action to "
        '"answer" and answer it from the context.\n'
        "- Each question: header (1-2 word tab label, at most 12 characters, "
        "e.g. Topic, Service, Scope), question (one specific sentence ending "
        "with ?), multi_select (true only when several options can apply "
        "together), and 2-4 mutually exclusive options.\n"
        "- Each option must be answerable from the context: label (2-6 words, "
        'specific, e.g. "Buy a DeployIQ service" or "Pricing and plans"), '
        "description (one line saying exactly what they will get, naming the "
        "concrete offerings, plans or pages found in the context) and "
        "source_ids (the topic ids above that answer it). Never offer an "
        "option the context cannot answer, and never a generic option such "
        '"contact sales" unless the context has that page.\n'
        "- Use Australian English and a warm, professional tone.\n\n"
    )


# ---------------------------------------------------------------------------
# Recommended option (rule-based, from the retrieval scores)
# ---------------------------------------------------------------------------


def recommend_option(
    options: list[ClarificationOption],
    scores: list[float],
    defaults: list[bool] | None = None,
) -> list[ClarificationOption]:
    """Tag at most one option ``recommended``, only when the evidence clearly leads.

    ``scores[i]`` is option ``i``'s best grounding score. The top option is
    recommended when it leads the runner-up by ``clarify_recommend_margin``;
    otherwise, among the options tied with the top, a single ``defaults`` flag
    breaks the tie. Anything else recommends nothing. The order is kept (the
    1-4 shortcuts map to positions), and nothing is ever pre-selected.
    """
    settings = get_settings()
    if (
        not settings.clarify_recommend_enabled
        or len(options) < 2  # noqa: PLR2004 - a lead needs a runner-up
        or len(scores) != len(options)
    ):
        return options
    margin = settings.clarify_recommend_margin
    order = sorted(range(len(options)), key=lambda i: scores[i], reverse=True)
    top = scores[order[0]]
    lead = top - scores[order[1]]
    pick: int | None = None
    if lead > _SCORE_EPSILON and lead + _SCORE_EPSILON >= margin:
        pick = order[0]
    elif defaults is not None and len(defaults) == len(options):
        tied = [i for i in order if top - scores[i] + _SCORE_EPSILON < margin]
        flagged = [i for i in tied if defaults[i]]
        pick = flagged[0] if len(flagged) == 1 else None
    if pick is None:
        return options
    return [
        o.model_copy(update={"recommended": i == pick}) for i, o in enumerate(options)
    ]


# ---------------------------------------------------------------------------
# Validation of the model's questions
# ---------------------------------------------------------------------------


def _label(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    words = raw.split()
    if not words:
        return None
    return _clip(" ".join(words[:_MAX_LABEL_WORDS]), _MAX_LABEL_CHARS)


def _question_text(raw: object) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = _clip(raw, _MAX_QUESTION_CHARS - 1).rstrip(" .:")
    return text if text.endswith("?") else f"{text}?"


def _header(raw: object, index: int) -> str:
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        return DEFAULT_HEADER if index == 1 else f"Question {index}"[:_MAX_HEADER_CHARS]
    return text if len(text) <= _MAX_HEADER_CHARS else text[:_MAX_HEADER_CHARS].rstrip()


def _grounding(
    raw: object, by_topic: dict[str, ClarifyCandidate]
) -> list[ClarifyCandidate]:
    """Retrieved topics an option cites (unknown ids dropped)."""
    topics: list[ClarifyCandidate] = []
    for item in raw if isinstance(raw, list) else []:
        topic = by_topic.get(item.strip().upper()) if isinstance(item, str) else None
        if topic is not None and topic not in topics:
            topics.append(topic)
    return topics


def _options(
    raw: object, by_topic: dict[str, ClarifyCandidate]
) -> list[ClarificationOption]:
    settings = get_settings()
    options: list[ClarificationOption] = []
    scores: list[float] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        label = _label(item.get("label"))
        topics = _grounding(item.get("source_ids"), by_topic)
        # Ungrounded options are dropped: never offer what cannot be answered.
        if label is None or label.lower() in seen or not topics:
            continue
        seen.add(label.lower())
        description = item.get("description")
        doc_ids = list(dict.fromkeys(t.document_id for t in topics))
        names = list(dict.fromkeys(t.source_name for t in topics if t.source_name))
        options.append(
            ClarificationOption(
                id=f"O{len(options) + 1}",
                label=label,
                description=(
                    _clip(description, _MAX_DESCRIPTION_CHARS)
                    if isinstance(description, str)
                    else ""
                ),
                document_ids=doc_ids[:_MAX_OPTION_SOURCES],
                sources=names[:_MAX_OPTION_SOURCES],
            )
        )
        scores.append(max(t.score for t in topics))
        if len(options) >= settings.clarify_max_options:
            break
    return recommend_option(options, scores)


def build_clarification(
    payload: dict, candidates: list[ClarifyCandidate], original_query: str
) -> Clarification | None:
    """Validated questions from the model's payload, or ``None`` if none is grounded."""
    settings = get_settings()
    by_topic = {c.id: c for c in candidates}
    questions: list[ClarificationQuestion] = []
    seen: set[str] = set()
    raw_questions = payload.get("questions")
    for raw in raw_questions if isinstance(raw_questions, list) else []:
        if not isinstance(raw, dict):
            continue
        text = _question_text(raw.get("question"))
        options = _options(raw.get("options"), by_topic)
        if text is None or text.lower() in seen:
            continue
        if len(options) < settings.clarify_min_options:
            continue
        seen.add(text.lower())
        index = len(questions) + 1
        questions.append(
            ClarificationQuestion(
                id=f"Q{index}",
                header=_header(raw.get("header"), index),
                question=text,
                multi_select=raw.get("multi_select") is True,
                options=options,
            )
        )
        if len(questions) >= settings.clarify_max_questions:
            break
    if not questions:
        return None
    preamble = payload.get("preamble")
    return Clarification(
        preamble=_clip(preamble, _MAX_PREAMBLE_CHARS)
        if isinstance(preamble, str)
        else "",
        questions=questions,
        original_query=original_query[:_MAX_QUERY_CHARS],
    )


def parse_decision(
    raw: str, candidates: list[ClarifyCandidate], original_query: str
) -> LlmDecision:
    """Read the gated call's JSON. Non-JSON output is treated as a plain answer."""
    try:
        payload = json.loads(raw.strip())
    except ValueError:
        return LlmDecision(answer=raw)
    if not isinstance(payload, dict):
        return LlmDecision(answer=raw)
    answer = payload.get("answer")
    answer = answer if isinstance(answer, str) else ""
    if payload.get("action") == "clarify":
        clarification = build_clarification(payload, candidates, original_query)
        if clarification is not None:
            return LlmDecision(answer="", clarification=clarification)
    return LlmDecision(answer=answer)


def render_clarification_text(clarification: Clarification) -> str:
    """Plain-text reply for clients that ignore the structured questions."""
    blocks: list[str] = [clarification.preamble] if clarification.preamble else []
    single = len(clarification.questions) == 1
    for qi, question in enumerate(clarification.questions, 1):
        lines = [question.question if single else f"{qi}. {question.question}"]
        for oi, option in enumerate(question.options, 1):
            marker = f"{oi}." if single else "  -"
            detail = f": {option.description}" if option.description else ""
            tag = RECOMMENDED_SUFFIX if option.recommended else ""
            lines.append(f"{marker} {option.label}{tag}{detail}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + f"\n\n{REPLY_HINT}"


# ---------------------------------------------------------------------------
# Answers -> one explicit question
# ---------------------------------------------------------------------------


def compose_query(original_query: str, parts: list[str]) -> str:
    """The original question plus the user's answers, as one standalone query."""
    suffix = f" ({'; '.join(p.strip() for p in parts if p.strip())})"
    room = max(_MAX_QUERY_CHARS - len(suffix), 0)
    return original_query.strip()[:room] + suffix


def _last_clarification(history: list[ChatTurn] | None) -> Clarification | None:
    for turn in reversed(history or []):
        if turn.role == "assistant":
            if turn.outcome == CLARIFY_OUTCOME:
                return turn.clarification
            return None
    return None


def _entry(
    question: ClarificationQuestion,
    options: list[ClarificationOption],
    other: str | None,
) -> dict[str, object]:
    return {
        "question_id": question.id,
        "header": question.header,
        "option_ids": [o.id for o in options],
        "labels": [o.label for o in options],
        "other_text": other,
        "document_ids": list(dict.fromkeys(d for o in options for d in o.document_ids)),
    }


def _part(entry: dict[str, object]) -> str:
    labels = [
        _NOT_SURE_HINT if label == NOT_SURE_LABEL else str(label)
        for label in entry["labels"]  # type: ignore[attr-defined]
    ]
    if entry.get("other_text"):
        labels.append(str(entry["other_text"]))
    return f"{entry['header']}: {' and '.join(labels)}"


def _from_answers(
    clarification: Clarification, answers: list[ClarificationAnswer]
) -> list[dict[str, object]]:
    by_id = {q.id: q for q in clarification.questions}
    entries: list[dict[str, object]] = []
    for answer in answers:
        question = by_id.get(answer.question_id)
        if question is None:
            continue
        options_by_id = {o.id: o for o in question.options}
        picked = [
            options_by_id[i]
            for i in dict.fromkeys(answer.option_ids)
            if i in options_by_id
        ]
        other = (
            _clip(answer.other_text, _MAX_OTHER_CHARS) if answer.other_text else None
        )
        if not question.multi_select:
            # Single select: "Other" replaces a picked option.
            picked = [] if other else picked[:1]
        if picked or other:
            entries.append(_entry(question, picked, other))
    return entries


def _match_text(clarification: Clarification, text: str) -> list[dict[str, object]]:
    """Plain-text reply: option number (one question) or option names."""
    cleaned = text.strip().strip(".!?)").strip().lower()
    questions = clarification.questions
    if len(questions) == 1 and cleaned.isdigit():
        index = int(cleaned) - 1
        options = questions[0].options
        if 0 <= index < len(options):
            return [_entry(questions[0], [options[index]], None)]
        return []
    pieces = {
        p.strip().strip(".!?").lower().removesuffix(RECOMMENDED_SUFFIX).strip()
        for p in _ANSWER_SPLIT_RE.split(text)
    }
    # A label may itself contain a comma ("Not sure, help me choose").
    pieces.add(cleaned.removesuffix(RECOMMENDED_SUFFIX).strip())
    entries: list[dict[str, object]] = []
    for question in questions:
        picked = [o for o in question.options if o.label.lower() in pieces]
        if picked:
            entries.append(
                _entry(question, picked if question.multi_select else picked[:1], None)
            )
    return entries


def _goal_context(original: str) -> str | None:
    """The vague original, only when it still says something ("a small business")."""
    words = [w.lower() for w in _WORD_RE.findall(original)]
    kept = [w for w in words if w not in _STOPWORDS and w not in _VAGUE_WORDS]
    return original.strip() if len(kept) >= 2 else None  # noqa: PLR2004


def _goal_query(last: Clarification, entries: list[dict[str, object]]) -> str | None:
    """A Goal pick replaces the vague original (kept only when it has context)."""
    if len(entries) != 1 or entries[0].get("other_text"):
        return None
    if entries[0].get("header") != GOAL_HEADER:
        return None
    labels = entries[0]["labels"]
    pick = GOAL_QUERIES.get(str(labels[0])) if labels else None  # type: ignore[index]
    if pick is None:
        return None
    context = _goal_context(last.original_query)
    return compose_query(pick, [context]) if context else pick


def resolve_clarification_reply(
    query: str,
    history: list[ChatTurn] | None,
    answers: list[ClarificationAnswer] | None = None,
) -> ResolvedReply | None:
    """Rewrite a reply to the last clarification into one explicit question.

    Structured ``answers`` (clients that render the question card) win;
    otherwise the plain ``query`` is matched by option number / names, and a
    short free-text reply refines the original question. ``None`` when the
    previous turn asked nothing (the query is then answered as it is).
    """
    last = _last_clarification(history)
    if last is None:
        return None
    entries = _from_answers(last, answers) if answers else _match_text(last, query)
    if entries:
        doc_ids = list(
            dict.fromkeys(d for e in entries for d in e["document_ids"])  # type: ignore[attr-defined]
        )
        goal_query = _goal_query(last, entries)
        return ResolvedReply(
            query=goal_query
            or compose_query(last.original_query, [_part(e) for e in entries]),
            original_query=last.original_query,
            answers=entries,
            document_ids=doc_ids,
        )
    settings = get_settings()
    # A new question ("what is E8?") is answered as such, not folded in.
    if (
        not answers
        and not query.strip().endswith("?")
        and detect_conversational_intent(query) is None
        and 0 < content_word_count(query) <= settings.clarify_max_query_words
    ):
        return ResolvedReply(
            query=compose_query(last.original_query, [query]),
            original_query=last.original_query,
            free_text=True,
        )
    return None


def boost_documents(
    sources: list[SourceChunk], document_ids: list[str] | None
) -> list[SourceChunk]:
    """Chosen options' documents first (stable), so the answer leads with them."""
    if not document_ids:
        return sources
    preferred = set(document_ids)
    return sorted(sources, key=lambda s: s.document_id not in preferred)
