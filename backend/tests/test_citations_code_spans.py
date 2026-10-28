"""Free-bracket stripping leaves code (fenced and inline) alone."""

from __future__ import annotations

import time

import pytest

from app.harness.citations import strip_free_brackets


def test_inline_code_span_keeps_brackets() -> None:
    assert strip_free_brackets("Use `x[i]` here and [note] there.") == (
        "Use `x[i]` here and note there."
    )


def test_double_backtick_span_keeps_brackets() -> None:
    assert strip_free_brackets("Use ``a[b] ` c[d]`` and [note].") == (
        "Use ``a[b] ` c[d]`` and note."
    )


def test_unmatched_backtick_is_literal() -> None:
    assert strip_free_brackets("A ` tick and [note].") == "A ` tick and note."


def test_fenced_block_and_markers_and_whole_sentence_brackets() -> None:
    text = "[Yes. We pilot first.] [1]\n```\na[b]\n```\nSee `c[d]` [2]."
    assert strip_free_brackets(text) == (
        "Yes. We pilot first. [1]\n```\na[b]\n```\nSee `c[d]` [2]."
    )


@pytest.mark.parametrize(
    "text",
    ["`" * 50_000, "`a" * 50_000, "".join("`" * n + "[x] " for n in range(1, 300))],
    ids=["ticks", "open", "lengths"],
)
def test_strip_free_brackets_is_near_linear(text: str) -> None:
    started = time.perf_counter()
    strip_free_brackets(text)
    assert time.perf_counter() - started < 2.0
