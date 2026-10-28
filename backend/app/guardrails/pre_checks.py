import re
import string
import unicodedata

UNSAFE_PATTERNS = [
    r"\b(ignore|disregard|forget|override)\b.{0,30}\b(instructions|rules|guidelines|prompt)\b",
    r"\b(print|reveal|show|repeat|output)\b.{0,30}\b(hidden|secret|system|initial)\b.{0,20}\b(instructions|prompt|rules)\b",
    r"\bsystem prompt\b",
]

# Links have no place in a genuine question to this assistant
_URL_RE = re.compile(
    r"https?://\S+|www\.\S+|\b[a-z0-9-]+\.(?:com|net|org|io|biz|xyz|info|co)\b",
    re.IGNORECASE,
)

# Promotional/scam phrasing
_PROMO_PHRASES_RE = re.compile(
    r"\b(buy now|click here|limited time offer|act now|"
    r"make money fast|work from home|subscribe to my channel|follow me|"
    r"dm me|whatsapp me|claim your prize|you'?ve won|free gift|"
    r"guaranteed income|check out my channel)\b",
    re.IGNORECASE,
)

# Sensitive-request patterns. Every pattern runs on a whitespace-collapsed,
# length-capped query and uses only bounded quantifiers (no polynomial ReDoS).
_SENSITIVE_MAX_CHARS = 1000
_CROSS_TENANT_RE = re.compile(
    r"\b(?:other|another)\s{1,3}(?:customers?|tenants?|clients?)\b",
    re.IGNORECASE,
)
_CREDENTIAL_RE = re.compile(
    r"\b(?:passwords?|admin(?:istrator)?\s{1,3}credentials?|"
    r"api[\s_-]{0,3}keys?|secrets?|access\s{1,3}tokens?)\b",
    re.IGNORECASE,
)
_REVEAL_VERB_RE = re.compile(
    r"\b(?:print|reveal|show|list|provide|output|give|display|what\s{1,3}is)\b",
    re.IGNORECASE,
)
_OWN_APPLICATION_RE = re.compile(
    r"\bused\s{1,3}(?:by|in)\s{1,3}(?:this|your)\s{1,3}(?:application|app|system)\b"
    r"|\bin\s{1,3}your\s{1,3}environment\b",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"\b(?:value|contents?)\s{1,3}of\s{1,3}[A-Z][A-Z0-9_]{0,60}"
    r"(?:KEY|SECRET|TOKEN|PASSWORD)\b"
)
_PERSONAL_CONTACT_RE = re.compile(
    r"\bpersonal\s{1,3}number\b|\b(?:personal|private)\s{1,3}"
    r"(?:mobile|phone|contact)\s{1,3}(?:number|details?)\b",
    re.IGNORECASE,
)
_PERSON_RE = re.compile(
    r"\b(?:ceo|chief\s{1,3}executive|executive|employee|"
    r"staff\s{1,3}member|team\s{1,3}member|director)\b",
    re.IGNORECASE,
)

_VOWEL_RE = re.compile(r"[aeiouy]", re.IGNORECASE)
_CONSONANT_CLUSTER_RE = re.compile(r"[bcdfghjklmnpqrstvwxz]{5,}", re.IGNORECASE)

# Whitelisted technical terms and domain acronyms that must never be flagged as gibberish
ALLOWED_ACRONYMS = {
    "e8",
    "mfa",
    "soc2",
    "acsc",
    "rbac",
    "saml",
    "sso",
    "jwt",
    "api",
    "sdk",
    "iam",
    "siem",
    "edr",
    "ml6",
    "llm",
    "rag",
    "ai",
    "db",
    "ip",
    "url",
    "id",
    "os",
    "vm",
}

_ALLOWED_SHORT_TOKENS = {"hi", "yo", "ok", "no"}

_KEYBOARD_SEQS = (
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
    "poiuytrewq",
    "lkjhgfdsa",
    "mnbvcxz",
    "qazwsxedc",
    "plmoknijb",
)

# Letter doubles that virtually never appear in valid English words
_UNUSUAL_DOUBLES_RE = re.compile(r"(jj|qq|vv|xx|hh|ww|yy)", re.IGNORECASE)


_STRIP_CHARS = string.punctuation + string.whitespace


def _clean_token(token: str) -> str:
    """Strip leading and trailing punctuation/symbols from token."""
    return token.strip(_STRIP_CHARS)


def is_gibberish_token(token: str) -> bool:
    """Check if an individual token looks like keyboard mash or gibberish."""
    cleaned = _clean_token(token).lower()
    if not cleaned or cleaned in ALLOWED_ACRONYMS or cleaned in _ALLOWED_SHORT_TOKENS:
        return False

    letters_only = re.sub(r"[^a-z]", "", cleaned)
    if not letters_only:
        return False

    # 1. Keyboard mash sequences of length >= 3 (e.g. asd, qwe, zxc, asdfgh)
    if len(letters_only) >= 3 and any(letters_only in seq for seq in _KEYBOARD_SEQS):
        return True

    # 2. Obvious unnatural character combinations (e.g. "jj" in "akjj", "qq", "xx")
    if _UNUSUAL_DOUBLES_RE.search(letters_only):
        return True

    # 3. 4 or more consecutive consonants (or 5+)
    if _CONSONANT_CLUSTER_RE.search(letters_only):
        return True

    # 4. Short tokens (length >= 4) ending in unusual 3-consonant clusters
    if len(letters_only) == 4 and re.search(
        r"[bcdfghjklmnpqrstvwxz]{3,}$", letters_only
    ):
        vowels = len(_VOWEL_RE.findall(letters_only))
        if vowels <= 1 and not letters_only.endswith(("ing", "tion", "able", "ment")):
            return True

    # 5. Tokens of length >= 4 with zero vowels (including 'y')
    vowels = len(_VOWEL_RE.findall(letters_only))
    if len(letters_only) >= 4 and vowels == 0:
        return True

    # 6. Long tokens (length >= 5) with very low vowel density (< 15%)
    if len(letters_only) >= 5 and (vowels / len(letters_only)) < 0.15:
        return True

    # 7. Tokens with mixed punctuation/digits embedded that have low vowel density
    # e.g. "a;;o0d,ndnk", "sa;k;k;f"
    return bool(
        any(ch in token for ch in ";,:_")
        and len(letters_only) >= 4
        and (vowels == 0 or (vowels / len(letters_only)) < 0.25)
    )


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


def check_sensitive_request(query: str) -> tuple[bool, str | None]:
    """Block requests for credentials/secrets or staff personal contact details."""
    text = " ".join(query[:_SENSITIVE_MAX_CHARS].split())
    if _REVEAL_VERB_RE.search(text):
        if _SECRET_VALUE_RE.search(text):
            return False, "application_secret_request"
        if _CREDENTIAL_RE.search(text) and _OWN_APPLICATION_RE.search(text):
            return False, "application_secret_request"
    if _CROSS_TENANT_RE.search(text) and _CREDENTIAL_RE.search(text):
        return False, "no_permission"
    if _PERSONAL_CONTACT_RE.search(text) and _PERSON_RE.search(text):
        return False, "personal_contact_request"
    return True, None


def check_spam(
    query: str,
) -> tuple[bool, str | None]:
    """Detect junk input: gibberish, keyboard mash, ultra-short noise, or promo spam."""
    text = unicodedata.normalize("NFKC", query).strip()
    if not text:
        return True, None

    if _URL_RE.search(text) or _PROMO_PHRASES_RE.search(text):
        return False, "spam_detected"

    # Same character repeated 5+ times in a row (e.g. "aaaaa" or "!!!!!!!!")
    if re.search(r"(.)\1{5,}", text):
        return False, "spam_detected"

    # Punctuation or symbols only (e.g. "???", "---", "!?!?")
    if re.match(r"^[\s\W_]+$", text):
        return False, "unclear_input"

    # Ultra-short inputs (e.g. "a", "y", "k") that are not whitelisted
    alphanumeric_chars = re.sub(r"[\W_]", "", text).lower()
    if (
        len(alphanumeric_chars) <= 2
        and alphanumeric_chars not in ALLOWED_ACRONYMS
        and alphanumeric_chars not in _ALLOWED_SHORT_TOKENS
    ):
        return False, "unclear_input"

    words = text.split()

    # Repetitive words (e.g. "buy buy buy buy buy")
    if len(words) >= 4:
        lowered_words = [w.lower() for w in words]
        most_common_count = max(lowered_words.count(w) for w in set(lowered_words))
        if most_common_count >= 4 and most_common_count / len(words) >= 0.5:
            return False, "spam_detected"

    # Check for gibberish words
    gibberish_words = [w for w in words if is_gibberish_token(w)]

    # If single-word query is gibberish -> spam
    if len(words) == 1 and gibberish_words:
        return False, "spam_detected"

    # If 50% or more words are gibberish -> spam
    if gibberish_words and (len(gibberish_words) / len(words)) >= 0.5:
        return False, "spam_detected"

    return True, None


def sanitize_query(query: str) -> tuple[str, list[str]]:
    """Sanitize the query by stripping stray noise tokens when valid question content exists.

    Returns (sanitized_query, pre_flags).
    """
    flags = run_pre_checks(query, 2000)
    if flags:
        return query, flags

    # If clean, check if there are minor noise tokens to strip (e.g. "what is deployiq? akjj")
    words = query.strip().split()
    valid_words = [w for w in words if not is_gibberish_token(w)]

    if valid_words and len(valid_words) < len(words):
        cleaned = " ".join(valid_words).strip()
        return cleaned, []

    return query.strip(), []


def run_pre_checks(
    query: str,
    max_length: int,
) -> list[str]:
    """Run checks that do not require retrieval."""
    flags: list[str] = []

    checks = (
        (check_length, (query, max_length)),
        (check_sensitive_request, (query,)),
        (check_unsafe_input, (query,)),
        (check_spam, (query,)),
    )

    for check, args in checks:
        passed, flag = check(*args)
        if not passed and flag:
            flags.append(flag)

    return flags
