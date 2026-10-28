"""Pure checks for answer structure and AI tone (OR-8 eval gate).

``structure_score(answer, shape)``: is the element the shape expects present
(a heading with bold bullets, numbered steps, a valid table, a short direct
line with bullets, a "what we can confirm" block, a short out-of-scope reply)
and is there no prose block longer than 2 sentences?

``ai_tone_score(answer)``: banned-phrase hits, em-dash rate and hedge-word
rate per 100 words; lower is better and ``banned_hits`` must be zero.

Inline citation markers (``[n]``, OR-7) are stripped first: they are not
words, so they never change a lead's length, a rate or a structure check.

Line-based ``str`` checks plus the linear-time marker strip, so both are
linear-time.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.harness.citations import strip_markers
from app.harness.style import BANNED_PHRASES
from app.harness.tone import longest_prose_run

_PER = 100
_MAX_PROSE_SENTENCES = 2
_MIN_BULLETS = 2
_MAX_QUICK_BULLETS = 3
_MIN_STEPS = 2
_MAX_QUICK_LEAD_WORDS = 25
_MAX_OUT_OF_SCOPE_LINES = 2
_MIN_TABLE_COLUMNS = 2
_MAX_TABLE_COLUMNS = 5
_BULLETS: tuple[str, ...] = ("- ", "* ", "• ")
_CONFIRM = "what we can confirm"
_NEXT_STEP = "next step"
_HEDGES = frozenset(
    {
        "may",
        "might",
        "perhaps",
        "possibly",
        "potentially",
        "generally",
        "typically",
        "usually",
        "arguably",
        "various",
        "somewhat",
        "likely",
    }
)
_STRIP = ".,;:!?()[]\"'*"


def _lines(answer: str) -> list[str]:
    return [line.strip() for line in answer.split("\n") if line.strip()]


def _is_bullet(line: str) -> bool:
    return line.startswith(_BULLETS)


def _is_step(line: str) -> bool:
    digits = len(line) - len(line.lstrip("0123456789"))
    return digits > 0 and line[digits : digits + 2] in {". ", ") "}


def _is_heading(line: str) -> bool:
    level = len(line) - len(line.lstrip("#"))
    return level > 0 and line[level : level + 1] == " "


def _has_bold(line: str) -> bool:
    start = line.find("**")
    return start >= 0 and line.find("**", start + 2) > start + 2


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(line: str) -> bool:
    cells = _cells(line)
    return bool(cells) and all(
        cell and "-" in cell and not cell.strip("-: ") for cell in cells
    )


def has_table(answer: str) -> bool:
    """A header row, a separator row and at least one data row (2-5 columns)."""

    lines = _lines(answer)
    for i in range(len(lines) - 2):
        header, separator, row = lines[i : i + 3]
        if not (header.startswith("|") and _is_separator(separator)):
            continue
        columns = len(_cells(header))
        if (
            _MIN_TABLE_COLUMNS <= columns <= _MAX_TABLE_COLUMNS
            and row.startswith("|")
            and len(_cells(row)) == columns
        ):
            return True
    return False


@dataclass(frozen=True, slots=True)
class _Parsed:
    answer: str
    lines: list[str]
    lowered: list[str]
    bullets: list[str]
    next_steps: int

    @classmethod
    def of(cls, answer: str) -> _Parsed:
        lines = _lines(answer)
        lowered = [line.lower() for line in lines]
        return cls(
            answer,
            lines,
            lowered,
            [line for line in lines if _is_bullet(line)],
            sum(1 for low in lowered if low.startswith(_NEXT_STEP)),
        )


def _definition(p: _Parsed) -> dict[str, bool]:
    return {
        "heading": any(_is_heading(line) for line in p.lines),
        "bullets": len(p.bullets) >= _MIN_BULLETS,
        "bold_terms": sum(1 for b in p.bullets if _has_bold(b)) >= _MIN_BULLETS,
    }


def _how_to(p: _Parsed) -> dict[str, bool]:
    return {"steps": sum(1 for line in p.lines if _is_step(line)) >= _MIN_STEPS}


def _comparison(p: _Parsed) -> dict[str, bool]:
    return {"table": has_table(p.answer)}


def _quick_fact(p: _Parsed) -> dict[str, bool]:
    lead = p.lines[0] if p.lines else ""
    return {
        "direct_line": bool(lead)
        and not _is_bullet(lead)
        and len(lead.split()) <= _MAX_QUICK_LEAD_WORDS,
        "bullets": _MIN_BULLETS <= len(p.bullets) <= _MAX_QUICK_BULLETS,
    }


def _partial(p: _Parsed) -> dict[str, bool]:
    prose = [
        line
        for line, low in zip(p.lines, p.lowered, strict=True)
        if not _is_bullet(line)
        and _CONFIRM not in low
        and not low.startswith(_NEXT_STEP)
    ]
    return {
        "confirm_block": any(_CONFIRM in low for low in p.lowered) and bool(p.bullets),
        "gap_line": len(prose) == 1,
        "next_step": p.next_steps == 1,
    }


def _out_of_scope(p: _Parsed) -> dict[str, bool]:
    return {
        "short": len(p.lines) <= _MAX_OUT_OF_SCOPE_LINES,
        "next_step": p.next_steps == 1,
    }


def _general(p: _Parsed) -> dict[str, bool]:
    return {"bullets": len(p.bullets) >= _MIN_BULLETS}


_CHECKS: dict[str, Callable[[_Parsed], dict[str, bool]]] = {
    "definition": _definition,
    "how_to": _how_to,
    "comparison": _comparison,
    "quick_fact": _quick_fact,
    "partial": _partial,
    "out_of_scope": _out_of_scope,
    "general": _general,
}


def structure_score(answer: str, shape: str) -> dict[str, object]:
    """Per-shape structure check; ``passed`` only if every check holds."""

    answer = strip_markers(answer)
    checks = _CHECKS.get(str(shape), _general)(_Parsed.of(answer))
    prose = longest_prose_run(answer)
    checks["no_prose_dump"] = prose <= _MAX_PROSE_SENTENCES
    return {
        "shape": str(shape),
        "passed": all(checks.values()),
        "checks": checks,
        "longest_prose_run": prose,
    }


def banned_phrases_in(answer: str) -> list[str]:
    lowered = answer.lower()
    return [p for p in BANNED_PHRASES if p.lower() in lowered]


INTERNAL_WORDING: tuple[str, ...] = (
    "knowledge base",
    "supplied context",
    "the documents",
    "materials",
)
CONTACT_STEP = "specialist"


def internal_wording_in(answer: str) -> list[str]:
    """Internal/pipeline jargon that must never reach a customer reply.

    Whole-phrase and case-insensitive, so ``immaterials`` does not match.
    """

    lowered = strip_markers(answer).lower()
    return [
        phrase
        for phrase in INTERNAL_WORDING
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", lowered)
    ]


def has_contact_step(answer: str) -> bool:
    """True when the reply points the reader at a specialist."""

    return CONTACT_STEP in answer.lower()


def ai_tone_score(answer: str) -> dict[str, object]:
    """Stock-AI signals per 100 words; ``score`` is their sum (lower is better)."""

    answer = strip_markers(answer)
    words = answer.split()
    count = max(len(words), 1)
    banned = banned_phrases_in(answer)
    em_dashes = answer.count("—")
    hedges = sum(1 for w in words if w.lower().strip(_STRIP) in _HEDGES)
    banned_rate = len(banned) * _PER / count
    dash_rate = em_dashes * _PER / count
    hedge_rate = hedges * _PER / count
    return {
        "words": len(words),
        "banned_hits": banned,
        "banned_per_100w": round(banned_rate, 3),
        "em_dash_per_100w": round(dash_rate, 3),
        "hedge_per_100w": round(hedge_rate, 3),
        "score": round(banned_rate + dash_rate + hedge_rate, 3),
    }
