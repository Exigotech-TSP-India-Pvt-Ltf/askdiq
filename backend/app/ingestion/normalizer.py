"""Deterministic markdown normalization pipeline (Subtask 1.4.2).

Applied identically for every entry point (API upload, CLI, seed scripts).
Fixed execution order:
1. Decode UTF-8 (strict, BOM stripped)
2. CRLF/CR -> LF
3. Unicode NFC normalization
4. Trim trailing whitespace per line
5. Collapse >= 3 blank lines down to 2 newlines
6. Single trailing newline
7. redact_pii
8. strip_boilerplate_lines
9. Canonical hash generation with DOC_HASH_VERSION and PIPELINE_VERSION
"""

import hashlib
import re
import unicodedata

from app.chunking.cleaning import strip_boilerplate_lines
from app.governance.pii import redact_pii

DOC_HASH_VERSION = "v1"
PIPELINE_VERSION = "v3"


def normalize_markdown(raw: str | bytes) -> tuple[str, list[str]]:
    """Normalize markdown content deterministically and redact PII.

    Returns (normalized_text, list_of_redacted_pii_types).
    """
    # 1. Decode UTF-8 (strict, BOM stripped)
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw.lstrip("\ufeff")

    # 2. CRLF and CR -> LF
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 3. Unicode NFC normalization
    text = unicodedata.normalize("NFC", text)

    # 4. Trim trailing whitespace per line
    lines = [line.rstrip(" \t") for line in text.split("\n")]
    text = "\n".join(lines)

    # 5. Collapse >= 3 blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 6. Ensure consistent bounding whitespace before cleaning
    text = text.strip()

    # 7. Redact PII
    text, pii_found = redact_pii(text)

    # 8. Strip boilerplate lines (e.g. copyright notices, metadata headers)
    text = strip_boilerplate_lines(text)

    # 9. Ensure single trailing newline if non-empty
    cleaned = text.strip()
    result = (cleaned + "\n") if cleaned else ""

    return result, pii_found


def doc_content_hash(text: str) -> str:
    """Document identity hash folding DOC_HASH_VERSION and PIPELINE_VERSION."""
    payload = f"{DOC_HASH_VERSION}:{PIPELINE_VERSION}:{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def chunk_content_hash(text: str) -> str:
    """Chunk identity hash folding only PIPELINE_VERSION so document hash bumps don't re-embed chunks."""
    payload = f"{PIPELINE_VERSION}{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
