"""OR-8 eval metrics: structure_score per shape and ai_tone_score."""

from __future__ import annotations

import time

import pytest

from app.evaluation.style_quality import (
    ai_tone_score,
    banned_phrases_in,
    has_table,
    structure_score,
)

GOOD: dict[str, str] = {
    "definition": (
        "The Essential Eight is the ACSC's eight core mitigation strategies.\n\n"
        "## What it covers\n"
        "- **Application control**: only approved software runs.\n"
        "- **Patching**: apps and systems stay current.\n"
        "- **MFA**: a second factor for every admin."
    ),
    "how_to": (
        "Here's how we onboard you.\n"
        "1. Connect your tenant.\n"
        "2. Run the assessment.\n"
        "3. Approve the plan."
    ),
    "comparison": (
        "The Essential Eight is a baseline; ISO 27001 is a management system.\n\n"
        "| Aspect | Essential Eight | ISO 27001 |\n"
        "| --- | :---: | --- |\n"
        "| Scope | Eight strategies | Annex A controls |\n"
        "| Owner | ACSC | ISO |"
    ),
    "quick_fact": (
        "Yes, we work in Microsoft 365 tenants.\n"
        "- Controls apply through Microsoft 365 policies.\n"
        "- Your team approves every change."
    ),
    "partial": (
        "We don't publish a standard ISO 27001 timeline.\n"
        "Here's what we can confirm:\n"
        "- Scope: the Annex A technological controls.\n"
        "- Your team approves every change.\n"
        "Next step: book a technical assessment."
    ),
    "out_of_scope": (
        "We don't cover retail stores here.\n"
        "Next step: talk to our team about buying questions."
    ),
    "general": (
        "We deliver security uplift in phases.\n\n"
        "## How it works\n- Pilot first.\n- Scale after validation."
    ),
}

DUMP = (
    "The Essential Eight is a framework. It has eight strategies. It is "
    "published by the ACSC. Many organisations use it."
)


@pytest.mark.parametrize("shape", list(GOOD))
def test_well_formed_answers_pass(shape: str) -> None:
    result = structure_score(GOOD[shape], shape)
    assert result["passed"], result


@pytest.mark.parametrize("shape", list(GOOD))
def test_paragraph_dumps_fail(shape: str) -> None:
    result = structure_score(DUMP, shape)
    assert not result["passed"]
    assert result["checks"]["no_prose_dump"] is False


def test_definition_needs_bold_terms() -> None:
    plain = GOOD["definition"].replace("**", "")
    assert structure_score(plain, "definition")["checks"]["bold_terms"] is False


def test_how_to_needs_two_steps() -> None:
    assert not structure_score("Do this.\n1. Connect.", "how_to")["passed"]


def test_table_needs_separator_and_matching_row() -> None:
    assert has_table(GOOD["comparison"])
    no_sep = "| A | B |\n| x | y |\n| z | w |"
    ragged = "| A | B |\n| --- | --- |\n| x | y | z |"
    too_wide = "|a|b|c|d|e|f|\n|-|-|-|-|-|-|\n|1|2|3|4|5|6|"
    assert not has_table(no_sep)
    assert not has_table(ragged)
    assert not has_table(too_wide)


def test_quick_fact_lead_is_short() -> None:
    long_lead = " ".join(["word"] * 30) + "\n- a\n- b"
    assert structure_score(long_lead, "quick_fact")["checks"]["direct_line"] is False


def test_out_of_scope_is_short_with_a_next_step() -> None:
    assert not structure_score("We don't cover that here.", "out_of_scope")["passed"]


def test_tone_score_counts_banned_dashes_and_hedges() -> None:
    clean = ai_tone_score(GOOD["definition"])
    assert clean["banned_hits"] == []
    assert clean["score"] == 0
    noisy = ai_tone_score(
        "Great question! We leverage a robust — seamless — approach "
        "that may typically help. I hope this helps"
    )
    assert set(noisy["banned_hits"]) >= {
        "Great question!",
        "Leverage",
        "Robust",
        "Seamless",
        "I hope this helps",
    }
    assert noisy["em_dash_per_100w"] > 0
    assert noisy["hedge_per_100w"] > 0
    assert noisy["score"] > clean["score"]


def test_banned_matching_is_case_insensitive() -> None:
    assert banned_phrases_in("as an ai, I can't") == ["As an AI"]


def test_metrics_are_linear_on_100kb() -> None:
    text = "| a |\n" * 20_000 + "- **x" * 10_000
    started = time.perf_counter()
    structure_score(text, "comparison")
    ai_tone_score(text)
    assert time.perf_counter() - started < 1.0


def test_citation_markers_do_not_change_style_metrics() -> None:
    lead = " ".join(["word"] * 24) + "."
    plain = f"{lead}\n- One fact.\n- Two facts."
    cited = f"{lead} [1][2]\n- One fact [1].\n- Two facts [2][3]."
    assert structure_score(cited, "quick_fact") == structure_score(plain, "quick_fact")
    assert structure_score(cited, "quick_fact")["passed"]
    table = "One lead [1].\n| A | B |\n| --- | --- |\n| x [1] | y [2] |"
    assert has_table(table)
    assert structure_score(table, "comparison")["passed"]
    assert ai_tone_score(cited)["words"] == ai_tone_score(plain)["words"]
