"""Detection of conversational small-talk (greetings/thanks/capability asks).

These aren't real questions about DeployIQ content, so routing them through
retrieval + the LLM would either waste a call or get rejected as off-topic.
Handled here with canned, deterministic replies instead.
"""

import hashlib
import re

from app.core.config import get_settings
from app.guardrails import intent_examples as ex
from app.harness.prompt_catalog import (
    PERSONA_CAPABILITIES,
    PERSONA_CREATOR,
    PERSONA_HUMAN,
    PERSONA_LANGUAGES,
    PERSONA_MEMORY,
    PERSONA_MODEL,
    PERSONA_NAME,
    PERSONA_PRIVACY,
    PERSONA_PURPOSE,
    PERSONA_TEMPLATES,
    POLICY_IDENTITY,
    POLICY_REGULATED,
    POLICY_TEMPLATE_IDS,
    brand_values,
    persona_template,
    persona_template_id,
    policy_template,
)

# Approved POLICY template ids (app.harness.prompt_catalog.POLICY_TEMPLATES).
IDENTITY_TEMPLATE = POLICY_TEMPLATE_IDS[POLICY_IDENTITY]
REGULATED_TEMPLATE = POLICY_TEMPLATE_IDS[POLICY_REGULATED]
# (template id, guardrail flag) of a pre-LLM policy refusal.
IDENTITY_POLICY = (IDENTITY_TEMPLATE, "identity_probe")
REGULATED_POLICY = (REGULATED_TEMPLATE, "regulated_advice")

_GREETING_WORD = (
    r"(?:hi+|hello+|hey+|hiya|yo|howdy|greetings|sup|what'?s\s*up|"
    r"good\s*(?:morning|afternoon|evening|day)|there|again|team|guys|"
    r"folks|everyone)"
)

# Matches one or more greeting words/phrases in a row (e.g. "hi good
# morning", "hey what's up"), not just a single isolated phrase.
_GREETING_RE = re.compile(
    rf"^\s*{_GREETING_WORD}(?:[\s!.,]+{_GREETING_WORD})*[\s!.,]*$",
    re.IGNORECASE,
)

# Conversational fillers are short; anything longer is a real question. Every
# pattern below is matched only after a length cap and uses bounded quantifiers.
_MAX_CONVERSATIONAL_CHARS = 80

_THANKS_RE = re.compile(
    r"^\s{0,3}(?:(?:ok(?:ay)?|great|cool|perfect|awesome|nice|brilliant|"
    r"got\s{0,2}it|good|alright)[\s!.,]{1,4})?"
    r"(?:thanks?|thank\s{0,2}(?:you|u)|thx|ty|cheers|appreciate\s{0,2}it)"
    r"(?:\s{1,3}(?:a\s{1,3}lot|so\s{1,3}much|very\s{1,3}much|heaps|a\s{1,3}ton|"
    r"again|mate|team|guys|everyone|"
    r"for\s{1,3}(?:your\s{1,3})?(?:the\s{1,3})?(?:help|that|info(?:rmation)?)))?"
    r"[\s!.,]{0,6}$",
    re.IGNORECASE,
)

_FAREWELL_RE = re.compile(
    r"^\s{0,3}(?:bye(?:\s{0,2}bye)?|goodbye|see\s{0,2}you(?:\s{1,3}(?:later|soon))?|"
    r"see\s{0,2}ya|later|cya|catch\s{1,3}you\s{1,3}later|good\s{0,2}night|"
    r"take\s{1,3}care)[\s!.,]{0,6}$",
    re.IGNORECASE,
)

_CAPABILITY_RE = re.compile(
    r"^\s{0,3}(?:what\s{0,2}can\s{0,2}(?:you|u)\s{0,2}do|"
    r"how\s{0,2}can\s{0,2}(?:you|u)\s{0,2}help(?:\s{1,3}me)?|"
    r"what\s{0,2}do\s{0,2}(?:you|u)\s{0,2}do|"
    r"what\s{0,2}are\s{0,2}(?:you|u)(?:\s{1,3}able\s{1,3}to\s{1,3}do)?|"
    r"what\s{0,2}can\s{0,2}(?:you|u)\s{0,2}help(?:\s{1,3}me)?\s{1,3}with|"
    r"(?:can|could)\s{0,2}(?:you|u)\s{0,2}help(?:\s{1,3}me)?|"
    r"help(?:\s{1,3}me)?|what\s{0,2}is\s{0,2}this)[\s?!.,]{0,6}$",
    re.IGNORECASE,
)


_GREETING_VARIANTS: tuple[str, ...] = (
    "Hi, I'm {assistant}, {brand}'s virtual assistant. Ask me about Zero Trust, "
    "Essential Eight or our Microsoft 365 security packages.",
    "Hello, I'm {assistant}. What would you like to know about {brand} and our "
    "security services?",
    "Hi there, {assistant} here. How can I help with {brand} today?",
)

_THANKS_VARIANTS: tuple[str, ...] = (
    "No worries. Ask away if anything else about {brand} comes up.",
    "Happy to help. Is there anything else you'd like to know?",
    "Anytime. I'm here if you think of anything else.",
)

_FAREWELL_VARIANTS: tuple[str, ...] = (
    "Thanks for stopping by. Come back any time you have a question about {brand}.",
    "Take care, and good luck with your security plans.",
    "Cheers, have a great day. I'm here whenever you need me.",
)

_CAPABILITY_VARIANTS: tuple[str, ...] = (
    "I can walk you through {brand}'s services, from Zero Trust and Essential "
    "Eight to our Microsoft 365 security packages. Where would you like to start?",
    "I can explain what {brand} does, how Zero Trust and Essential Eight work "
    "and how we look after your data. What would you like to know?",
    "Ask me about {brand}'s services, our security packages or how we look "
    "after your data. I'm happy to help.",
)


def _variant_index(query: str, count: int) -> int:
    """Stable (process-independent) variant choice for ``query``."""
    digest = hashlib.sha256(query.strip().lower().encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % count


def _render(variants: tuple[str, ...], query: str) -> str:
    template = variants[_variant_index(query, len(variants))]
    return template.format(**brand_values())


def _render_all(variants: tuple[str, ...]) -> tuple[str, ...]:
    values = brand_values()
    return tuple(v.format(**values) for v in variants)


def get_greeting_response(query: str = "") -> str:
    return _render(_GREETING_VARIANTS, query)


def get_thanks_response(query: str = "") -> str:
    return _render(_THANKS_VARIANTS, query)


def get_farewell_response(query: str = "") -> str:
    return _render(_FAREWELL_VARIANTS, query)


def get_capability_response(query: str = "") -> str:
    return _render(_CAPABILITY_VARIANTS, query)


def all_greeting_responses() -> tuple[str, ...]:
    return _render_all(_GREETING_VARIANTS)


def all_thanks_responses() -> tuple[str, ...]:
    return _render_all(_THANKS_VARIANTS)


def all_farewell_responses() -> tuple[str, ...]:
    return _render_all(_FAREWELL_VARIANTS)


def all_capability_responses() -> tuple[str, ...]:
    return _render_all(_CAPABILITY_VARIANTS)


GREETING_RESPONSE = get_greeting_response()
THANKS_RESPONSE = get_thanks_response()
FAREWELL_RESPONSE = get_farewell_response()
CAPABILITY_RESPONSE = get_capability_response()


def detect_conversational_intent(query: str) -> str | None:
    """Return a canned reply for small-talk input, or None if `query` looks
    like a genuine question that should go through retrieval/generation.
    """

    text = query.strip()

    if not text or len(text) > _MAX_CONVERSATIONAL_CHARS:
        return None

    if _GREETING_RE.match(text):
        return get_greeting_response(text)

    if _THANKS_RE.match(text):
        return get_thanks_response(text)

    if _FAREWELL_RE.match(text):
        return get_farewell_response(text)

    if _CAPABILITY_RE.match(text):
        return get_capability_response(text)

    return None


# "Let me talk to a human" hand-off requests: a fixed reply. Contact details
# (phone, email, offices, "how do I contact you") are NOT matched: the Contact
# Us page answers them, so they are retrieved and cited. Short, anchored,
# bounded patterns only (no nested quantifiers).
_MAX_CONTACT_CHARS = 100

_CONTACT_RE = re.compile(
    r"^\s{0,3}(?:"
    # "I want to talk to someone" / "speak to a human"
    r"(?:(?:i|we)(?:\s{1,3}(?:want|need|would\s{1,3}like)|'d\s{1,3}like)\s{1,3}to\s{1,3}|"
    r"(?:can|could|may)\s{1,3}(?:i|we)\s{1,3}|let\s{1,3}me\s{1,3})?"
    r"(?:talk|speak|chat)\s{1,3}(?:to|with)\s{1,3}"
    r"(?:a|an|some|someone|somebody|your|the)?\s{0,3}(?:real\s{1,3})?"
    r"(?:someone|somebody|human|person|agent|specialist|representative|rep|"
    r"sales(?:\s{1,3}team)?|support(?:\s{1,3}team)?|team|expert)"
    r")[\s?!.]{0,6}$",
    re.IGNORECASE,
)


def get_contact_response() -> str:
    brand = get_settings().brand_name
    return (
        "Our team would be glad to talk with you. You can reach them through "
        f"the Contact {brand} form on our website."
    )


def detect_contact_request(query: str) -> str | None:
    """Return the fixed hand-off reply for a "let me talk to a human" request.

    Never invents a phone number, email or address. Returns None for anything
    else: contact-detail and office questions are answered from the Contact Us
    page, and a named person's details are blocked by pre-checks.
    """

    text = query.strip()
    if not text or len(text) > _MAX_CONTACT_CHARS:
        return None
    if _CONTACT_RE.match(text):
        return get_contact_response()
    return None


# Someone describing a live security incident ("we got hacked"): a warm fixed
# reply, never remediation advice. Short, anchored, bounded patterns only, and
# never for a question that names DeployIQ or a service (those are retrieved).
_MAX_INCIDENT_CHARS = 100
_INCIDENT_EVENT = (
    r"(?:hacked|breached|compromised|phished|infected|attacked|"
    r"under\s{1,3}attack|hit\s{1,3}(?:by|with)\s{1,3}ransomware)"
)
_INCIDENT_SUBJECT = r"(?:we|i|(?:our|my)\s{1,3}[a-z]{1,20}(?:\s{1,3}[a-z]{1,20})?)"
_INCIDENT_AUX = r"(?:'ve|'re|'m|\s{1,3}(?:have|has|had|are|is|am|was|were|just|got|been|being)){0,3}"
_INCIDENT_RE = re.compile(
    r"^\s{0,3}(?:"
    # "we got hacked" / "we've been breached" / "our email was compromised"
    rf"{_INCIDENT_SUBJECT}{_INCIDENT_AUX}\s{{1,3}}{_INCIDENT_EVENT}"
    # "ransomware" / "we have ransomware"
    r"|(?:(?:we|i)\s{1,3}(?:have|got)\s{1,3}(?:a\s{1,3})?)?ransomware"
    # "phishing attack happened"
    r"|(?:a\s{1,3})?(?:phishing|ransomware|malware|cyber)\s{1,3}(?:attack|incident)\s{1,3}"
    r"(?:happened|hit\s{1,3}us|occurred)"
    r")(?:[\s,.!?]{1,3}(?:what|how|help|now\s{1,3}what)[a-z\s',?!.]{0,40})?[\s?!.]{0,6}$",
    re.IGNORECASE,
)
INCIDENT_REPLY = (
    "I'm sorry you're dealing with that. If it's happening right now, contact "
    "your IT team or provider straight away, and our team can help you work out "
    "what happened and tighten things up through the Contact page on our website."
)


def detect_incident_request(query: str) -> str | None:
    """Return the fixed incident reply for "we got hacked"-style statements.

    Pure and bounded. Returns None for informational questions (for example
    "how does DeployIQ help after a breach?"), which go through retrieval.
    """

    text = query.strip()
    if not text or len(text) > _MAX_INCIDENT_CHARS:
        return None
    if ex.names_catalogue_term(text):
        return None
    if _INCIDENT_RE.match(text):
        return INCIDENT_REPLY
    return None


# Service-agnostic pricing / quote requests: the knowledge base publishes no
# prices, so a fixed reply. Bare "how much does it cost?" is NOT matched (it
# asks which service). Bounded, anchored patterns only.
# Not on the live path (pricing is routed by the clarify/buying rules); kept
# for detect_pricing_request callers. States no fact the pages don't state.
PRICING_REPLY = (
    "We don't publish pricing, as it depends on your setup. Tell me which "
    "service you're interested in and I'll share what's included."
)
_MAX_PRICING_CHARS = 100
_QUOTE = r"(?:a|an)\s{1,3}(?:free\s{1,3})?(?:quote|quotation|estimate)"
_PRICING_RE = re.compile(
    r"^\s{0,3}(?:"
    r"(?:can|could|may)\s{1,3}(?:i|we)\s{1,3}(?:please\s{1,3})?"
    rf"(?:get|have|request)\s{{1,3}}{_QUOTE}"
    r"|(?:i|we)\s{1,3}(?:would\s{1,3}like|want|need|'d\s{1,3}like)\s{1,3}"
    rf"(?:to\s{{1,3}}(?:get|request)\s{{1,3}})?{_QUOTE}"
    rf"|(?:get|request)\s{{1,3}}{_QUOTE}"
    r"|how\s{1,3}much\s{1,3}(?:does|would|will|is)\s{1,3}it\s{1,3}(?:cost|be)\s{1,3}"
    r"(?:for|to)\s{1,3}(?:my|our)\s{1,3}(?:company|business|organi[sz]ation|org|team)"
    r"|(?:what|whats|what's)(?:\s{1,3}(?:is|are))?\s{1,3}(?:your|ur|the)\s{1,3}"
    r"(?:prices|pricing|price|rates|costs)"
    r")[\s?!.]{0,6}$",
    re.IGNORECASE,
)


def detect_pricing_request(query: str) -> str | None:
    """Return the fixed pricing reply for a service-agnostic pricing request."""

    text = query.strip()
    if 0 < len(text) <= _MAX_PRICING_CHARS and _PRICING_RE.match(text):
        return PRICING_REPLY
    return None


# ---------------------------------------------------------------------------
# Support, callback, assurance, identity and acknowledgement. Fixed, warm
# replies: no LLM, no retrieval, no sources. Support, callback, complaint and
# assurance are chosen by the intent classifier (app.guardrails.intent), never
# by keywords; identity and acknowledgement keep anchored, bounded patterns.
# ---------------------------------------------------------------------------
_MAX_SMALLTALK_CHARS = 200
_MAX_ACK_CHARS = 30

SUPPORT_CANCEL_REPLY = (
    "I'm sorry to hear that. I can't change accounts or payments here, but our "
    "team can sort it out quickly, so please get in touch through the Contact "
    "page on our website."
)
SUPPORT_BROKEN_REPLY = (
    "I'm really sorry, that's not the experience we want you to have. Please "
    "contact our specialists through the Contact page on our website so they "
    "can look into it straight away."
)
SUPPORT_FRUSTRATION_REPLY = (
    "Sorry it's not helping. Tell me a bit more about what you were after, or "
    "our specialists can step in through the Contact page on our website."
)
SUPPORT_MANAGER_REPLY = (
    "Understood. I can't transfer you from here, but our team can take it from "
    "there, so please use the Contact page on our website and ask for a manager."
)
CALLBACK_REPLY = (
    "I can't book calls from this chat, but our team will gladly get back to "
    "you if you send a request through the Contact page on our website."
)
ASSURANCE_REPLY = (
    "Nobody can honestly promise 100% security, and we wouldn't want to. What "
    "we do is keep a person in charge of every change, with checkpoints and "
    "evidence along the way. If you'd like the detail, one of our specialists "
    "can walk you through it."
)
_ACK_VARIANTS: tuple[str, ...] = (
    "Sure. What would you like to know about {brand}?",
    "No problem. Ask me about Zero Trust, Essential Eight or our Microsoft 365 "
    "security packages.",
    "Okay. What can I help you with today?",
)

_IDENTITY_RE = re.compile(
    r"^\s{0,3}(?:"
    r"(?:what|whats|what's)(?:\s{1,3}is)?\s{1,3}(?:your|ur)\s{1,3}name"
    r"|who\s{1,3}(?:are|r)\s{1,3}(?:you|u)"
    r"|who\s{1,3}(?:made|built|created|developed|trained|programmed|owns)\s{1,3}(?:you|u)"
    r"|(?:what|which)\s{1,3}(?:model|ai|llm|bot|chatbot|gpt)(?:\s{1,3}(?:model|bot))?"
    r"\s{1,3}(?:are|r)\s{1,3}(?:you|u)(?:\s{1,3}(?:running|using|based)(?:\s{1,3}(?:on|in))?)?"
    r"|(?:are|r)\s{1,3}(?:you|u)\s{1,3}(?:a\s{1,3}|an\s{1,3})?"
    r"(?:real\s{1,3}(?:person|human)|bot|chatbot|human|real|robot|ai|person|"
    r"chat\s{0,2}gpt|gpt|claude|gemini|copilot|llm)"
    r")[\s?!.]{0,6}$",
    re.IGNORECASE,
)
_ACK_RE = re.compile(
    r"^(?:yes|yeah|yep|yup|no|nope|nah|ok|okay|k|kk|sure|cool|lol|ha(?:ha){1,3}|"
    r"test|testing|nice|great|hm{1,4}|right|alright|\U0001F44D)"
    r"[\s!.,?\U0001F44D]{0,6}$",
    re.IGNORECASE,
)


def _bounded(query: str, limit: int) -> str | None:
    text = query.strip()
    if not text or len(text) > limit:
        return None
    return text


def identity_reply() -> str:
    """The approved identity template (owner rule: always says it is an AI)."""
    return policy_template(IDENTITY_TEMPLATE)


# ---------------------------------------------------------------------------
# PERSONA: questions about the assistant itself, each with its own approved
# template (app.harness.prompt_catalog.PERSONA_TEMPLATES). Whole-message,
# anchored, bounded patterns; checked in order (model before human, so "are
# you ChatGPT?" is a model question). A message naming a product or KB topic
# is never a persona question ("what can DeployIQ do for Intune?"), except the
# model and privacy patterns, which only accept fixed words.
# ---------------------------------------------------------------------------
_MAX_PERSONA_CHARS = 120
_P_LEAD = (
    r"^\s{0,3}(?:(?:hey|hi|hello|so|ok(?:ay)?|but|and|wait|just\s{1,3}curious|"
    r"quick\s{1,3}question|out\s{1,3}of\s{1,3}curiosity|honestly|seriously)"
    r"[\s,.!]{1,3})?(?:(?:can|could|may)\s{1,3}(?:i\s{1,3}ask|you\s{1,3}tell\s{1,3}me)"
    r"[\s,]{1,3})?"
)
_P_TAIL = r"(?:[\s,]{1,3}(?:please|mate|then|again|exactly|anyway))?[\s?!.]{0,6}$"
_P_YOU = r"(?:you|u|ya)"
_P_BOT = (
    r"(?:bot|chat\s{0,2}bot|assistant|ai|chat|thing|tool|service|widget|"
    r"virtual\s{1,3}assistant)"
)
_P_THIS = rf"(?:{_P_YOU}|this(?:\s{{1,3}}{_P_BOT})?|the\s{{1,3}}{_P_BOT})"
_VENDOR = (
    r"(?:chat\s{0,2}gpt|gpt(?:[\s-]{0,2}\d{1,2}(?:\.\d)?[a-z]{0,2})?|open\s{0,2}ai|"
    r"claude|anthropic|gemini|bard|google|copilot|llama|meta|mistral|grok|"
    r"deepseek|bing|watson|alexa|siri|(?:a|an)\s{1,3}llm|llm|"
    r"(?:a\s{1,3})?(?:large\s{1,3})?language\s{1,3}model)"
)
_LANGUAGE = (
    r"(?:english|spanish|french|german|italian|portuguese|chinese|mandarin|"
    r"cantonese|japanese|korean|hindi|arabic|vietnamese|thai|indonesian|greek|"
    r"russian|punjabi|tagalog|filipino|urdu|dutch|polish|turkish|bengali|tamil|"
    r"gujarati|nepali|malay|persian|farsi|hebrew|swedish|"
    r"other\s{1,3}languages|another\s{1,3}language|(?:my|your)\s{1,3}(?:own\s{1,3})?language|"
    r"different\s{1,3}languages|multiple\s{1,3}languages)"
)
_CHAT = r"(?:chat|chats|conversation|conversations|convo|messages?|questions?|answers?)"

_PERSONA_PATTERNS: tuple[tuple[str, str, bool], ...] = (
    # (intent, pattern body, skip the catalogue guard)
    (
        PERSONA_MODEL,
        rf"(?:(?:are|r)\s{{1,3}}{_P_YOU}\s{{1,3}}(?:(?:just|really|actually|basically)"
        rf"\s{{1,3}})?(?:a\s{{1,3}}|an\s{{1,3}})?{_VENDOR}(?:\s{{1,3}}(?:based|powered))?"
        rf"|(?:are|r)\s{{1,3}}{_P_YOU}\s{{1,3}}(?:built|based|powered|running|made|run)"
        rf"\s{{1,3}}(?:on|with|by|using)\s{{1,3}}{_VENDOR}"
        rf"|(?:is|does)\s{{1,3}}{_P_THIS}\s{{1,3}}(?:use|using|run(?:ning)?\s{{1,3}}on|"
        rf"built\s{{1,3}}on|powered\s{{1,3}}by|based\s{{1,3}}on)\s{{1,3}}{_VENDOR}"
        r"|(?:what|which)\s{1,3}(?:ai\s{1,3})?(?:model|llm|ai|gpt|language\s{1,3}model|"
        r"engine|version|tech(?:nology)?)(?:\s{1,3}(?:model|version))?\s{1,3}"
        rf"(?:are|r|do|is|does)\s{{1,3}}{_P_THIS}(?:\s{{1,3}}(?:running|using|use|based|"
        r"built|powered|run)(?:\s{1,3}(?:on|in|by|with))?)?"
        rf"|(?:what|which)\s{{1,3}}(?:ai\s{{1,3}})?(?:model|llm|ai|engine)\s{{1,3}}"
        rf"(?:powers|runs|drives|is\s{{1,3}}behind)\s{{1,3}}{_P_THIS}"
        r"|(?:what|whats|what's)(?:\s{1,3}is)?\s{1,3}(?:your|ur)\s{1,3}(?:ai\s{1,3})?"
        r"(?:model|llm|underlying\s{1,3}model|tech(?:nology)?(?:\s{1,3}stack)?|"
        r"architecture|engine)"
        rf"|how\s{{1,3}}(?:are|were)\s{{1,3}}{_P_YOU}\s{{1,3}}(?:built|made|trained|"
        r"programmed|coded))",
        True,
    ),
    (
        PERSONA_NAME,
        r"(?:(?:what|whats|what's)(?:\s{1,3}is)?\s{1,3}(?:your|ur|the\s{1,3}"
        rf"{_P_BOT}'?s?|this\s{{1,3}}{_P_BOT}'?s?)\s{{1,3}}name"
        rf"|do\s{{1,3}}{_P_YOU}\s{{1,3}}have\s{{1,3}}a\s{{1,3}}name"
        rf"|what\s{{1,3}}(?:should|do|can)\s{{1,3}}(?:i|we)\s{{1,3}}call\s{{1,3}}{_P_YOU}"
        rf"|(?:what|who)\s{{1,3}}(?:are|r)\s{{1,3}}{_P_YOU}\s{{1,3}}called"
        rf"|what\s{{1,3}}is\s{{1,3}}this\s{{1,3}}{_P_BOT}\s{{1,3}}called"
        rf"|(?:tell\s{{1,3}}me\s{{1,3}})?(?:your|ur)\s{{1,3}}name)",
        False,
    ),
    (
        PERSONA_CREATOR,
        r"(?:who\s{1,3}(?:made|built|created|developed|designed|trained|programmed|"
        rf"owns|runs|operates|wrote|coded|makes|maintains)\s{{1,3}}{_P_THIS}"
        rf"|who\s{{1,3}}(?:do|d)\s{{1,3}}{_P_YOU}\s{{1,3}}(?:work|belong)\s{{1,3}}(?:for|to)"
        rf"|who\s{{1,3}}(?:are|r)\s{{1,3}}{_P_YOU}\s{{1,3}}(?:made|built|created|owned|"
        r"run|developed)\s{1,3}by"
        rf"|who\s{{1,3}}(?:are|r)\s{{1,3}}{_P_YOU}\s{{1,3}}(?:with|from)"
        rf"|who(?:'s|\s{{1,3}}is)\s{{1,3}}behind\s{{1,3}}{_P_THIS}"
        r"|who(?:'s|\s{1,3}is)\s{1,3}(?:your|ur)\s{1,3}(?:creator|maker|developer|"
        r"owner|boss|employer|company)"
        r"|(?:which|what)\s{1,3}company\s{1,3}(?:made|built|created|owns|runs|"
        rf"makes)\s{{1,3}}{_P_THIS}"
        rf"|(?:which|what)\s{{1,3}}company\s{{1,3}}(?:are|do)\s{{1,3}}{_P_YOU}\s{{1,3}}"
        r"(?:from|with|work\s{1,3}for|belong\s{1,3}to|represent))",
        False,
    ),
    (
        PERSONA_MEMORY,
        rf"(?:(?:do|will|would|can)\s{{1,3}}{_P_YOU}\s{{1,3}}(?:still\s{{1,3}})?remember\s{{1,3}}"
        r"(?:me|us|this(?:\s{1,3}(?:chat|conversation))?|who\s{1,3}i\s{1,3}am|"
        r"my\s{1,3}name|what\s{1,3}(?:i|we)\s{1,3}(?:said|asked|talked\s{1,3}about|"
        r"discussed|chatted\s{1,3}about)|our\s{1,3}(?:last|previous|earlier|old)\s{1,3}"
        r"(?:chat|conversation|talk|session)s?|(?:past|previous|earlier|old)\s{1,3}"
        r"(?:chats|conversations|sessions))"
        r"(?:\s{1,3}(?:last\s{1,3}(?:week|time|month|night)|yesterday|before|earlier|"
        r"from\s{1,3}(?:before|last\s{1,3}(?:week|time))))?"
        r"|have\s{1,3}we\s{1,3}(?:talked|chatted|spoken|met)\s{1,3}before"
        rf"|do\s{{1,3}}{_P_YOU}\s{{1,3}}(?:know|recogni[sz]e)\s{{1,3}}(?:me|who\s{{1,3}}i\s{{1,3}}am)"
        rf"|(?:do|will|can)\s{{1,3}}{_P_YOU}\s{{1,3}}(?:have\s{{1,3}}(?:a\s{{1,3}})?memory|"
        r"keep\s{1,3}(?:a\s{1,3})?(?:history|record)|learn\s{1,3}from\s{1,3}(?:me|us))"
        rf"|will\s{{1,3}}{_P_YOU}\s{{1,3}}forget\s{{1,3}}(?:me|this|this\s{{1,3}}conversation))",
        False,
    ),
    (
        PERSONA_HUMAN,
        rf"(?:(?:are|r)\s{{1,3}}{_P_YOU}\s{{1,3}}(?:(?:really|actually|just)\s{{1,3}})?"
        r"(?:a\s{1,3}|an\s{1,3})?(?:real(?:\s{1,3}(?:person|human))?|human(?:\s{1,3}being)?|"
        r"person|bot|robot|chat\s{0,2}bot|ai|a\.i\.?|machine|computer|program|automated|"
        r"virtual\s{1,3}assistant)"
        r"(?:\s{1,3}or\s{1,3}(?:a\s{1,3}|an\s{1,3})?(?:real\s{1,3})?(?:bot|ai|robot|machine|"
        r"human|person|computer))?"
        r"|(?:am|is)\s{1,3}(?:i|this)\s{1,3}(?:talking|chatting|speaking)\s{1,3}(?:to|with)"
        r"\s{1,3}(?:a\s{1,3}|an\s{1,3})?(?:real\s{1,3})?(?:person|human|bot|robot|machine|"
        r"ai|computer)"
        r"|is\s{1,3}this\s{1,3}(?:a\s{1,3}|an\s{1,3})?(?:real\s{1,3})?(?:person|human|bot|"
        r"robot|machine|ai|chat\s{0,2}bot|automated(?:\s{1,3}(?:chat|reply|response|system))?)"
        r"|is\s{1,3}(?:there\s{1,3})?(?:a\s{1,3})?(?:real\s{1,3})?(?:person|human)\s{1,3}"
        r"(?:there|on\s{1,3}the\s{1,3}other\s{1,3}end|behind\s{1,3}this|typing|reading\s{1,3}this))",
        False,
    ),
    (
        PERSONA_PURPOSE,
        rf"(?:who\s{{1,3}}(?:are|r)\s{{1,3}}{_P_YOU}"
        rf"|what\s{{1,3}}(?:are|r)\s{{1,3}}{_P_YOU}(?:\s{{1,3}}(?:exactly|for|here\s{{1,3}}for))?"
        rf"|(?:what|whats|what's)(?:\s{{1,3}}is)?\s{{1,3}}this(?:\s{{1,3}}{_P_BOT})?"
        rf"(?:\s{{1,3}}for)?"
        r"|(?:what|whats|what's)(?:\s{1,3}is)?\s{1,3}(?:your|ur)\s{1,3}(?:purpose|job|"
        r"role|function)"
        rf"|why\s{{1,3}}(?:are|r)\s{{1,3}}{_P_YOU}\s{{1,3}}here"
        r"|tell\s{1,3}me\s{1,3}about\s{1,3}(?:yourself|you)"
        r"|introduce\s{1,3}yourself)",
        False,
    ),
    (
        PERSONA_CAPABILITIES,
        rf"(?:what\s{{1,3}}(?:can|could)\s{{1,3}}{_P_YOU}\s{{1,3}}(?:do|help(?:\s{{1,3}}"
        r"(?:me|us))?(?:\s{1,3}with)?|tell\s{1,3}me(?:\s{1,3}about)?|answer)"
        r"(?:\s{1,3}for\s{1,3}(?:me|us))?"
        rf"|what\s{{1,3}}do\s{{1,3}}{_P_YOU}\s{{1,3}}(?:do|know(?:\s{{1,3}}about)?|cover)"
        rf"|how\s{{1,3}}(?:can|could|do|will)\s{{1,3}}{_P_YOU}\s{{1,3}}help(?:\s{{1,3}}(?:me|us))?"
        r"|what\s{1,3}(?:can|should|could)\s{1,3}(?:i|we)\s{1,3}ask(?:\s{1,3}(?:you|about))?"
        r"|what\s{1,3}(?:kind|sort|type)s?\s{1,3}of\s{1,3}(?:questions|things|stuff)\s{1,3}"
        rf"(?:can|do)\s{{1,3}}{_P_YOU}\s{{1,3}}(?:answer|help\s{{1,3}}with|do|"
        r"know(?:\s{1,3}about)?)"
        rf"|what\s{{1,3}}topics\s{{1,3}}(?:can|do)\s{{1,3}}{_P_YOU}\s{{1,3}}(?:cover|"
        r"help\s{1,3}with|know(?:\s{1,3}about)?)"
        rf"|what\s{{1,3}}(?:are|r)\s{{1,3}}{_P_YOU}\s{{1,3}}(?:able\s{{1,3}}to\s{{1,3}}do|"
        r"good\s{1,3}(?:at|for))"
        rf"|(?:can|could)\s{{1,3}}{_P_YOU}\s{{1,3}}help(?:\s{{1,3}}(?:me|us))?"
        r"|help(?:\s{1,3}me)?)",
        False,
    ),
    (
        PERSONA_LANGUAGES,
        rf"(?:(?:what|which)\s{{1,3}}languages?\s{{1,3}}(?:do|can)\s{{1,3}}{_P_YOU}\s{{1,3}}"
        r"(?:speak|understand|support|talk|reply\s{1,3}in|respond\s{1,3}in|use|read|"
        r"write|know)"
        rf"|how\s{{1,3}}many\s{{1,3}}languages\s{{1,3}}(?:do|can)\s{{1,3}}{_P_YOU}\s{{1,3}}"
        r"(?:speak|understand|support|know)"
        rf"|(?:do|can)\s{{1,3}}{_P_YOU}\s{{1,3}}(?:speak|understand|talk|reply\s{{1,3}}in|"
        r"respond\s{1,3}in|chat\s{1,3}in|write\s{1,3}in|answer\s{1,3}in|support|handle)"
        rf"\s{{1,3}}{_LANGUAGE}"
        rf"|(?:can|may)\s{{1,3}}(?:i|we)\s{{1,3}}(?:ask|write|chat|talk|type)\s{{1,3}}"
        rf"(?:to\s{{1,3}}{_P_YOU}\s{{1,3}})?in\s{{1,3}}{_LANGUAGE}"
        rf"|(?:are|is)\s{{1,3}}{_P_THIS}\s{{1,3}}(?:available\s{{1,3}}in\s{{1,3}}"
        r"(?:other\s{1,3}languages|another\s{1,3}language)|(?:only\s{1,3})?in\s{1,3}"
        r"english(?:\s{1,3}only)?|english\s{1,3}only)"
        r"|hablas?\s{1,3}espa(?:n|ñ)ol|parle[sz][\s-]{1,3}vous\s{1,3}fran(?:c|ç)ais"
        r"|sprechen\s{1,3}sie\s{1,3}deutsch)",
        False,
    ),
    (
        PERSONA_PRIVACY,
        rf"(?:(?:is|are)\s{{1,3}}(?:this|our|my|the)\s{{1,3}}{_CHAT}(?:\s{{1,3}}"
        r"(?:with\s{1,3}you|here))?\s{1,3}(?:really\s{1,3})?(?:private|confidential|secure|"
        r"safe|encrypted|anonymous|recorded|logged|saved|stored|monitored|kept|"
        r"being\s{1,3}(?:recorded|logged|saved|stored|monitored|read)|"
        r"read\s{1,3}by\s{1,3}(?:anyone|someone|a\s{1,3}(?:person|human)|people))"
        rf"|(?:do|will|does|can)\s{{1,3}}(?:{_P_YOU}|anyone|someone|your\s{{1,3}}team|"
        r"a\s{1,3}(?:person|human)|people)\s{1,3}(?:save|store|record|log|keep|read|see|"
        rf"share|sell|monitor|use)\s{{1,3}}(?:this\s{{1,3}}{_CHAT}|our\s{{1,3}}{_CHAT}|"
        rf"my\s{{1,3}}{_CHAT}|what\s{{1,3}}i\s{{1,3}}(?:type|write|say|send|share))"
        r"|who\s{1,3}(?:can|will)\s{1,3}(?:see|read)\s{1,3}(?:this(?:\s{1,3}(?:chat|"
        rf"conversation))?|my\s{{1,3}}{_CHAT}|what\s{{1,3}}i\s{{1,3}}(?:type|write|say))"
        r"|what\s{1,3}(?:happens\s{1,3}to|do\s{1,3}you\s{1,3}do\s{1,3}with)\s{1,3}"
        rf"(?:this\s{{1,3}}{_CHAT}|my\s{{1,3}}{_CHAT}|what\s{{1,3}}i\s{{1,3}}(?:type|write))"
        r"|is\s{1,3}it\s{1,3}(?:safe|ok(?:ay)?)\s{1,3}to\s{1,3}(?:share|type|enter|put)\s{1,3}"
        r"(?:personal|private|sensitive|confidential)\s{1,3}(?:details|info(?:rmation)?|"
        r"stuff)(?:\s{1,3}(?:here|in\s{1,3}(?:this|the)\s{1,3}chat))?"
        r"|is\s{1,3}this\s{1,3}(?:chat\s{1,3})?private)",
        True,
    ),
)
_PERSONA_RES: tuple[tuple[str, re.Pattern[str], bool], ...] = tuple(
    (intent, re.compile(_P_LEAD + body + _P_TAIL, re.IGNORECASE), skip_guard)
    for intent, body, skip_guard in _PERSONA_PATTERNS
)


def detect_persona(query: str) -> str | None:
    """The persona intent of a question about the assistant itself, else None.

    Pure, bounded and anchored: a product question ("what can DeployIQ do for
    Intune?") never matches, so it goes to retrieval.
    """
    text = _bounded(query, _MAX_PERSONA_CHARS)
    if text is None:
        return None
    names_topic = ex.names_catalogue_term(text)
    for intent, pattern, skip_guard in _PERSONA_RES:
        if (skip_guard or not names_topic) and pattern.match(text):
            return intent
    return None


def persona_reply(intent: str, privacy_url: str | None = None) -> str:
    """The approved persona template for ``intent``."""
    return persona_template(intent, privacy_url)


PERSONA_REPLY_KEYS: dict[str, str] = {
    persona_template_id(intent): intent for intent in PERSONA_TEMPLATES
}


def detect_identity_question(query: str) -> str | None:
    """Fixed reply to "who are you / are you a bot / what model are you"."""

    text = _bounded(query, _MAX_SMALLTALK_CHARS)
    if text is not None and _IDENTITY_RE.match(text):
        return identity_reply()
    return None


# Identity probes: personal identity, emotions, memory or human attributes
# ("do you have feelings?", "how old are you?"). Anchored, bounded patterns;
# the new probes never fire when the message names a KB topic.
_MAX_PROBE_CHARS = 120
_YOU = r"(?:you|u)"
_LEAD = r"(?:(?:hey|hi|so|ok(?:ay)?|but|and|wait|honestly|seriously)[\s,]{1,3})?"
_HUMAN_THING = (
    r"(?:real(?:\s{1,3}(?:person|human))?|human(?:\s{1,3}being)?|person|"
    r"bot|robot|chat\s{0,2}bot|ai|machine|alive|sentient|conscious|"
    r"self[\s-]{0,2}aware|male|female|man|woman|happy|sad|lonely)"
)
_INNER_LIFE = (
    r"(?:feelings?|emotions?|opinions?|views|beliefs|thoughts|preferences|"
    r"consciousness|a\s{1,3}(?:body|family|life|personality|soul|memory|"
    r"favou?rite\s{1,3}[a-z]{1,20})|memory|memories|friends)"
)
_IDENTITY_PROBE_RE = re.compile(
    rf"^\s{{0,3}}{_LEAD}(?:"
    rf"(?:are|r)\s{{1,3}}{_YOU}\s{{1,3}}(?:(?:really|actually)\s{{1,3}})?"
    rf"(?:a\s{{1,3}}|an\s{{1,3}})?{_HUMAN_THING}"
    r"|(?:am|is)\s{1,3}(?:i|this)\s{1,3}(?:talking|chatting|speaking)\s{1,3}"
    r"(?:to|with)\s{1,3}(?:a\s{1,3}|an\s{1,3})?(?:real\s{1,3})?"
    r"(?:person|human|bot|robot|machine|ai)"
    r"|is\s{1,3}this\s{1,3}(?:a\s{1,3}|an\s{1,3})?(?:real\s{1,3})?"
    r"(?:person|human|bot|robot|machine)"
    rf"|(?:do|can)\s{{1,3}}{_YOU}\s{{1,3}}(?:have|feel|get|experience)\s{{1,3}}"
    rf"(?:any\s{{1,3}}|real\s{{1,3}})?{_INNER_LIFE}"
    rf"|(?:do|can)\s{{1,3}}{_YOU}\s{{1,3}}(?:ever\s{{1,3}})?(?:feel|love|dream|sleep|eat|"
    r"get\s{1,3}(?:tired|bored|lonely|sad|angry|sick|fed\s{1,3}up))"
    r"(?:\s{1,3}(?:anything|things|emotions?))?"
    r"(?:\s{1,3}of\s{1,3}[a-z]{1,20}(?:\s{1,3}[a-z]{1,20}){0,2})?"
    rf"|(?:are|r)\s{{1,3}}{_YOU}\s{{1,3}}(?:ever\s{{1,3}})?(?:tired|bored|sick|fed\s{{1,3}}up)"
    r"(?:\s{1,3}of\s{1,3}[a-z]{1,20}(?:\s{1,3}[a-z]{1,20}){0,2})?"
    rf"|(?:do|will|would)\s{{1,3}}{_YOU}\s{{1,3}}remember\s{{1,3}}"
    r"(?:me|us|who\s{1,3}i\s{1,3}am|our\s{1,3}(?:last|previous)\s{1,3}"
    r"(?:chat|conversation))"
    rf"|how\s{{1,3}}old\s{{1,3}}(?:are|r)\s{{1,3}}{_YOU}"
    rf"|(?:what|whats|what's)(?:\s{{1,3}}is)?\s{{1,3}}(?:your|ur)\s{{1,3}}"
    r"(?:age|gender|favou?rite\s{1,3}[a-z]{1,20}|opinion|view|feelings?)"
    rf"|when\s{{1,3}}were\s{{1,3}}{_YOU}\s{{1,3}}born"
    rf"|where\s{{1,3}}do\s{{1,3}}{_YOU}\s{{1,3}}live"
    r")(?:\s{1,3}(?:or|and)\s{1,3}[a-z]{1,20})?(?:\s{1,3}at\s{1,3}all)?[\s?!.]{0,6}$",
    re.IGNORECASE,
)


def is_identity_probe(query: str) -> bool:
    """True for a question about the assistant itself (identity template)."""

    text = _bounded(query, _MAX_SMALLTALK_CHARS)
    if text is None:
        return False
    if _IDENTITY_RE.match(text):
        return True
    return (
        len(text) <= _MAX_PROBE_CHARS
        and not ex.names_catalogue_term(text)
        and _IDENTITY_PROBE_RE.match(text) is not None
    )


# Regulated professional advice (legal, financial, medical, insurance,
# employment): a regulated topic asked about the visitor's own situation.
# Used only on pre-LLM decline paths; a question about what our pages or
# services say is never matched (it is retrieved and answered).
_MAX_REGULATED_CHARS = 300
_REGULATED_TOPIC_RE = re.compile(
    r"\b(?:legal(?:ly)?|lawful|unlawful|illegal|laws?|lawyers?|solicitors?|"
    r"court|fined|fines|penalt(?:y|ies)|liab(?:le|ility|ilities)|lawsuits?|"
    r"sue|sued|suing|contracts?|clauses?|enforceable|tax(?:es|ed)?|"
    r"deductible|deduct|invest(?:ing|ment|ments)?|medical|medications?|"
    r"diagnos[a-z]{0,6}|doctors?|insurance|insurer|employment\s{1,3}law|"
    r"unfair\s{1,3}dismissal|privacy\s{1,3}act|gdpr|"
    # employment law without a "legal" word: reading / monitoring staff
    r"(?:employee|staff|worker)(?:'s|s'|s)?\s{1,3}(?:personal|private)\s{1,3}"
    r"(?:messages|emails?|texts|chats|phones?|accounts?|files|social\s{1,3}media)|"
    r"(?:monitor|spy\s{1,3}on|track|surveil)(?:ing)?\s{1,3}(?:my\s{1,3}|our\s{1,3})?"
    r"(?:employees|staff|workers)|workplace\s{1,3}surveillance)\b",
    re.IGNORECASE,
)
# Advice shape: the visitor's own situation ("should I", "can my company",
# "do we") or a verdict on a thing ("is this clause enforceable").
_ADVICE_SHAPE_RE = re.compile(
    r"\b(?:i|we|my|our|me|us|i'm|we're)\b"
    r"|^\s{0,3}(?:is|are)\s{1,3}(?:it|this|that|these|those)\b"
    # a hypothetical organisation's own situation ("how much cover does a
    # 50-person company need")
    r"|\b(?:does|do|should|would|must)\s{1,3}(?:a|an|the|my|our)\s{1,3}"
    r"(?:[\w-]{1,20}\s{1,3}){0,3}(?:company|business|organi[sz]ation|firm|"
    r"employer|sme|startup|charity)\s{1,3}(?:need|require|have\s{1,3}to|get)\b"
    r"|\bhow\s{1,3}much\s{1,3}(?:[a-z]{1,20}\s{1,3}){0,3}(?:cover|coverage)\b",
    re.IGNORECASE,
)
_ABOUT_OUR_PAGES_RE = re.compile(
    r"\b(?:your|ur|deploy\s{0,2}iq|askdiq|exigo(?:\s{0,2}tech)?)\b"
    r"|\bwhat\s{1,3}(?:does|do|is|are)\b[^?]{0,80}?\b(?:say|says|cover|covers|"
    r"include|includes|mention|mentions|state|states|offer|offers)\b"
    r"|\b(?:on|in)\s{1,3}(?:the|this)\s{1,3}(?:website|site|page|pages)\b",
    re.IGNORECASE,
)


def pre_llm_policy(query: str, *, regulated: bool) -> tuple[str, str] | None:
    """(template id, guardrail flag) of a message refused before the LLM:
    an identity probe, or (``regulated``: on a decline path) a regulated-advice
    request. None: no policy refusal."""

    if is_identity_probe(query):
        return IDENTITY_POLICY
    if regulated and is_regulated_advice(query):
        return REGULATED_POLICY
    return None


def is_regulated_advice(query: str) -> bool:
    """True for a request for legal/financial/medical professional advice
    that is not a question about what our pages or services say."""

    text = _bounded(query, _MAX_REGULATED_CHARS)
    if text is None:
        return False
    brand = get_settings().brand_name.strip().lower()
    if brand and brand in text.lower():
        return False
    return (
        _REGULATED_TOPIC_RE.search(text) is not None
        and _ADVICE_SHAPE_RE.search(text) is not None
        and _ABOUT_OUR_PAGES_RE.search(text) is None
    )


def detect_acknowledgement(query: str) -> str | None:
    """Fixed reply to a bare "ok", "yes", "lol" or "test" with nothing else.

    The caller must skip this when the previous assistant turn was a
    clarification, since "yes" or "ok" is then an answer to it.
    """

    text = _bounded(query, _MAX_ACK_CHARS)
    if text is not None and _ACK_RE.match(text):
        return _render(_ACK_VARIANTS, text)
    return None


def all_acknowledgement_responses() -> tuple[str, ...]:
    return _render_all(_ACK_VARIANTS)


# ---------------------------------------------------------------------------
# Canned replies chosen by the intent classifier (app.guardrails.intent).
# Keys are the reply keys of app.guardrails.intent_examples.
# ---------------------------------------------------------------------------
_REPLY_BY_KEY: dict[str, str] = {
    ex.CALLBACK: CALLBACK_REPLY,
    ex.INCIDENT: INCIDENT_REPLY,
    ex.CANCEL: SUPPORT_CANCEL_REPLY,
    ex.BROKEN: SUPPORT_BROKEN_REPLY,
    ex.MANAGER: SUPPORT_MANAGER_REPLY,
    ex.FRUSTRATION: SUPPORT_FRUSTRATION_REPLY,
    ex.ASSURANCE: ASSURANCE_REPLY,
}


def canned_reply_for(reply_key: str | None, query: str) -> str | None:
    """The fixed reply for a classifier reply key (None: no canned reply)."""
    if reply_key is None:
        return None
    fixed = _REPLY_BY_KEY.get(reply_key)
    if fixed is not None:
        return fixed
    if reply_key in PERSONA_REPLY_KEYS:
        return persona_reply(PERSONA_REPLY_KEYS[reply_key])
    render = {
        ex.GREETING: get_greeting_response,
        ex.THANKS: get_thanks_response,
        ex.FAREWELL: get_farewell_response,
        ex.CAPABILITY: get_capability_response,
        ex.CONTACT: lambda _q: get_contact_response(),
        ex.IDENTITY: lambda _q: identity_reply(),
        ex.ACK: lambda q: _render(_ACK_VARIANTS, q),
    }.get(reply_key)
    return render(query) if render is not None else None
