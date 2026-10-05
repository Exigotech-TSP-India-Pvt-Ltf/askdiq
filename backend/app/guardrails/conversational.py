"""Detection of conversational small-talk (greetings/thanks/capability asks).

These aren't real questions about DeployIQ content, so routing them through
retrieval + the LLM would either waste a call or get rejected as off-topic.
Handled here with canned, deterministic replies instead.
"""

import re

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

GREETING_RESPONSE = (
    "Hello! I'm the DeployIQ assistant. Ask me anything about DeployIQ's "
    "platform, Essential Eight, Zero Trust, governance."
)

THANKS_RESPONSE = (
    "You're welcome! Let me know if you have any other questions about DeployIQ."
)

FAREWELL_RESPONSE = (
    "Goodbye! Feel free to come back anytime you have questions about DeployIQ."
)

CAPABILITY_RESPONSE = (
    "I can answer questions about DeployIQ — its platform, Essential Eight "
    "and Zero Trust compliance, governance features, and plans. "
    "Just ask a question and I'll find the relevant information."
)


def detect_conversational_intent(query: str) -> str | None:
    """Return a canned reply for small-talk input, or None if `query` looks
    like a genuine question that should go through retrieval/generation.
    """

    text = query.strip()

    if not text:
        return None

    if _GREETING_RE.match(text):
        return GREETING_RESPONSE

    if _THANKS_RE.match(text):
        return THANKS_RESPONSE

    if _FAREWELL_RE.match(text):
        return FAREWELL_RESPONSE

    if _CAPABILITY_RE.match(text):
        return CAPABILITY_RESPONSE

    return None
