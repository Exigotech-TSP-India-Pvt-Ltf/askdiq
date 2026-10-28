"""Generic "check with one of our specialists" is dropped from complete answers."""

from __future__ import annotations

import time

from app.harness.style import NOT_COVERED_SENTENCE
from app.harness.tone import drop_stock_handoff, polish_answer

P1 = (
    "DeployIQ is our platform for planning and running safe software changes, "
    "with a person approving every step. [1]"
)
P2 = (
    "It keeps a clear record of each change, so you can roll back quickly "
    "if something goes wrong. [2]"
)
SCREENSHOT = f"{P1}\n\n{P2}\n\n{NOT_COVERED_SENTENCE}"
CASUAL = "what is deployiq tellllllll mee okayyyyyyy"


def test_screenshot_stock_line_removed() -> None:
    out = drop_stock_handoff(SCREENSHOT, CASUAL)
    assert out == f"{P1}\n\n{P2}"
    assert "[1]" in out
    assert "[2]" in out


def test_polish_pipeline_removes_it() -> None:
    out = polish_answer(SCREENSHOT, "adaptive", CASUAL)
    assert "specialist" not in out
    assert "[1]" in out
    assert "[2]" in out


def test_decline_only_answer_stays() -> None:
    assert drop_stock_handoff(NOT_COVERED_SENTENCE, CASUAL) == NOT_COVERED_SENTENCE
    assert drop_stock_handoff("I don't see that here.", "what is x") == (
        "I don't see that here."
    )


def test_pricing_question_keeps_one_handoff() -> None:
    answer = f"The Zero Trust Professional plan covers device checks. [1]\n\n{NOT_COVERED_SENTENCE}"
    out = drop_stock_handoff(answer, "what does the Zero Trust Professional plan cost?")
    assert out == answer


def test_ask_topics_keep_handoff() -> None:
    answer = f"{P1} {NOT_COVERED_SENTENCE}"
    for question in (
        "what is the SLA?",
        "how long will it take",
        "do you have a certification",
        "what is your phone number",
        "does it have integration with Jira",
        "what is the price",
    ):
        assert drop_stock_handoff(answer, question) == answer


def test_inline_variants_dropped() -> None:
    for tail in (
        "I don't see that detail here.",
        "We don't have details here.",
        "It's best to talk to one of our specialists.",
        "I can't see that detail here, so it's best to check with one of our specialists.",
    ):
        assert drop_stock_handoff(f"{P1} {tail}", CASUAL) == P1


def test_mid_answer_stock_line_dropped() -> None:
    answer = f"{P1}\n\n{NOT_COVERED_SENTENCE}\n\n{P2}"
    assert drop_stock_handoff(answer, CASUAL) == f"{P1}\n\n{P2}"
    inline = f"{P1} {NOT_COVERED_SENTENCE} {P2}"
    assert drop_stock_handoff(inline, CASUAL) == f"{P1} {P2}"


def test_compound_answer_keeps_exactly_one_handoff_when_asked() -> None:
    answer = f"{P1}\n\n{NOT_COVERED_SENTENCE}\n\n{P2}\n\n{NOT_COVERED_SENTENCE}"
    out = drop_stock_handoff(answer, "what is it and how much does it cost?")
    assert out.count("specialists") == 1
    assert out.endswith(NOT_COVERED_SENTENCE)
    assert P1 in out
    assert P2 in out


def test_compound_answer_drops_all_when_unasked() -> None:
    answer = f"{P1}\n\n{NOT_COVERED_SENTENCE}\n\n{P2}\n\n{NOT_COVERED_SENTENCE}"
    assert drop_stock_handoff(answer, CASUAL) == f"{P1}\n\n{P2}"


def test_named_topic_decline_untouched_here() -> None:
    answer = f"{P1} I can't see any pricing details, so talk to one of our specialists."
    assert drop_stock_handoff(answer, CASUAL) == answer


def test_factual_absence_sentence_kept() -> None:
    answer = f"{P1} We don't have a free plan."
    assert drop_stock_handoff(answer, CASUAL) == answer


def test_no_question_changes_nothing() -> None:
    assert drop_stock_handoff(SCREENSHOT, None) == SCREENSHOT
    assert drop_stock_handoff(SCREENSHOT, "  ") == SCREENSHOT


def test_idempotent() -> None:
    once = drop_stock_handoff(SCREENSHOT, CASUAL)
    assert drop_stock_handoff(once, CASUAL) == once
    kept = drop_stock_handoff(SCREENSHOT, "what is the price")
    assert drop_stock_handoff(kept, "what is the price") == kept


def test_code_fence_untouched() -> None:
    answer = f"{P1}\n\n```\n{NOT_COVERED_SENTENCE}\n```"
    assert drop_stock_handoff(answer, CASUAL) == answer


def test_linear_time() -> None:
    text = ("word " * 4000) + "x" * 20000 + " " + NOT_COVERED_SENTENCE
    start = time.perf_counter()
    drop_stock_handoff(text, CASUAL)
    drop_stock_handoff("[" * 20000 + f" {NOT_COVERED_SENTENCE}", CASUAL)
    drop_stock_handoff((P1 + " ") * 150 + NOT_COVERED_SENTENCE, CASUAL)
    assert time.perf_counter() - start < 2
