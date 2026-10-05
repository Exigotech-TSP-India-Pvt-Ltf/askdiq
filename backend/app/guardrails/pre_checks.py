"""Guardrail checks that run before retrieval/generation."""

import re


UNSAFE_PATTERNS = [
    r"\bignore (all|previous) instructions\b",
    r"\bsystem prompt\b",
]

# Links have no place in a genuine question to this assistant — real users
# ask about DeployIQ, they don't paste URLs. A strong, low-false-positive
# spam signal on its own.
_URL_RE = re.compile(
    r"https?://\S+|www\.\S+|\b[a-z0-9-]+\.(?:com|net|org|io|biz|xyz|info|co)\b",
    re.IGNORECASE,
)

# Common promotional/scam phrasing — deliberately narrow (not generic words
# like "subscribe"/"pricing" that a real DeployIQ question could contain).
_PROMO_PHRASES_RE = re.compile(
    r"\b(buy now|click here|limited time offer|act now|100%\s*free|"
    r"make money fast|work from home|subscribe to my channel|follow me|"
    r"dm me|whatsapp me|claim your prize|you'?ve won|free gift|"
    r"guaranteed income|check out my channel)\b",
    re.IGNORECASE,
)

_VOWEL_RE = re.compile(r"[aeiou]", re.IGNORECASE)
# Below this vowel ratio, a long alphabetic token reads as keyboard-mash
# gibberish rather than a real word in any common language.
_GIBBERISH_MIN_LEN = 10
_GIBBERISH_MAX_VOWEL_RATIO = 0.2


def check_length(
    query: str,
    max_length: int,
) -> tuple[bool, str | None]:
    """Validate query length and ensure it is not empty."""

    if len(query) > max_length:
        return False, "query_too_long"

    if not query.strip():
        return False, "empty_query"

    return True, None


def check_unsafe_input(
    query: str,
) -> tuple[bool, str | None]:
    """Detect obviously unsafe prompt-injection style input."""

    lowered = query.lower()

    for pattern in UNSAFE_PATTERNS:
        if re.search(pattern, lowered):
            return False, "unsafe_input_detected"

    return True, None


def _looks_like_gibberish(word: str) -> bool:
    if len(word) < _GIBBERISH_MIN_LEN or not word.isalpha():
        return False
    vowel_ratio = len(_VOWEL_RE.findall(word)) / len(word)
    return vowel_ratio < _GIBBERISH_MAX_VOWEL_RATIO


def check_spam(
    query: str,
) -> tuple[bool, str | None]:
    """Detect junk input that isn't a genuine question: gibberish,
    keyboard-mash repetition, or promotional/scam content — none of which
    is worth spending a retrieval + LLM call on, or worth answering at all.
    """

    text = query.strip()
    if not text:
        return True, None

    if _URL_RE.search(text) or _PROMO_PHRASES_RE.search(text):
        return False, "spam_detected"

    # Same character repeated many times in a row (e.g. "aaaaaaaaaa!!!!!!!!").
    if re.search(r"(.)\1{6,}", text):
        return False, "spam_detected"

    words = text.split()

    # Same word dominating the whole message (e.g. "buy buy buy buy buy").
    if len(words) >= 4:
        lowered_words = [w.lower() for w in words]
        most_common_count = max(lowered_words.count(w) for w in set(lowered_words))
        if most_common_count >= 4 and most_common_count / len(words) >= 0.5:
            return False, "spam_detected"

    # Long alphabetic tokens with almost no vowels read as keyboard mash.
    if any(_looks_like_gibberish(word) for word in words):
        return False, "spam_detected"

    return True, None


def run_pre_checks(
    query: str,
    max_length: int,
) -> list[str]:
    """Run checks that do not require retrieval.

    Domain relevance is intentionally NOT checked here.

    Relevance is evaluated after vector retrieval so that semantic
    queries that do not contain exact domain keywords are not rejected.
    """

    flags: list[str] = []

    checks = (
        (check_length, (query, max_length)),
        (check_unsafe_input, (query,)),
        (check_spam, (query,)),
    )

    for check, args in checks:
        passed, flag = check(*args)

        if not passed and flag:
            flags.append(flag)

    return flags
