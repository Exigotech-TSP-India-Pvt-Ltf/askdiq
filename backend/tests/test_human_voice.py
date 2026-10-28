"""Human voice: the real bad answers from the review, before and after the filter."""

from __future__ import annotations

import time

import pytest

from app.harness import prompts
from app.harness.citations import MARKER_RE
from app.harness.style import (
    NOT_COVERED_REPLY,
    ResponseStyle,
    style_instructions,
)
from app.harness.tone import humanise, polish_answer

BUDGET_SECONDS = 2.0
SPECIALIST_CLOSING = "If you'd like, one of our specialists can help with this."


def _polish(text: str) -> str:
    return polish_answer(text, ResponseStyle.ADAPTIVE)


# --------------------------------------------------------------------------
# Real bad examples
# --------------------------------------------------------------------------


def test_leading_key_points_heading_is_stripped() -> None:
    raw = (
        "## Key points\n- **Purpose:** keeps your controls mapped [1]\n"
        "- **Owner:** your team [2]"
    )
    out = _polish(raw)
    assert "key points" not in out.lower()
    assert out.startswith("- **Purpose:** keeps your controls mapped [1]")
    assert "[2]" in out


def test_non_breaking_hyphen_becomes_a_normal_hyphen() -> None:
    out = _polish(
        "It suits high\N{NON-BREAKING HYPHEN}value, low\N{NO-BREAK SPACE}risk work."
    )
    assert out == "It suits high-value, low risk work."


def test_em_dash_becomes_a_comma() -> None:
    out = _polish("Human approval is required \N{EM DASH} nothing changes without it.")
    assert out == "Human approval is required, nothing changes without it."


def test_dash_after_a_bold_term_becomes_a_colon() -> None:
    out = _polish("- **Approval** \N{EM DASH} a person signs off [1]")
    assert out == "- **Approval**: a person signs off [1]"


def test_spaced_hyphen_and_ranges() -> None:
    assert _polish("It takes time - plan for it.") == "It takes time, plan for it."
    assert _polish("Teams of 5\N{EN DASH}10 people.") == "Teams of 5 to 10 people."


def test_buzzword_pair_is_simplified() -> None:
    out = _polish("You get a comprehensive, tailored assessment.")
    assert out == "You get a tailored assessment."
    assert "comprehensive" not in out.lower()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A robust process.", "A strong process."),
        ("Robust checks run daily.", "Strong checks run daily."),
        ("It is seamless.", "It is smooth."),
        ("We leverage your data.", "We use your data."),
        ("A comprehensive view.", "A full view."),
        ("Let's delve into the plan.", "Let's look at the plan."),
    ],
)
def test_buzzwords_are_replaced_as_whole_words(raw: str, expected: str) -> None:
    assert humanise(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        'The "Robust Shield" module stays.',
        "Run `leverage()` and `a \N{EM DASH} b`.",
        "See https://example.com/robust-comprehensive for more.",
        "Email robust@example.com today.",
        "It is a robustness check.",
    ],
)
def test_quoted_names_code_urls_and_longer_words_are_untouched(text: str) -> None:
    assert humanise(text) == text


def test_next_steps_heading_becomes_one_natural_closing() -> None:
    raw = "Pilots start small [1].\n\n## Next steps\n- Contact DeployIQ to begin"
    out = _polish(raw)
    assert out == (
        "Pilots start small [1].\n\nIf you'd like, you can get in touch with us to begin."
    )
    assert "next step" not in out.lower()


def test_not_covered_with_next_step_block_becomes_one_specialist_line() -> None:
    raw = "We don't have details on that here.\n\n## Next step\n- **Talk to a specialist**"
    out = _polish(raw)
    assert out == f"We don't have details on that here.\n\n{SPECIALIST_CLOSING}"


def test_flattened_not_covered_with_next_step_heading() -> None:
    raw = "We don't have details on that here. ## Next step - **Talk to a specialist**"
    out = _polish(raw)
    assert out == f"We don't have details on that here.\n\n{SPECIALIST_CLOSING}"


def test_next_step_label_becomes_a_sentence_with_specialist_wording() -> None:
    raw = "We run guided pilots [1].\n\nNext step: talk to one of our specialists to scope this for you."
    assert _polish(raw) == (
        "We run guided pilots [1].\n\n"
        "If you'd like, one of our specialists can help scope this for you."
    )


def test_only_one_closing_is_kept() -> None:
    raw = (
        "Pilots are guided [1].\n\nNext step: talk to a specialist.\n\n"
        "Next step: book a call."
    )
    out = _polish(raw)
    assert out == f"Pilots are guided [1].\n\n{SPECIALIST_CLOSING}"


def test_closing_is_dropped_when_a_specialist_line_already_exists() -> None:
    raw = f"{NOT_COVERED_REPLY}\n\nNext step: talk to a specialist."
    assert _polish(raw) == NOT_COVERED_REPLY


def test_prose_that_mentions_next_step_is_left_alone() -> None:
    raw = "The next step is approval by a person [1]."
    assert _polish(raw) == raw


def test_consecutive_duplicate_sentences_are_dropped() -> None:
    raw = "Setup is guided by our team. Setup is guided by our team. You approve each change."
    assert _polish(raw) == "Setup is guided by our team. You approve each change."
    assert humanise("- Same bullet text here\n- Same bullet text here\n- Other") == (
        "- Same bullet text here\n- Other"
    )


def test_short_repeats_and_table_rows_are_kept() -> None:
    assert humanise("Yes. Yes.") == "Yes. Yes."
    table = "| A | B |\n| --- | --- |\n| x | y |\n| x | y |"
    assert humanise(table) == table


def test_text_that_is_already_human_is_unchanged() -> None:
    raw = (
        "You can start with one team and add more later [1]. "
        "A person approves every change, so you stay in control [2]."
    )
    assert _polish(raw) == raw


def test_humanise_is_off_under_the_current_style() -> None:
    raw = "A high\N{NON-BREAKING HYPHEN}value plan \N{EM DASH} fine."
    assert polish_answer(raw, ResponseStyle.CURRENT) == raw


@pytest.mark.parametrize(
    "answer",
    [
        "Great question! It is robust [1] \N{EM DASH} and clear [2][3].",
        "## Key points\n- A \N{EM DASH} b [1]\n- C [2]\n\nNext step: talk to a specialist.",
        "Pilots are guided [1]. A comprehensive plan [2].",
    ],
)
def test_citation_markers_survive_the_voice_filter(answer: str) -> None:
    polished = _polish(answer)
    assert [m.group(0) for m in MARKER_RE.finditer(polished)] == [
        m.group(0) for m in MARKER_RE.finditer(answer)
    ]


# --------------------------------------------------------------------------
# Prompt wording
# --------------------------------------------------------------------------


def test_not_covered_reply_is_one_natural_line_with_a_specialist() -> None:
    assert NOT_COVERED_REPLY == (
        "I can't see that detail, so it's best to check with one of our specialists."
    )
    assert "\n" not in NOT_COVERED_REPLY


@pytest.mark.parametrize(
    "needle",
    [
        "first sentence",
        '"We" plus a corporate verb',
        "contractions",
        "bullets only for 3+",
        '"Based on"',
        '"Next step:"',
        '"Key points"',
        "repeat a sentence",
        "at most one next step",
        "never the product name",
        "never dashes",
        "Australian spelling",
        "a person approves it",
    ],
)
def test_adaptive_prompt_carries_the_human_style_rules(needle: str) -> None:
    prompt = prompts.get_system_prompt(ResponseStyle.ADAPTIVE, "general")
    assert needle in prompt


@pytest.mark.parametrize("shape", ["partial", "out_of_scope"])
def test_hand_over_shapes_use_natural_specialist_words(shape: str) -> None:
    block = style_instructions(ResponseStyle.ADAPTIVE, shape).partition(
        "Never use these phrases:"
    )[2]
    assert "specialist" in block
    assert "starting" not in block


@pytest.mark.parametrize("shape", ["definition", "general", "quick_fact"])
def test_simple_shapes_ask_for_short_plain_sentences(shape: str) -> None:
    block = style_instructions(ResponseStyle.ADAPTIVE, shape)
    assert "## heading" not in block.partition("Never use these phrases:")[2]


# --------------------------------------------------------------------------
# Speed
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "a \N{EM DASH} " * 30_000,
        "robust " * 30_000,
        "- " * 50_000,
        "`" * 100_000,
        '"' * 100_000,
        "Next step: x\n" * 20_000,
        "## Key points\n" * 20_000,
        "same sentence repeated here. " * 20_000,
        " ## Next step " * 10_000,
    ],
    ids=[
        "dash",
        "robust",
        "hyphens",
        "ticks",
        "quotes",
        "next",
        "keypoints",
        "dups",
        "inline",
    ],
)
def test_voice_filter_is_linear(text: str) -> None:
    started = time.perf_counter()
    humanise(text)
    assert time.perf_counter() - started < BUDGET_SECONDS


def test_current_not_covered_tail_dropped_when_unasked() -> None:
    from app.harness.tone import drop_unrequested_caveat

    body = "We keep a person in charge of every change."
    tail = (
        "I can't see any pricing or timeline details here, so it's best to "
        "talk to one of our specialists."
    )
    assert drop_unrequested_caveat(f"{body} {tail}", "What is E8?") == body
    assert drop_unrequested_caveat(tail, "What is E8?").startswith("I can't see that")
