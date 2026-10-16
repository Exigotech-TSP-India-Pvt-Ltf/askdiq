"""Australian & Enterprise PII detection/redaction.

Supports:
- Australian Tax File Number (TFN: 8 or 9 digits, mod-11 checksum)
- Australian Business Number (ABN: 11 digits, mod-89 checksum)
- Australian Medicare Card Number (10 digits + optional IRN, check digit)
- Major Credit Card numbers (Visa, Mastercard, Amex with Luhn checksum)
- Australian and International phone numbers
- Email addresses

Detectors are ordered from most specific to least specific:
Credit Card -> Medicare -> ABN -> TFN -> Phone -> Email.
Checksum validation prevents false positives on ISO standards, dates, or product SKUs.
"""

import re
from collections.abc import Sequence

# Regex patterns for initial candidate extraction
RE_CREDIT_CARD = re.compile(
    r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|2(?:2[2-9][0-9]{2}|[3-6][0-9]{3}|7[0-1][0-9]{2}|720[0-9])[0-9]{10}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|6(?:011|5[0-9]{2})[0-9]{12})\b"
)
RE_MEDICARE = re.compile(r"\b([2-6]\d{3}[-\s]?\d{5}[-\s]?\d)(?:[-\s/]?([1-9]))?\b")
RE_ABN = re.compile(r"\b(\d{2}[-\s]?\d{3}[-\s]?\d{3}[-\s]?\d{3})\b")
RE_TFN = re.compile(r"\b(\d{2,3}[-\s]?\d{3}[-\s]?\d{3})\b")
RE_AU_PHONE = re.compile(
    r"(?:\+61[-\s]?4\d{2}[-\s]?\d{3}[-\s]?\d{3}|04\d{2}[-\s]?\d{3}[-\s]?\d{3}|(?:\+61[-\s]?[2378]|0[2378])[-\s]?\d{4}[-\s]?\d{4})"
)
RE_GENERIC_PHONE = re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")
RE_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"
)
RE_SSN_LIKE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# Backward compatibility mapping for quality evaluators
PATTERNS = {
    "email": RE_EMAIL,
    "phone": RE_GENERIC_PHONE,
    "ssn_like": RE_SSN_LIKE,
    "credit_card": RE_CREDIT_CARD,
    "medicare": RE_MEDICARE,
    "abn": RE_ABN,
    "tfn": RE_TFN,
}


def validate_luhn(number_str: str) -> bool:
    """Validate credit card number using Luhn algorithm (mod 10)."""
    digits = [int(c) for c in number_str if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 1:
            doubled = d * 2
            checksum += doubled - 9 if doubled > 9 else doubled
        else:
            checksum += d
    return checksum % 10 == 0


def validate_abn(abn_str: str) -> bool:
    """Validate Australian Business Number (11 digits, mod 89)."""
    digits = [int(c) for c in abn_str if c.isdigit()]
    if len(digits) != 11:
        return False
    weights = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]
    digits[0] -= 1
    total = sum(d * w for d, w in zip(digits, weights, strict=True))
    return total % 89 == 0


def validate_tfn(tfn_str: str) -> bool:
    """Validate Australian Tax File Number (8 or 9 digits, mod 11)."""
    digits = [int(c) for c in tfn_str if c.isdigit()]
    if len(digits) == 9:
        weights = [1, 4, 3, 7, 5, 8, 6, 9, 10]
        return sum(d * w for d, w in zip(digits, weights, strict=True)) % 11 == 0
    if len(digits) == 8:
        weights = [10, 7, 8, 4, 6, 3, 5, 1]
        return sum(d * w for d, w in zip(digits, weights, strict=True)) % 11 == 0
    return False


def validate_medicare(medicare_str: str) -> bool:
    """Validate Australian Medicare Card Number (10 digits)."""
    digits = [int(c) for c in medicare_str if c.isdigit()]
    if len(digits) < 10:
        return False
    if digits[0] < 2 or digits[0] > 6:
        return False
    # First 8 digits are weighted, 9th digit is check digit
    weights = [1, 3, 7, 9, 1, 3, 7, 9]
    check = sum(d * w for d, w in zip(digits[:8], weights, strict=True)) % 10
    return digits[8] == check


def redact_pii(
    text: str,
    allowlist: Sequence[str] | None = None,
) -> tuple[str, list[str]]:
    """Detect and redact Australian and enterprise PII from text.

    Returns:
        (redacted_text, list_of_detected_types)
    """
    if not text:
        return text, []

    allowset = {re.sub(r"\D", "", a) for a in (allowlist or []) if a}
    found_types: list[str] = []
    redacted = text

    # 1. Credit Cards (Luhn validated)
    def _replace_cc(match: re.Match) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if digits in allowset:
            return raw
        if validate_luhn(digits):
            found_types.append("credit_card")
            return "[REDACTED_CREDIT_CARD]"
        return raw

    if RE_CREDIT_CARD.search(redacted):
        redacted = RE_CREDIT_CARD.sub(_replace_cc, redacted)

    # 2. Medicare (Check digit validated)
    def _replace_medicare(match: re.Match) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if digits in allowset:
            return raw
        if validate_medicare(digits):
            found_types.append("medicare")
            return "[REDACTED_MEDICARE]"
        return raw

    if RE_MEDICARE.search(redacted):
        redacted = RE_MEDICARE.sub(_replace_medicare, redacted)

    # 3. ABN (Mod 89 validated)
    def _replace_abn(match: re.Match) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if digits in allowset:
            return raw
        if validate_abn(digits):
            found_types.append("abn")
            return "[REDACTED_ABN]"
        return raw

    if RE_ABN.search(redacted):
        redacted = RE_ABN.sub(_replace_abn, redacted)

    # 4. TFN (Mod 11 validated)
    def _replace_tfn(match: re.Match) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if digits in allowset:
            return raw
        if validate_tfn(digits):
            found_types.append("tfn")
            return "[REDACTED_TFN]"
        return raw

    if RE_TFN.search(redacted):
        redacted = RE_TFN.sub(_replace_tfn, redacted)

    # 5. Australian & Generic Phones
    def _replace_au_phone(match: re.Match) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if digits in allowset:
            return raw
        found_types.append("phone")
        return "[REDACTED_PHONE]"

    if RE_AU_PHONE.search(redacted):
        redacted = RE_AU_PHONE.sub(_replace_au_phone, redacted)

    def _replace_generic_phone(match: re.Match) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if digits in allowset:
            return raw
        found_types.append("phone")
        return "[REDACTED_PHONE]"

    if RE_GENERIC_PHONE.search(redacted):
        redacted = RE_GENERIC_PHONE.sub(_replace_generic_phone, redacted)

    # 6. Emails
    def _replace_email(match: re.Match) -> str:
        raw = match.group(0)
        if raw.lower() in {a.lower() for a in (allowlist or [])}:
            return raw
        found_types.append("email")
        return "[REDACTED_EMAIL]"

    if RE_EMAIL.search(redacted):
        redacted = RE_EMAIL.sub(_replace_email, redacted)

    # Maintain unique list preserving insertion order
    unique_types = list(dict.fromkeys(found_types))
    return redacted, unique_types
