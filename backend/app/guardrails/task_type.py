"""Task-type policy checks run before retrieval (owner templates).

Some messages must get an approved template whatever the knowledge base
holds, so the retrieval gate never gets to call them off-domain or not
covered:

- a credential the visitor pasted ("our admin password is X, is it strong?")
  -> credentials template;
- a question about the assistant itself the anchored identity probe misses
  ("which LLM powers you?") -> identity template;
- a creative-writing, translation, or personal recommendation / travel /
  shopping task -> off-topic template, even when it names a KB topic
  ("write a haiku about Zero Trust");
- a clear-cut regulated professional-advice ask (medication, crypto, "is it
  legal to ...", "can I fire ...") -> regulated-advice template.

Questions about what our pages or services say ("is DeployIQ liable ...?",
"do your prices include GST?") are never matched: they are retrieved.
A message mixing a regulated ask with a separate KB question is left to
retrieval (the gate decline path still applies the regulated template).
Every pattern runs on a length-capped text with bounded quantifiers only.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from enum import StrEnum

from app.core.config import get_settings
from app.guardrails.conversational import (
    IDENTITY_POLICY,
    REGULATED_POLICY,
    is_identity_probe,
)

_MAX_CHARS = 300
_MAX_IDENTITY_CHARS = 120
_MIN_CLAUSE_WORDS = 2

CREDENTIALS_POLICY = ("credentials", "no_permission")
OFF_TOPIC_POLICY = ("off_topic", "off_topic")


class TaskType(StrEnum):
    """A message's task when it has a fixed policy reply."""

    SHARED_CREDENTIAL = "shared_credential"
    IDENTITY = "identity"
    CREATIVE = "creative"
    TRANSLATION = "translation"
    RECOMMENDATION = "recommendation"
    REGULATED = "regulated"


_POLICY_BY_TASK: dict[TaskType, tuple[str, str]] = {
    TaskType.SHARED_CREDENTIAL: CREDENTIALS_POLICY,
    TaskType.IDENTITY: IDENTITY_POLICY,
    TaskType.CREATIVE: OFF_TOPIC_POLICY,
    TaskType.TRANSLATION: OFF_TOPIC_POLICY,
    TaskType.RECOMMENDATION: OFF_TOPIC_POLICY,
    TaskType.REGULATED: REGULATED_POLICY,
}

# A question about us or our pages: retrieved, never a task-type refusal.
_ABOUT_US_RE = re.compile(
    r"\b(?:your|ur|yours|deploy\s{0,2}iq|askdiq|exigo(?:\s{0,2}tech)?)\b"
    r"|\b(?:do|does|can|will|would)\s{1,3}(?:you|u)\s{1,3}(?:offer|provide|"
    r"support|cover|guarantee|sell|include|recommend\s{1,3}(?:a|any)\s{1,3}"
    r"(?:package|service))\b"
    r"|\bwhat\s{1,3}(?:does|do|is|are)\b[^?]{0,80}?\b(?:say|says|cover|covers|"
    r"mention|mentions|state|states)\b"
    r"|\b(?:on|in)\s{1,3}(?:the|this)\s{1,3}(?:website|site|page|pages)\b",
    re.IGNORECASE,
)

# --- shared credential -----------------------------------------------------
_CRED_NOUN = (
    r"(?:pass(?:word|code|phrase)|pwd|pin|api[\s_-]{0,2}key|secret(?:\s{1,3}key)?|"
    r"access\s{1,3}token|credentials?|login(?:\s{1,3}details)?|mfa\s{1,3}code)"
)
_SHARED_CREDENTIAL_RE = re.compile(
    # "our admin password is X", "my Microsoft 365 admin password: X"
    rf"\b(?:my|our|the)\s{{1,3}}(?:[\w-]{{1,20}}\s{{1,3}}){{0,3}}{_CRED_NOUN}s?"
    r"\s{0,3}(?:is|was|=|:)\s{0,3}[\"'`]?(?=[^\s\"'`]{0,40}[\d!@#$%^&*_])"
    r"[^\s\"'`]{4,60}"
    # "here's my password ...", "here is our API key ..."
    rf"|\bhere(?:'s|\s{{1,3}}is|\s{{1,3}}are)\s{{1,3}}(?:my|our|the)\s{{1,3}}"
    rf"(?:[\w-]{{1,20}}\s{{1,3}}){{0,3}}{_CRED_NOUN}s?\b"
    # "is the password X strong enough?"
    rf"|\bis\s{{1,3}}(?:the\s{{1,3}}|this\s{{1,3}}|my\s{{1,3}}|our\s{{1,3}})?"
    rf"{_CRED_NOUN}\s{{1,3}}[\"'`]?\S{{4,60}}[\"'`]?\s{{1,3}}"
    r"(?:strong|secure|safe|good|ok(?:ay)?)\b",
    re.IGNORECASE,
)

# --- identity (beyond the anchored probe) ----------------------------------
_YOU = r"(?:you|u)"
_IDENTITY_EXTRA_RE = re.compile(
    rf"\b(?:which|what)\s{{1,3}}(?:llm|ai|model|ai\s{{1,3}}model|language\s{{1,3}}"
    rf"model|gpt|engine)\s{{1,3}}(?:powers|runs|drives|is\s{{1,3}}behind|"
    rf"are|is\s{{1,3}}powering|do)\s{{1,3}}{_YOU}\b"
    rf"|\b(?:are|r)\s{{1,3}}{_YOU}\s{{1,3}}(?:using\s{{1,3}}|based\s{{1,3}}on\s{{1,3}}|"
    r"powered\s{1,3}by\s{1,3})?(?:chat\s{0,2}gpt|gpt[\s-]{0,2}\d{0,2}o?|claude|gemini|"
    r"llama|openai|copilot|bard)\b"
    # "who owns you guys?" asks about the company: retrieved.
    rf"|\bwho\s{{1,3}}(?:built|made|created|trained|programmed)\s{{1,3}}{_YOU}\b"
    r"(?!\s{1,3}(?:guys|folks|people|all))"
    rf"|^\s{{0,3}}(?:do|will|would|can)\s{{1,3}}{_YOU}\s{{1,3}}(?:still\s{{1,3}})?"
    r"remember\s{1,3}(?:me|us|who\s{1,3}i\s{1,3}am|what\s{1,3}(?:we|i)\s{1,3}"
    r"(?:talked|spoke|chatted|discussed|said)|(?:our|my)\s{1,3}(?:last|previous|"
    r"earlier)|anything)\b"
    rf"|^\s{{0,3}}(?:do|does|can)\s{{1,3}}{_YOU}\s{{1,3}}(?:ever\s{{1,3}})?"
    r"(?:get|feel)\s{1,3}(?:tired|bored|lonely|sad|angry|annoyed|frustrated|stressed)\b",
    re.IGNORECASE,
)

# --- creative writing --------------------------------------------------------
_CREATIVE_FORM = (
    r"(?:poem|haiku|limerick|sonnet|ode|story|song|rap|jingle|joke|riddle|"
    r"ballad|verse|rhyme|fairy\s{0,2}tale|bedtime\s{1,3}story|pun|knock[\s-]knock)"
)
_CREATIVE_RE = re.compile(
    r"\b(?:write|compose|create|make\s{1,3}up|make|give|tell|sing|draft|come\s{1,3}"
    r"up\s{1,3}with|generate)\s{1,3}(?:me\s{1,3}|us\s{1,3})?(?:a|an|some|another|"
    r"one|the)?\s{0,3}(?:(?:short|funny|little|quick|silly|cute|rhyming|good)"
    rf"\s{{1,3}}){{0,2}}{_CREATIVE_FORM}s?\b"
    rf"|\b{_CREATIVE_FORM}s?\s{{1,3}}(?:about|on|for)\b",
    re.IGNORECASE,
)
# "a customer success story", "tell me the story behind DeployIQ": a KB ask.
_STORY_KB_RE = re.compile(
    r"\b(?:success|customer|client|case)\s{1,3}stor(?:y|ies)\b|\bcase\s{1,3}stud",
    re.IGNORECASE,
)

# --- translation -------------------------------------------------------------
_LANGUAGE = (
    r"(?:hindi|french|spanish|german|japanese|chinese|mandarin|cantonese|italian|"
    r"portuguese|arabic|korean|russian|vietnamese|thai|indonesian|greek|dutch|"
    r"turkish|punjabi|tamil|telugu|urdu|bengali|gujarati|marathi|tagalog|"
    r"filipino|polish|swedish|norwegian|danish|finnish|latin|hebrew|malay|"
    r"persian|farsi|nepali|sinhala|swahili|ukrainian|english|maori|auslan)"
)
_TRANSLATION_RE = re.compile(
    rf"\btranslat(?:e|ion|ing)\b[^?]{{0,80}}?\b(?:into|to|in)\s{{1,3}}{_LANGUAGE}\b"
    rf"|^\s{{0,3}}(?:(?:can|could|would)\s{{1,3}}{_YOU}\s{{1,3}}(?:please\s{{1,3}})?|"
    r"please\s{1,3})?translate\b"
    rf"|\bhow\s{{1,3}}(?:do|would|can|should)\s{{1,3}}(?:you|i|we|one)\s{{1,3}}"
    rf"(?:say|write|spell|pronounce)\b[^?]{{1,60}}?\bin\s{{1,3}}{_LANGUAGE}\b"
    rf"|\b(?:say|write)\s{{1,3}}[^?]{{1,60}}?\bin\s{{1,3}}{_LANGUAGE}\b"
    rf"|\bwhat(?:'s|\s{{1,3}}is)\s{{1,3}}[^?]{{1,40}}?\bin\s{{1,3}}{_LANGUAGE}\s{{0,3}}\??$",
    re.IGNORECASE,
)
# "explain Essential Eight in plain English" is a KB ask.
_PLAIN_LANGUAGE_RE = re.compile(
    r"\b(?:plain|simple|everyday|layman'?s?|non[\s-]?technical)\s{1,3}"
    r"(?:english|terms|language|words)\b",
    re.IGNORECASE,
)

# --- personal recommendation / travel / shopping ----------------------------
_THING = (
    r"(?:restaurants?|cafes?|bars?|pubs?|hotels?|motels?|airbnbs?|movies?|films?|"
    r"books?|novels?|tv\s{1,3}shows?|series|podcasts?|holidays?|vacations?|"
    r"destinations?|beaches?|laptops?|phones?|smartphones?|cars?|gifts?|"
    r"presents?|recipes?|shoes|sneakers|clothes|outfits?|games?|wines?|"
    r"flights?|places?\s{1,3}to\s{1,3}(?:eat|stay|visit|go))"
)
_RECOMMENDATION_RE = re.compile(
    rf"\b(?:recommend|suggest)\s{{1,3}}(?:me\s{{1,3}})?(?:a|an|some|any|the|good|"
    rf"nice|cheap|best)?\s{{0,3}}(?:(?:good|nice|cheap|great|decent)\s{{1,3}})?{_THING}\b"
    rf"|\b(?:best|good|cheap|cheapest|nice)\s{{1,3}}{_THING}\s{{1,3}}(?:in|near|for|around|under)\b"
    r"|\b(?:plan|book)\s{1,3}(?:a|my|our|me\s{1,3}a)\s{1,3}(?:trip|holiday|vacation|"
    r"flight|getaway|itinerary)\b|\bitinerary\b"
    # "what should I buy, microsoft laptops?" keeps its goal card.
    r"|\bwhat\s{1,3}should\s{1,3}i\s{1,3}(?:watch|read|eat|cook|wear)\b",
    re.IGNORECASE,
)

# --- regulated professional advice (clear-cut asks only) ---------------------
_MEDICAL_RE = re.compile(
    r"\b(?:dose|dosage|medications?|medicines?|antidepressants?|antibiotics?|"
    r"ibuprofen|paracetamol|panadol|nurofen|aspirin|prescri(?:be|ption)s?|"
    r"cancerous|cancer|tumou?r|mole|rash|chest\s{1,3}pain|pregnan(?:t|cy)|"
    r"tablets?\s{1,3}(?:should|a\s{1,3}day)|diagnos(?:e|is)\s{1,3}(?:me|my))\b",
    re.IGNORECASE,
)
_FINANCIAL_RE = re.compile(
    r"\b(?:crypto(?:currency|currencies)?|bitcoin|ethereum|stocks?|"
    r"(?:buy|sell|selling|buying)\s{1,3}shares|share\s{1,3}market|"
    r"(?:my|our)\s{1,3}super(?:annuation)?|super\s{1,3}fund|retirement\s{1,3}savings|"
    r"cash\s{1,3}reserves|mortgage|home\s{1,3}loan|investment\s{1,3}portfolio|"
    r"(?:allocate|invest)\s{1,3}(?:my|our)\s{1,3}(?:[\w']{1,20}\s{1,3}){0,2}"
    r"(?:cash|money|funds|savings|reserves))\b",
    re.IGNORECASE,
)
_TAX_RE = re.compile(
    r"\b(?:gst|capital\s{1,3}gains|tax\s{1,3}(?:rate|rates|return|deduction|"
    r"deductions|deductible|bracket|offset|refund|advice|bill)|income\s{1,3}tax|"
    r"company\s{1,3}tax|payroll\s{1,3}tax|fringe\s{1,3}benefits?)\b",
    re.IGNORECASE,
)
_LEGAL_HR_RE = re.compile(
    r"^\s{0,3}(?:is|are)\s{1,3}(?:it|this|that)\s{1,3}(?:legal|lawful|illegal|"
    r"unlawful|allowed\s{1,3}by\s{1,3}law)\b"
    r"|\bcan\s{1,3}(?:i|we|my\s{1,3}company|our\s{1,3}company)\s{1,3}(?:legally\s{1,3})?"
    r"(?:sue|fire|dismiss|sack|terminate|discipline)\b"
    r"|\bcan\s{1,3}(?:i|we)\s{1,3}(?:legally\s{1,3})?(?:read|monitor|access|search|check)"
    r"\s{1,3}(?:an?\s{1,3}|my\s{1,3}|our\s{1,3})?(?:employees?|staff|workers?)(?:'s|'|s')?"
    r"\s{1,3}(?:personal|private)\b"
    r"|\b(?:draft|write|prepare)\s{1,3}(?:me\s{1,3}|us\s{1,3})?(?:an?\s{1,3}|the\s{1,3}|"
    r"my\s{1,3}|our\s{1,3})?(?:non[\s-]{0,2}disclosure|nda|contract|employment\s{1,3}"
    r"contract|agreement|will|lease|legal\s{1,3}letter)\b"
    # the visitor's own legal obligation ("is my company legally required to
    # comply with the Essential Eight?", "are we required by law to ...")
    r"|\b(?:(?:my|our)\s{1,3}(?:[\w-]{1,20}\s{1,3}){0,2}|we\s{1,3}|i\s{1,3})"
    r"(?:is\s{1,3}|are\s{1,3}|am\s{1,3})?(?:legally|lawfully)\s{1,3}"
    r"(?:required|obliged|obligated|bound|mandated)\b"
    r"|\b(?:(?:my|our)\s{1,3}[\w-]{1,20}|we|i)\s{1,3}(?:is\s{1,3}|are\s{1,3}|am\s{1,3})?"
    r"required\s{1,3}by\s{1,3}law\b"
    r"|\bnotice\s{1,3}period\b|\bfair\s{1,3}work\b|\bunfair\s{1,3}dismissal\b"
    r"|\bredundancy\s{1,3}pay\b|\bminimum\s{1,3}wage\b|\bworkers'?\s{1,3}comp",
    re.IGNORECASE,
)
_INSURANCE_RE = re.compile(
    r"\bhow\s{1,3}much\s{1,3}(?:cyber\s{1,3})?insurance\b"
    r"|\binsurance\s{1,3}cover(?:age)?\s{1,3}(?:do|does|should|would)\b"
    r"|\b(?:tell|inform|notify|disclose\s{1,3}to)\s{1,3}(?:my|our|the)\s{1,3}insurer\b"
    r"|\bwill\s{1,3}(?:my|our|the)\s{1,3}insur(?:er|ance)\s{1,3}(?:pay|cover)\b"
    r"|\bwhich\s{1,3}(?:cyber\s{1,3})?insurance\s{1,3}(?:policy|provider|company)\b",
    re.IGNORECASE,
)
_REGULATED_RES = (_MEDICAL_RE, _FINANCIAL_RE, _TAX_RE, _LEGAL_HR_RE, _INSURANCE_RE)
# Clause boundaries of a multi-part message ("..., and what is Zero Trust?").
_CLAUSE_SPLIT_RE = re.compile(
    r"[?!;]\s{0,3}|\.\s{1,3}|,?\s{1,3}(?:and\s{1,3}also|also|plus)\s{1,3}"
    r"|,?\s{1,3}and\s{1,3}(?=(?:what|how|which|who|where|when|why|do|does|can|"
    r"is|are)\b)",
    re.IGNORECASE,
)


# ... plus ", then" ("tell me a joke, then tell me what Frontier includes").
_CREATIVE_SPLIT_RE = re.compile(
    rf"{_CLAUSE_SPLIT_RE.pattern}|,\s{{0,3}}(?:and\s{{1,3}})?then\s{{1,3}}"
    r"|,\s{0,3}(?=(?:what|how|which|who|where|when|why|does|do|can|is|are)\b)",
    re.IGNORECASE,
)


def _bounded(query: str) -> str | None:
    text = " ".join(query.split())
    if not text or len(text) > _MAX_CHARS:
        return None
    return text


def _about_us(text: str) -> bool:
    brand = get_settings().brand_name.strip().lower()
    return (
        bool(brand and brand in text.lower()) or _ABOUT_US_RE.search(text) is not None
    )


def _is_regulated_clause(text: str) -> bool:
    return any(pattern.search(text) for pattern in _REGULATED_RES)


def _is_regulated(text: str) -> bool:
    """Every clause of the message is a clear regulated-advice ask."""
    if _about_us(text):
        return False
    clauses = [
        c for c in _CLAUSE_SPLIT_RE.split(text) if len(c.split()) >= _MIN_CLAUSE_WORDS
    ]
    return bool(clauses) and all(_is_regulated_clause(c) for c in clauses)


def _is_identity(text: str) -> bool:
    if len(text) > _MAX_IDENTITY_CHARS:
        return False
    return is_identity_probe(text) or _IDENTITY_EXTRA_RE.search(text) is not None


def _is_creative(text: str) -> bool:
    """Every clause is a creative ask: "tell me a joke, then tell me what
    Frontier includes" is retrieved (the joke part gets the off-topic line)."""
    if _CREATIVE_RE.search(text) is None or _STORY_KB_RE.search(text) is not None:
        return False
    clauses = [
        c for c in _CREATIVE_SPLIT_RE.split(text) if len(c.split()) >= _MIN_CLAUSE_WORDS
    ]
    return all(_CREATIVE_RE.search(c) is not None for c in clauses)


def _is_translation(text: str) -> bool:
    return (
        _TRANSLATION_RE.search(text) is not None
        and _PLAIN_LANGUAGE_RE.search(text) is None
    )


def _is_recommendation(text: str) -> bool:
    return _RECOMMENDATION_RE.search(text) is not None and not _about_us(text)


def _is_shared_credential(text: str) -> bool:
    return _SHARED_CREDENTIAL_RE.search(text) is not None


# Checked in this order; the first match wins.
_CHECKS: tuple[tuple[TaskType, Callable[[str], bool]], ...] = (
    (TaskType.SHARED_CREDENTIAL, _is_shared_credential),
    (TaskType.IDENTITY, _is_identity),
    (TaskType.CREATIVE, _is_creative),
    (TaskType.TRANSLATION, _is_translation),
    (TaskType.RECOMMENDATION, _is_recommendation),
    (TaskType.REGULATED, _is_regulated),
)


def detect_task_type(query: str) -> TaskType | None:
    """The message's policy task type, or None (retrieve and answer as usual).

    Priority: a pasted credential, identity, creative / translation /
    recommendation (off-topic, even when the subject is a KB topic), then a
    clear-cut regulated-advice ask.
    """

    text = _bounded(query)
    if text is None:
        return None
    return next((task for task, test in _CHECKS if test(text)), None)


def task_type_policy(query: str) -> tuple[str, str] | None:
    """(template id, guardrail flag) of a pre-retrieval task-type refusal."""

    task = detect_task_type(query)
    return None if task is None else _POLICY_BY_TASK[task]
