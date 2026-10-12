"""Strip repeated header/footer/page-count boilerplate before chunking.

Only whole lines that are *entirely* boilerplate (after trimming whitespace)
are removed — if the same words appear as part of a larger sentence/section
(e.g. a paragraph that happens to mention "internal use only"), the line is
left untouched since it no longer matches the pattern exactly.
"""
import re

# Each pattern must match a line in full (re.fullmatch), case-insensitively.
_DEFAULT_PATTERNS = [
    r"\s*GENERAL\s*,?\s*INTERNAL\s+USE\s+ONLY\s*",
    r"\s*INTERNAL\s+USE\s+ONLY\s*",
    # Some exports split the "GENERAL, INTERNAL USE ONLY" footer across
    # separate lines (sometimes repeated once per page), leaving a lone
    # "GENERAL" line behind — drop it only when it's the entire line.
    r"\s*GENERAL\s*",
    r"\s*CONFIDENTIAL\s*",
    r"\s*Page\s+\d+\s*(?:of|/)\s*\d+\s*",
]


def strip_boilerplate_lines(text: str, extra_patterns: list[str] | None = None) -> str:
    """Remove lines that are exactly header/footer/page-count boilerplate.

    A line is dropped only when, after trimming, it fully matches one of the
    patterns — text that merely contains these words as part of a longer
    line/sentence is preserved.
    """
    patterns = [re.compile(p, re.IGNORECASE) for p in (*_DEFAULT_PATTERNS, *(extra_patterns or []))]

    kept_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and any(p.fullmatch(stripped) for p in patterns):
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines)
