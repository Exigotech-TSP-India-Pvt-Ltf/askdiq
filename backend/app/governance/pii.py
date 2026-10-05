"""Basic PII detection/redaction applied on ingest and on logged queries.

TODO: this regex set covers common patterns (email, phone, SSN-like) for
scaffold purposes. Swap in a proper PII library (e.g. Presidio) before
handling real user data.
"""
import re

PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone": re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "ssn_like": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
}


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Returns (redacted_text, list_of_pii_types_found)."""
    found = []
    redacted = text
    for label, pattern in PATTERNS.items():
        if pattern.search(redacted):
            found.append(label)
            redacted = pattern.sub(f"[REDACTED_{label.upper()}]", redacted)
    return redacted, found
