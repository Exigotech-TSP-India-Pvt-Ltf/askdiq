"""Canonical document identity and naming validation (Task 1.4.1).

Enforces one canonical identity per document:
- Strips any directory path (e.g. browser uploads with folders)
- Enforces Unicode NFC normalization and strips trailing whitespace/dots
- Maps .docx, .md, .markdown case-insensitively to .md
- Strictly rejects path traversal (..), drive letters, control characters,
  unsupported extensions (.doc, .docm, etc.), double extensions (a.md.docx),
  empty stems, and oversized names (> 255 chars).
"""

import re
import unicodedata
from pathlib import PurePosixPath, PureWindowsPath

ALLOWED_EXTENSIONS = {".md", ".docx", ".markdown"}
DANGEROUS_SUB_EXTENSIONS = {
    ".md",
    ".docx",
    ".markdown",
    ".doc",
    ".docm",
    ".bin",
    ".exe",
    ".sh",
}


def canonical_source_name(filename: str) -> str:
    """Return canonical '<stem>.md' or raise ValueError for invalid/unsafe names."""
    if not filename or not isinstance(filename, str):
        raise ValueError("Filename must be a non-empty string")

    # 1. Reject control characters
    for ch in filename:
        if ord(ch) < 32 or 127 <= ord(ch) < 160:
            raise ValueError("Filename contains prohibited control characters")

    # 2. Unicode NFC normalization & strip whitespace
    normalized = unicodedata.normalize("NFC", filename).strip()

    # 3. Check for path traversal (..) anywhere in path except benign trailing dots
    if ".." in normalized and ".." in normalized.rstrip(" ."):
        raise ValueError("Filename contains prohibited path traversal sequence '..'")

    # 4. Check for Windows drive letters (e.g. C:, D:)
    if re.match(r"^[a-zA-Z]:", normalized):
        raise ValueError("Filename contains prohibited drive letter prefix")

    # 5. Strip directory prefix
    base_name = PureWindowsPath(normalized).name
    base_name = PurePosixPath(base_name).name.strip()

    # Check for empty stems (e.g. .md, .docx)
    if base_name.startswith(".") and "." not in base_name[1:]:
        raise ValueError("Filename stem cannot be empty")

    # Trim trailing dots and spaces from filename (Windows artifact)
    base_name = base_name.rstrip(" .")

    if not base_name:
        raise ValueError("Filename has an empty stem after path stripping")

    # 6. Check total length cap (255 chars)
    if len(base_name) > 255:
        raise ValueError("Filename exceeds maximum allowed length of 255 characters")

    # 7. Extract extension
    dot_idx = base_name.rfind(".")
    if dot_idx == -1:
        raise ValueError(f"Filename '{base_name}' lacks a file extension")

    stem = base_name[:dot_idx].rstrip(" .")
    ext = base_name[dot_idx:].lower()

    if not stem:
        raise ValueError("Filename stem cannot be empty")

    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file extension '{ext}'. Only .md and .docx are supported"
        )

    # 8. Reject double extensions changing type (e.g. a.md.docx, a.docx.md, a.doc.md)
    stem_lower = stem.lower()
    for dangerous in DANGEROUS_SUB_EXTENSIONS:
        if stem_lower.endswith(dangerous):
            raise ValueError(f"Prohibited double extension sequence '{dangerous}{ext}'")

    # 9. Return canonical name: <stem>.md
    return f"{stem}.md"
