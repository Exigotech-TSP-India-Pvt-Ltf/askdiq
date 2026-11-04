# pylint: disable=too-many-lines
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
from app.guardrails.pre_checks import is_junk_input
from app.harness.prompt_catalog import NO_ANSWER_LINE
from app.retrieval.reranker import entity_documents, entity_mentions, rerank_async
from app.retrieval.retriever import (
    decompose_query,
    history_subject,
    is_bare_reference,
    is_contact_ask,
)
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
REPLY_HINT = "Just pick an option, or tell me a bit more about what you need."

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


_THIRD_PARTY_TOOLS = frozenset(
    [
        "slack",
        "salesforce",
        "jira",
        "servicenow",
        "teams",
        "okta",
        "splunk",
        "aws",
        "azure",
        "google",
        "gcp",
        "zendesk",
        "hubspot",
        "github",
        "gitlab",
        "confluence",
        "sharepoint",
        "workday",
        "sap",
        "oracle",
        "datadog",
        "pagerduty",
        "crowdstrike",
        "sentinel",
        "snowflake",
        "zoom",
        "dropbox",
        "box",
        "docusign",
        "twilio",
        "stripe",
        "tableau",
    ]
)
_INTEGRATION_WORDS = frozenset(
    [
        "integrate",
        "integrates",
        "integration",
        "integrations",
        "connect",
        "connects",
        "work",
        "works",
    ]
)
_ASSURANCE_WORDS = frozenset(
    ["guarantee", "guaranteed", "guarantees", "certified", "sla", "uptime"]
)
_ASSURANCE_PHRASES = ("is it safe", "are you safe", "is it secure", "compliant with")


def _asks_integration_or_assurance(query: str) -> bool:
    """Third-party integration or assurance questions are answered, not clarified."""
    scan = _scan(query)
    words = [w.strip("?.,!;:'\"()").lower() for w in scan.split()]
    word_set = set(words)
    if word_set & _ASSURANCE_WORDS:
        return True
    low = " ".join(words)
    if any(p in low for p in _ASSURANCE_PHRASES):
        return not _names_framework(low)
    return bool(word_set & _INTEGRATION_WORDS) and bool(word_set & _THIRD_PARTY_TOOLS)


def _names_framework(low: str) -> bool:
    """A catalogue framework ('compliant with Essential Eight') keeps the card."""
    from app.harness import ambiguity  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    return ambiguity.strip_offerings(low) != low


def clarify_eligible(  # pylint: disable=too-many-return-statements
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
    if _asks_integration_or_assurance(query):
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
        "- If none of the topics above actually address what the visitor is "
        "asking — even if their wording loosely overlaps a topic's words — "
        'set action to "answer" and write the plain decline instead of '
        f'picking one: "{NO_ANSWER_LINE}" with nothing else '
        "and no mention of a specialist (specialists are only offered for a "
        "genuine, published-but-missing detail, never for a question unrelated "
        "to us). Never offer a clarifying choice between topics that do "
        "not genuinely answer the question asked.\n"
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
    entries = (
        _from_answers(last, answers)
        if answers
        else (
            _match_text(last, query)
            or _match_entity(last, query)
            or _match_pointer(last, query)
        )
    )
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
    if (
        answers
        or not query.strip()
        or detect_conversational_intent(query) is not None
        # "!!!" / keyboard mash refines nothing: the junk template answers it.
        or is_junk_input(query)
    ):
        return None
    if _NOT_SURE_RE.search(_scan(query)) and len(query.split()) <= _MAX_POINTER_WORDS:
        everything = [o for q in last.questions for o in q.options]
        return ResolvedReply(
            query=compose_query(last.original_query, [_NOT_SURE_HINT]),
            original_query=last.original_query,
            answers=[_entry(last.questions[0], [], query.strip()[:_MAX_OTHER_CHARS])],
            document_ids=list(
                dict.fromkeys(d for o in everything for d in o.document_ids)
            ),
            free_text=True,
        )
    if _is_new_question(query):
        # "where are you based?": a standalone question is answered as such.
        return None
    # Free text of any length refines the original question; the card's
    # option pages are searched for it.
    offered = [o for q in last.questions for o in q.options]
    return ResolvedReply(
        query=compose_query(last.original_query, [_clip(query, _MAX_OTHER_CHARS)]),
        original_query=last.original_query,
        document_ids=list(dict.fromkeys(d for o in offered for d in o.document_ids)),
        free_text=True,
    )


_MAX_POINTER_WORDS = 8
_ORDINAL_INDEX = {
    "first": 0,
    "1st": 0,
    "one": 0,
    "second": 1,
    "2nd": 1,
    "two": 1,
    "third": 2,
    "3rd": 2,
    "three": 2,
    "fourth": 3,
    "4th": 3,
    "four": 3,
}
_POINTER_RE = re.compile(
    r"\b(?:(?:option|number|no\.?|#)\s{0,3}(\d{1,2})|(\d{1,2})(?:st|nd|rd|th)?|"
    r"(first|1st|second|2nd|third|3rd|fourth|4th|last|one|two|three|four))\b",
    re.IGNORECASE,
)
_NOT_SURE_RE = re.compile(
    r"\b(?:not\s{1,3}sure|unsure|don'?t\s{1,3}know|dont\s{1,3}know|dunno|no\s{1,3}idea|"
    r"idk|either|whichever|help\s{1,3}me\s{1,3}choose|you\s{1,3}(?:pick|choose|decide)|"
    r"any\s{1,3}of\s{1,3}them|all\s{1,3}of\s{1,3}them|both)\b",
    re.IGNORECASE,
)
_ASK_LEAD_RE = re.compile(
    r"^(?:what|who|where|when|why|how|which|is|are|do|does|can|could|will|would|"
    r"should|tell|explain)\b",
    re.IGNORECASE,
)


def _option_index(token: tuple[str, str, str], count: int) -> int | None:
    number = token[0] or token[1]
    if number:
        index = int(number) - 1
    else:
        word = token[2].lower()
        index = count - 1 if word == "last" else _ORDINAL_INDEX.get(word, -1)
    return index if 0 <= index < count else None


def _match_pointer(clarification: Clarification, text: str) -> list[dict[str, object]]:
    """ "2", "option 2", "the first one", "1, 3": numbers / ordinals -> options.

    Several pointers on a multi-question card map to its questions in order;
    on a single question they are its (multi-select) picks.
    """
    scan = _scan(text)
    if len(scan.split()) > _MAX_POINTER_WORDS:
        return []
    tokens = _POINTER_RE.findall(scan)
    if not tokens:
        return []
    questions = clarification.questions
    entries: list[dict[str, object]] = []
    if len(questions) == 1 or len(tokens) == 1:
        question = questions[0]
        picked = [
            question.options[i]
            for t in tokens
            if (i := _option_index(t, len(question.options))) is not None
        ]
        picked = list(dict.fromkeys(picked)) if question.multi_select else picked[:1]
        return [_entry(question, picked, None)] if picked else []
    for question, token in zip(questions, tokens, strict=False):
        index = _option_index(token, len(question.options))
        if index is not None:
            entries.append(_entry(question, [question.options[index]], None))
    return entries


def _match_entity(clarification: Clarification, text: str) -> list[dict[str, object]]:
    """ "e8" / "the zero trust one": the option whose label names the same service."""
    scan = _scan(text)
    keys = {k for _s, _e, k in entity_mentions(scan)}
    words = {w.lower() for w in _WORD_RE.findall(scan)} - _STOPWORDS - _GENERIC_WORDS
    entries: list[dict[str, object]] = []
    for question in clarification.questions:
        by_key = [
            o
            for o in question.options
            if keys and keys & {k for _s, _e, k in entity_mentions(o.label)}
        ]
        by_word = [
            o
            for o in question.options
            if words
            and words
            <= ({w.lower() for w in _WORD_RE.findall(o.label)} | _LABEL_FILLER)
        ]
        picked = by_key or by_word
        if picked and (len(picked) == 1 or question.multi_select):
            entries.append(_entry(question, picked, None))
    return entries


def _is_new_question(text: str) -> bool:
    """A standalone question that names its own subject (not an answer to the card)."""
    scan = _scan(text)
    if not (scan.endswith("?") or _ASK_LEAD_RE.match(scan)):
        return False
    rest = scan.split(" ", 1)[1] if " " in scan else ""
    return bool(
        entity_mentions(scan) or _PROPER_NOUN_RE.search(rest) or is_contact_ask(scan)
    )


def boost_documents(
    sources: list[SourceChunk], document_ids: list[str] | None
) -> list[SourceChunk]:
    """Chosen options' documents first (stable), so the answer leads with them."""
    if not document_ids:
        return sources
    preferred = set(document_ids)
    return sorted(sources, key=lambda s: s.document_id not in preferred)


# ---------------------------------------------------------------------------
# Deterministic clarification (owner rule, 2026-11-04). ONE mechanism, no LLM:
#   (a) the question matches knowledge-base content but is ambiguous across
#       services (generic words only, the covered evidence spreads over two or
#       more service pages with close cross-encoder scores) -> "which service?";
#   (b) buying intent without a named service -> "which service?".
# Never for a named service/product, a multi-part ask, an off-domain ask, a
# reply to a card, or past the consecutive-card cap. Options are the retrieved
# service pages only (labels from their page titles, non-empty document ids).
# ---------------------------------------------------------------------------

SERVICE_HEADER = "Service"
SERVICE_QUESTION = "Which service is your question about?"
BUYING_QUESTION = "Which service are you interested in?"
SERVICE_PREAMBLE = "Sure, the answer depends on the service you have in mind."
BUYING_PREAMBLE = "I'd be happy to help you get started."
REFERENCE_PREAMBLE = "I'm not sure which one you mean yet."
REFERENCE_REASON = "unresolved_reference"
# Catalogue entities (app.retrieval.reranker) that are services a visitor can
# ask about or buy; governance pages (Secure AI, Responsible AI, platform) are not.
SERVICE_ENTITY_KEYS = (
    "zero trust",
    "essential eight",
    "security baseline",
    "cis",
    "iso 27001",
    "iso 42001",
    "packages",
)
# Retrieval used for the buying card when the question's own evidence names
# fewer than two service pages (still retrieved KB documents, never a fixed list).
SERVICE_CATALOGUE_QUERY = (
    "Deploy IQ services: Zero Trust, Essential Eight, Microsoft 365 Security "
    "Baseline Assessment, CIS Benchmark, ISO 27001, ISO 42001, security packages"
)
_TITLE_SUFFIX_RE = re.compile(
    r"\s{0,3}[-|\u2013\u2014]\s{0,3}deploy\s{0,3}iq\b.{0,80}$", re.IGNORECASE
)
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][A-Za-z0-9]+\b")
# Microsoft products and brand words the KB covers: a bare "m365" / "intune"
# / "entra" gets an overview answer, never a card.
_PRODUCT_TERM_RE = re.compile(
    r"\b(?:m365|o365|microsoft[\s_-]{0,3}365|office[\s_-]{0,3}365|intune|entra|"
    r"azure[\s_-]{0,3}ad|defender|purview|copilot|sharepoint|exchange|teams|"
    r"onedrive|conditional[\s_-]{0,3}access|mfa|multi[\s_-]{0,3}factor|mdr|"
    r"acsc|starter|professional|baseline|foundation|strengthen|frontier)\b",
    re.IGNORECASE,
)
# Fallback buying detector (app.guardrails.intent supplies the real intent).
_BUYING_RE = re.compile(
    r"\b(?:price|prices|pricing|priced|cost|costs|costing|quote|quotation|"
    r"how\s{1,3}much|buy|buying|purchase|purchasing|sign\s{1,3}(?:me|us)\s{1,3}up|"
    r"subscribe|subscription|demo)\b",
    re.IGNORECASE,
)
_BUYING_WORDS = frozenset(
    [
        "price",
        "prices",
        "pricing",
        "priced",
        "cost",
        "costs",
        "costing",
        "quote",
        "quotation",
        "estimate",
        "much",
        "buy",
        "buying",
        "purchase",
        "purchasing",
        "sign",
        "up",
        "subscribe",
        "subscription",
        "demo",
        "trial",
        "plan",
        "plans",
        "want",
        "like",
        "get",
        "interested",
        "something",
        "service",
        "services",
        "pay",
        "paying",
        "charge",
        "charges",
        "fee",
        "fees",
    ]
)
_GENERIC_WORDS = frozenset(
    [
        "roadmap",
        "roadmaps",
        "typical",
        "typically",
        "usual",
        "usually",
        "security",
        "secure",
        "cyber",
        "cybersecurity",
        "compliance",
        "compliant",
        "comply",
        "audit",
        "audits",
        "auditing",
        "audited",
        "certified",
        "certification",
        "certify",
        "framework",
        "frameworks",
        "standard",
        "standards",
        "protect",
        "protection",
        "help",
        "assessment",
        "assessments",
        "assess",
        "risk",
        "risks",
        "posture",
        "services",
        "service",
        "options",
        "start",
        "started",
        "get",
        "long",
        "take",
        "takes",
        "time",
        "timeline",
        "duration",
        "implement",
        "implementation",
        "cost",
        "costs",
        "price",
        "pricing",
        "need",
        "needs",
        "work",
        "offer",
        "offers",
        "do",
        "does",
        "doing",
        "done",
        "ready",
    ]
)
_LABEL_FILLER = frozenset(
    [
        "the",
        "one",
        "option",
        "that",
        "this",
        "please",
        "service",
        "services",
        "page",
        "i",
        "mean",
    ]
)


@dataclass(frozen=True, slots=True)
class ServiceDoc:
    """A retrieved service page and its best relevance to the question."""

    document_id: str
    label: str
    score: float  # best cross-encoder logit (cosine x 10 when the reranker is off)
    source_name: str | None = None


@dataclass(frozen=True, slots=True)
class ClarifyDecision:
    """The deterministic clarification verdict for one message."""

    ask: bool = False
    buying: bool = False
    docs: tuple[ServiceDoc, ...] = ()  # the question's own service pages
    reason: str = "not_eligible"
    term: str = ""  # an ambiguous service term ("iso"): the card shows its pages only


def page_label(source: SourceChunk) -> str:
    """The page title without the site suffix ("Essential Eight Compliance")."""
    metadata = source.metadata or {}
    title = (
        source.title
        or metadata_text(metadata, "page_title", "title")
        or humanize_source_name(metadata_text(metadata, "source_name") or "")
    )
    label = _TITLE_SUFFIX_RE.sub("", title).strip(" -|")
    return _clip(label or title, _MAX_LABEL_CHARS + 20)


def _service_key(source: SourceChunk) -> str | None:
    metadata = source.metadata or {}
    name = f"{metadata_text(metadata, 'source_name') or ''} {source.title or ''}"
    for key in SERVICE_ENTITY_KEYS:
        if entity_documents([key], {source.document_id: name}):
            return key
    return None


def service_documents(
    sources: list[SourceChunk], ce_by_chunk: dict[str, float] | None = None
) -> list[ServiceDoc]:
    """Distinct service pages among ``sources``, most relevant first."""
    best: dict[str, ServiceDoc] = {}
    for source in sources:
        if _service_key(source) is None:
            continue
        score = (ce_by_chunk or {}).get(source.chunk_id, source.score * 10)
        current = best.get(source.document_id)
        if current is None or score > current.score:
            best[source.document_id] = ServiceDoc(
                document_id=source.document_id,
                label=page_label(source),
                score=score,
                source_name=metadata_text(source.metadata or {}, "source_name"),
            )
    return sorted(best.values(), key=lambda d: (-d.score, d.label))


def is_buying(query: str) -> bool:
    """Fallback buying-intent check (price, quote, buy, demo, sign up)."""
    return _BUYING_RE.search(_scan(query)) is not None


def names_product(query: str) -> bool:
    """A named service, framework, Microsoft product or the brand."""
    scan = _scan(query)
    return bool(
        entity_mentions(scan) or _PRODUCT_TERM_RE.search(scan) or BRAND_RE.search(scan)
    )


def names_service(query: str, history: list[ChatTurn] | None) -> bool:
    """The service is known: named now, or the conversation is about one.

    A bare service-family word ("m365") names several services, not one.
    """
    scan = _scan(query)
    named = bool(entity_mentions(scan) or _PRODUCT_TERM_RE.search(scan))
    if named and family_term(query) is not None:
        named = False
    return named or history_subject(history) is not None


def _content_words(query: str) -> list[str]:
    return [
        w
        for w in (t.lower() for t in _WORD_RE.findall(_scan(query)))
        if w not in _STOPWORDS
    ]


def _is_generic(query: str) -> bool:
    """Every content word is generic ("security", "how do I get compliant?")."""
    words = _content_words(query)
    return bool(words) and all(w in _GENERIC_WORDS or w in _VAGUE_WORDS for w in words)


# Owner rule (2026-11-04): a short term naming 2+ catalogue services by their
# page names ("iso" -> ISO 27001 + ISO 42001, "assessment" -> Security
# Baseline + CIS) gets a card with exactly those services; one match is answered.
_TERM_FILLER = _STOPWORDS | frozenset(
    [
        "explain",
        "describe",
        "overview",
        "info",
        "information",
        "more",
        "details",
        "detail",
        "give",
        "know",
        "any",
        "anything",
        "guys",
        "offer",
        "have",
        "got",
        "us",
        # Chatty padding around a bare term ("iso stuff", "m365 help",
        # "assessments pls", "what's your iso offering").
        "stuff",
        "things",
        "thing",
        "something",
        "help",
        "pls",
        "plz",
        "offering",
        "offerings",
        "options",
        "option",
        "wanna",
        "want",
        "need",
        "interested",
        "looking",
        "show",
        "see",
        "list",
        "all",
    ]
)
# Words in nearly every service page name: never a service family.
_BROAD_TERMS = frozenset(
    ["security", "microsoft", "365", "m365", "service", "services", "deploy", "iq"]
)
_MAX_TERM_WORDS = 2
_NAME_SEP_RE = re.compile(r"[-_./|]+")


# Bare words naming a family of catalogue services that their page names do
# not carry as such (owner rule, stress test 2026-11-04): term -> page-name
# pattern. "m365" -> every Microsoft 365 service, "plans" -> the Zero Trust
# plans and the packages, "certification" -> the certifiable frameworks.
_M365_FAMILY = re.compile(r"\b(?:microsoft 365|m365)\b")
_TERM_FAMILIES: dict[str, re.Pattern[str]] = {
    "m365": _M365_FAMILY,
    "o365": _M365_FAMILY,
    "microsoft 365": _M365_FAMILY,
    "office 365": _M365_FAMILY,
    "plan": re.compile(r"\b(?:packages?|zero trust)\b"),
    "plans": re.compile(r"\b(?:packages?|zero trust)\b"),
    "certification": re.compile(r"\b(?:iso|essential eight)\b"),
    "certifications": re.compile(r"\b(?:iso|essential eight)\b"),
}


def _term_words(query: str) -> list[str]:
    words = [w.lower() for w in _WORD_RE.findall(_scan(query))]
    return [w for w in words if w not in _TERM_FILLER]


def family_term(query: str) -> str | None:
    """The bare service-family word of a short ask ("m365", "plans")."""
    term = " ".join(_term_words(query))
    return term if term in _TERM_FAMILIES else None


def ambiguous_term(query: str) -> str | None:
    """The bare service term of a short ask ("tell me about iso" -> "iso")."""
    family = family_term(query)
    if family is not None:
        return family
    rest = _term_words(query)
    if not rest or len(rest) > _MAX_TERM_WORDS or any(w in _BROAD_TERMS for w in rest):
        return None
    return " ".join(rest)


def _names_term(doc: ServiceDoc, term: str) -> bool:
    name = _NAME_SEP_RE.sub(" ", f"{doc.label} {doc.source_name or ''}".lower())
    family = _TERM_FAMILIES.get(term)
    if family is not None:
        return family.search(name) is not None
    stem = term[:-1] if term.endswith("s") and len(term) > 3 else term  # noqa: PLR2004
    return re.search(rf"\b{re.escape(stem)}s?\b", name) is not None


def term_documents(term: str, docs: list[ServiceDoc]) -> list[ServiceDoc]:
    """The service pages whose name carries ``term`` (order kept, deduped)."""
    seen: set[str] = set()
    found = []
    for doc in docs:
        if doc.document_id not in seen and _names_term(doc, term):
            seen.add(doc.document_id)
            found.append(doc)
    return found


def service_card(
    query: str, decision: ClarifyDecision, catalogue: list[ServiceDoc]
) -> Clarification | None:
    """The "which service?" card: the question's own service pages first, then
    the other retrieved service pages, at most ``clarify_max_options``."""
    settings = get_settings()
    docs = list(decision.docs)
    seen = {d.document_id for d in docs}
    docs += [d for d in catalogue if d.document_id not in seen]
    if decision.term:
        docs = term_documents(decision.term, docs)
        if (
            len(docs) > settings.clarify_max_options
            and decision.term not in _TERM_FAMILIES
        ):
            return None  # too broad to be one family of services
    options = [
        ClarificationOption(
            id=f"O{i}",
            label=doc.label,
            document_ids=[doc.document_id],
            sources=[doc.source_name] if doc.source_name else [],
        )
        for i, doc in enumerate(docs[: settings.clarify_max_options], 1)
    ]
    if len(options) < settings.clarify_min_options:
        return None
    if decision.buying:
        preamble = BUYING_PREAMBLE
    elif decision.reason == REFERENCE_REASON:
        preamble = REFERENCE_PREAMBLE
    else:
        preamble = SERVICE_PREAMBLE
    return Clarification(
        preamble=preamble,
        questions=[
            ClarificationQuestion(
                id="Q1",
                header=SERVICE_HEADER,
                question=BUYING_QUESTION if decision.buying else SERVICE_QUESTION,
                options=options,
            )
        ],
        original_query=query[:_MAX_QUERY_CHARS],
    )


def _concentrated(docs: list[ServiceDoc]) -> bool:
    """One service page clearly leads the evidence."""
    settings = get_settings()
    if not docs or docs[0].score < settings.retrieval_ce_confident_min:
        return False
    return len(docs) == 1 or docs[0].score - docs[1].score >= settings.clarify_ce_margin


def clarify_allowed(
    query: str,
    history: list[ChatTurn] | None,
    *,
    clarification_reply: bool,
    parts: int,
) -> bool:
    """Cheap pre-retrieval check: may this message get a card at all?"""
    settings = get_settings()
    return (
        settings.clarify_enabled
        and not clarification_reply
        and parts == 1
        and clarification_streak(history) < settings.clarify_max_consecutive
        and not names_service(query, history)
    )


_DANGLING_RE = re.compile(r"\b(?:it|its|it's|they|them|this|that|those|these)\b", re.I)


def decide_clarification(  # pylint: disable=too-many-arguments,too-many-locals,too-many-return-statements,too-many-branches
    query: str,
    history: list[ChatTurn] | None,
    *,
    sources: list[SourceChunk],
    ce_by_chunk: dict[str, float],
    max_ce: float | None,
    route: str,
    buying: bool,
    clarification_reply: bool,
    parts: int,
) -> ClarifyDecision:
    """Ask "which service?" only under the owner rule; otherwise answer.

    ``route`` is the gate's verdict (``app.harness.decision.Route`` value).
    A positive decision carries the question's own service pages; the card
    is completed by ``service_card`` with the retrieved service catalogue.
    """
    settings = get_settings()
    if not clarify_allowed(
        query, history, clarification_reply=clarification_reply, parts=parts
    ):
        return ClarifyDecision(reason="not_eligible")
    docs = [
        d
        for d in service_documents(sources, ce_by_chunk)
        if d.score >= settings.retrieval_ce_relevant_min
    ]
    if buying:
        if BRAND_RE.search(_scan(query)):
            # "a demo of DeployIQ": the whole product, nothing to choose.
            return ClarifyDecision(reason="buying_brand")
        residual = [w for w in _content_words(query) if w not in _BUYING_WORDS]
        relevant = max_ce is not None and max_ce >= settings.retrieval_ce_relevant_min
        if residual and route == "answer" and (relevant or _concentrated(docs)):
            # "is it free?", "how do I book a consultation?": the evidence
            # already answers it.
            return ClarifyDecision(reason="buying_answerable")
        if route == "off_topic" and residual:
            return ClarifyDecision(reason="off_topic")
        return ClarifyDecision(
            ask=True, buying=True, docs=tuple(docs), reason="buying_without_service"
        )
    if not history and is_bare_reference(query):
        # "what about the second one?" in a new chat: which service?
        return ClarifyDecision(ask=True, docs=tuple(docs), reason=REFERENCE_REASON)
    term = ambiguous_term(query)
    if term is not None and route != "off_topic":
        matched = term_documents(term, service_documents(sources, ce_by_chunk))
        # A specific term needs one retrieved match to look (the card is
        # completed from every service page and dropped unless 2+ carry it);
        # a generic word ("compliance") keeps the generic card below otherwise;
        # a family word ("plans") is completed from the catalogue alone.
        if term in _TERM_FAMILIES:
            needed = 0
        elif _is_generic(query):
            needed = settings.clarify_min_options
        else:
            needed = 1
        if len(matched) >= needed:
            return ClarifyDecision(
                ask=True, docs=tuple(matched), reason="ambiguous_term", term=term
            )
    if names_product(query) or is_company_or_generic(query) or not _is_generic(query):
        return ClarifyDecision(reason="specific")
    # "how long does it take?" with nothing to point at: which service?
    dangling = _DANGLING_RE.search(_scan(query)) is not None
    if not dangling and route != "answer":
        return ClarifyDecision(reason="not_in_kb")
    if not dangling and _concentrated(docs):
        return ClarifyDecision(reason="one_service")
    return ClarifyDecision(
        ask=True,
        docs=tuple(docs),
        reason="dangling_reference" if dangling else "ambiguous_across_services",
    )


async def catalogue_documents(
    vector_store: Any, part: Any, filters: dict | None
) -> list[ServiceDoc]:
    """Every service page, retrieved with the question's own embedding (its
    best chunk each), ranked by the cross-encoder against the question."""
    if not getattr(part, "embedding", None):
        return []
    found = await vector_store.best_chunk_per_entity_document(
        list(part.embedding), list(SERVICE_ENTITY_KEYS), filters
    )
    logits: dict[Any, float] = {}
    if found:
        await rerank_async(part.search or part.query, found, None, logits)
    sources = [
        SourceChunk(
            chunk_id=str(chunk.id),
            document_id=str(chunk.document_id),
            content=chunk.content,
            score=score,
            metadata=chunk.chunk_metadata or {},
            title=getattr(
                vector_store.document_links.get(chunk.document_id), "title", None
            ),
        )
        for chunk, score in found
    ]
    return service_documents(sources, {str(k): v for k, v in logits.items()})
