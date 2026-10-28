"""Detection of conversational small-talk (greetings/thanks/capability asks).

These aren't real questions about DeployIQ content, so routing them through
retrieval + the LLM would either waste a call or get rejected as off-topic.
Handled here with canned, deterministic replies instead.
"""

import hashlib
import re

from app.core.config import get_settings

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
    r"what\s{0,2}do\s{0,2}(?:you|u)\s{0,2}do|who\s{0,2}are\s{0,2}(?:you|u)|"
    r"what\s{0,2}are\s{0,2}(?:you|u)(?:\s{1,3}able\s{1,3}to\s{1,3}do)?|"
    r"what\s{0,2}can\s{0,2}(?:you|u)\s{0,2}help(?:\s{1,3}me)?\s{1,3}with|"
    r"(?:can|could)\s{0,2}(?:you|u)\s{0,2}help(?:\s{1,3}me)?|"
    r"help(?:\s{1,3}me)?|what\s{0,2}is\s{0,2}this)[\s?!.,]{0,6}$",
    re.IGNORECASE,
)


_GREETING_VARIANTS: tuple[str, ...] = (
    "Hi, I'm the {brand} assistant. Ask me about our services, Zero Trust or "
    "Essential Eight.",
    "Hello! Happy to help with questions about {brand}, Zero Trust or Essential Eight.",
    "Hi there. What would you like to know about {brand}?",
)

_THANKS_VARIANTS: tuple[str, ...] = (
    "No worries. Ask away if anything else comes up.",
    "Happy to help.",
    "Anytime. Just ask if you think of anything else.",
)

_FAREWELL_VARIANTS: tuple[str, ...] = (
    "Thanks for stopping by. Have a good one.",
    "Take care. Talk soon.",
    "Cheers, have a great day.",
)

_CAPABILITY_VARIANTS: tuple[str, ...] = (
    "I can explain our services, Essential Eight, Zero Trust and how we "
    "handle your data.",
    "Ask me about what {brand} does, how Essential Eight and Zero Trust work, "
    "or how we look after data.",
    "I can walk you through our services and the security frameworks we work with.",
)


def _variant_index(query: str, count: int) -> int:
    """Stable (process-independent) variant choice for ``query``."""
    digest = hashlib.sha256(query.strip().lower().encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % count


def _render(variants: tuple[str, ...], query: str) -> str:
    template = variants[_variant_index(query, len(variants))]
    return template.format(brand=get_settings().brand_name)


def _render_all(variants: tuple[str, ...]) -> tuple[str, ...]:
    brand = get_settings().brand_name
    return tuple(v.format(brand=brand) for v in variants)


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


# "How do I reach you" questions are on-topic but not answerable from the
# knowledge base. Short, anchored, bounded patterns only (no nested quantifiers).
_MAX_CONTACT_CHARS = 100

_CONTACT_NOUN = (
    r"(?:phone|telephone|mobile|email|e-?mail|address|contact|number|"
    r"office|location|support|helpline|hotline)"
)
_CONTACT_NOUN_TAIL = (
    r"(?:\s{1,3}(?:number|address|details|info|information|form|page))?"
)

_CONTACT_RE = re.compile(
    r"^\s{0,3}(?:"
    # "do you have a phone number?"
    r"(?:do|does|can)\s{1,3}(?:you|u|they|the\s{1,3}company)\s{1,3}have\s{1,3}"
    r"(?:a|an|any|some)?\s{0,3}"
    rf"(?:(?:phone|contact|support|email|e-?mail)\s{{1,3}})?{_CONTACT_NOUN}{_CONTACT_NOUN_TAIL}"
    # "what is your phone number" / "your email address"
    r"|(?:(?:what|whats|what's)(?:\s{1,3}is|\s{1,3}are)?\s{1,3})?(?:your|ur|the)\s{1,3}"
    rf"(?:(?:phone|contact|support|email|e-?mail)\s{{1,3}})?{_CONTACT_NOUN}{_CONTACT_NOUN_TAIL}"
    # "how do I contact you" / "how can I get in touch"
    r"|how\s{1,3}(?:do|can|could|would|should)\s{1,3}(?:i|we)\s{1,3}"
    r"(?:contact|reach|call|email|phone|get\s{1,3}in\s{1,3}touch(?:\s{1,3}with)?|"
    r"get\s{1,3}hold\s{1,3}of)"
    r"(?:\s{1,3}(?:you|us|them|someone|somebody|sales|support|your\s{1,3}team|the\s{1,3}team|"
    r"a\s{1,3}specialist|a\s{1,3}human|a\s{1,3}person|deployiq))?"
    # "I want to talk to someone" / "speak to a human"
    r"|(?:(?:i|we)(?:\s{1,3}(?:want|need|would\s{1,3}like)|'d\s{1,3}like)\s{1,3}to\s{1,3}|"
    r"(?:can|could|may)\s{1,3}(?:i|we)\s{1,3}|let\s{1,3}me\s{1,3})?"
    r"(?:talk|speak|chat)\s{1,3}(?:to|with)\s{1,3}"
    r"(?:a|an|some|someone|somebody|your|the)?\s{0,3}"
    r"(?:someone|somebody|human|person|agent|specialist|representative|rep|"
    r"sales(?:\s{1,3}team)?|support(?:\s{1,3}team)?|team|expert)"
    # "contact details" / "contact us" / "get in touch"
    r"|(?:contact\s{1,3}(?:us|you|details|info|information|sales|support)|get\s{1,3}in\s{1,3}touch)"
    r")[\s?!.]{0,6}$",
    re.IGNORECASE,
)


def get_contact_response() -> str:
    brand = get_settings().brand_name
    return (
        "Our specialists are happy to help. You can reach them through the "
        f"Contact {brand} form on our website."
    )


def detect_contact_request(query: str) -> str | None:
    """Return the fixed contact reply for a "how do I reach you" question.

    Never invents a phone number, email or address. Returns None for anything
    else (including contact details of a named person, which pre-checks block).
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
    "I'm sorry you're dealing with that. If it's happening right now, please "
    "contact your IT team or provider straight away. Our specialists can also "
    "help you assess what happened and tighten things up, so reach out through "
    "the Contact page on our website."
)
_INCIDENT_NAMED_RE = re.compile(
    r"\b(?:deploy\s?iq|essential\s{1,3}eight|e8|zero\s{1,3}trust|iso\s?27001|"
    r"cis|baseline|services?|plans?|products?)\b",
    re.IGNORECASE,
)


def detect_incident_request(query: str) -> str | None:
    """Return the fixed incident reply for "we got hacked"-style statements.

    Pure and bounded. Returns None for informational questions (for example
    "how does DeployIQ help after a breach?"), which go through retrieval.
    """

    text = query.strip()
    if not text or len(text) > _MAX_INCIDENT_CHARS:
        return None
    if _INCIDENT_NAMED_RE.search(text):
        return None
    if _INCIDENT_RE.match(text):
        return INCIDENT_REPLY
    return None


# Service-agnostic pricing / quote requests: the knowledge base publishes no
# prices, so a fixed reply. Bare "how much does it cost?" is NOT matched (it
# asks which service). Bounded, anchored patterns only.
PRICING_REPLY = (
    "We don't publish pricing, as it depends on your setup. The Zero Trust "
    "Starter plan is free, and for anything else one of our specialists can "
    "give you a proper quote. You can reach them through the Contact page on "
    "our website."
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
