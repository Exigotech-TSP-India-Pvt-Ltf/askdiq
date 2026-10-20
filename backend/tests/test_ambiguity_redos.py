"""The ambiguity rules stay linear on hostile input (CodeQL py/polynomial-redos)."""

from __future__ import annotations

import time

import pytest

from app.harness import ambiguity

HOSTILE = [
    "\t\t" * 50_000,
    "0" * 100_000,
    "it " + "\t" * 80_000 + "x",
    "Aa" + "0" * 100_000,
    "iso" + " /" * 50_000 + "x",
    "essential" + " -" * 50_000 + "eight",
]
BUDGET_SECONDS = 0.5


@pytest.mark.parametrize("text", HOSTILE, ids=lambda t: f"{t[:6]!r}x{len(t)}")
def test_rules_stay_fast_on_hostile_input(text: str) -> None:
    started = time.perf_counter()
    ambiguity._missing_slots(text)
    ambiguity._subjects(text)
    ambiguity._dangling_pronoun(text)
    assert time.perf_counter() - started < BUDGET_SECONDS


def test_scan_text_collapses_whitespace_and_bounds_length() -> None:
    assert ambiguity._scan_text("  Zero \t\n  Trust  ") == "Zero Trust"
    assert len(ambiguity._scan_text("a " * 10_000)) == ambiguity._MAX_SCAN_CHARS


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Essential Eight", "essential"),
        ("essential-8", "essential"),
        ("ISO/IEC 27001", "iso"),
        ("iso 27001", "iso"),
        ("zero_trust", "zero"),
        ("Zero   Trust", "zero"),
    ],
)
def test_catalogue_names_still_match(text: str, label: str) -> None:
    found = {s.lower() for s in ambiguity._subjects(text)}
    assert any(label in s for s in found), found


def test_proper_nouns_still_found_after_first_word() -> None:
    found = {
        s.lower() for s in ambiguity._subjects("compare it with AcmeCloud and M365")
    }
    assert {"acmecloud", "m365"} <= found
