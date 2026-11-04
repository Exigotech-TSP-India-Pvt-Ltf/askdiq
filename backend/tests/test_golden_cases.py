"""Integrity checks for the live golden set ``tests/golden/golden_cases.json``
(no DB / LLM / network). This file replaced the retired
``data/promoted_test_cases.json``."""

# pylint: disable=redefined-outer-name  # pytest fixtures are injected by name

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

GOLDEN_JSON = Path(__file__).resolve().parent / "golden" / "golden_cases.json"
# Perturbation cases are deliberate surface variants (case, tabs, newlines) of
# the same question, so only exact duplicates are rejected there.
SURFACE_VARIANT_CATEGORIES = frozenset({"perturbation"})


def _norm(query: str) -> str:
    return " ".join(query.lower().split())


@pytest.fixture(scope="module")
def cases() -> list[dict[str, Any]]:
    data = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
    assert isinstance(data.get("cases"), list) and data["cases"]
    return data["cases"]


def test_golden_case_ids_are_unique(cases: list[dict[str, Any]]) -> None:
    ids = Counter(c.get("id") for c in cases)
    assert None not in ids
    assert [i for i, n in ids.items() if n > 1] == []


def test_golden_cases_have_category_and_well_formed_turns(
    cases: list[dict[str, Any]],
) -> None:
    for case in cases:
        assert case.get("category"), case.get("id")
        turns = case.get("turns")
        assert isinstance(turns, list) and turns, case["id"]
        for turn in turns:
            assert isinstance(turn.get("query"), str) and turn["query"], case["id"]
            assert turn.get("expect"), case["id"]


def test_golden_single_turn_queries_are_unique_per_category(
    cases: list[dict[str, Any]],
) -> None:
    seen: dict[tuple[str, str], str] = {}
    dupes: list[tuple[str, str]] = []
    for case in cases:
        if len(case["turns"]) != 1:
            continue
        query = case["turns"][0]["query"]
        if case["category"] not in SURFACE_VARIANT_CATEGORIES:
            query = _norm(query)
        key = (case["category"], query)
        if key in seen:
            dupes.append((seen[key], case["id"]))
        else:
            seen[key] = case["id"]
    assert not dupes, dupes
