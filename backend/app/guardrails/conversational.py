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

_THANKS_RE = re.compile(
    r"^\s*(thanks?|thank\s*you|thx|ty|cheers|appreciate\s*it)[\s!.,]*$",
    re.IGNORECASE,
)

_FAREWELL_RE = re.compile(
    r"^\s*(bye|goodbye|see\s*you|see\s*ya|later)[\s!.,]*$",
    re.IGNORECASE,
)

_CAPABILITY_RE = re.compile(
    r"^\s*(what\s*can\s*you\s*do|how\s*can\s*you\s*help(\s*me)?|"
    r"what\s*do\s*you\s*do|who\s*are\s*you|what\s*are\s*you|"
    r"help(\s*me)?|what\s*is\s*this)[\s?!.,]*$",
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
    return f"You're welcome! Let me know if you have any other questions about {brand}."


def get_farewell_response() -> str:
    brand = get_settings().brand_name
    return f"Goodbye! Feel free to come back anytime you have questions about {brand}."


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

    if not text:
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
