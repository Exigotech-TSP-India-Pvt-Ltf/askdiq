"""Detection of conversational small-talk (greetings/thanks/capability asks).

These aren't real questions about DeployIQ content, so routing them through
retrieval + the LLM would either waste a call or get rejected as off-topic.
Handled here with canned, deterministic replies instead.
"""

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
    r"^\s*(bye|goodbye|see\s*you|see\s*ya|later)[\s!.,]*$",
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


def get_greeting_response() -> str:
    brand = get_settings().brand_name
    return (
        f"Hello! I'm the {brand} assistant. Ask me anything about {brand}'s "
        "platform, Essential Eight, Zero Trust, governance."
    )


def get_thanks_response() -> str:
    brand = get_settings().brand_name
    return f"You're welcome! Glad I could help with {brand}."


def get_farewell_response() -> str:
    brand = get_settings().brand_name
    return f"Goodbye, and thanks for stopping by {brand}. Take care!"


def get_capability_response() -> str:
    brand = get_settings().brand_name
    return (
        f"I can answer questions about {brand} — its platform, Essential Eight "
        "and Zero Trust compliance, governance features, and plans. "
        "Just ask a question and I'll find the relevant information."
    )


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
        return get_greeting_response()

    if _THANKS_RE.match(text):
        return get_thanks_response()

    if _FAREWELL_RE.match(text):
        return get_farewell_response()

    if _CAPABILITY_RE.match(text):
        return get_capability_response()

    return None
