"""Unit tests for the pure scoring helpers (no DB / LLM needed)."""

import math

import pytest

from app.evaluation import metrics as m
from app.evaluation.judge import build_judge_prompt, parse_judge_output


# ---- chunk relevance -------------------------------------------------------


def test_chunk_relevance_doc_only():
    flags = m.chunk_relevance(["a.md", "b.md", None], [None, None, None], ["b.md"])
    assert flags == [False, True, False]


def test_chunk_relevance_requires_doc_and_heading():
    flags = m.chunk_relevance(
        ["a.md", "a.md", "b.md"],
        ["Starter (Free)", "Professional", "Starter (Free)"],
        ["a.md"],
        ["starter"],
    )
    assert flags == [True, False, False]


def test_chunk_relevance_heading_missing_is_not_a_match():
    assert m.chunk_relevance(["a.md"], [None], ["a.md"], ["Starter"]) == [False]


# ---- recall / precision ----------------------------------------------------


def test_doc_recall_single_doc_is_binary():
    assert m.doc_recall_at_k(["x", "a"], ["a"], k=1) == 0.0
    assert m.doc_recall_at_k(["x", "a"], ["a"], k=2) == 1.0


def test_doc_recall_compound_is_graded():
    expected = ["a", "b", "c"]
    assert m.doc_recall_at_k(["a", "x"], expected, k=2, min_hits=2) == 0.5
    assert m.doc_recall_at_k(["a", "b"], expected, k=2, min_hits=2) == 1.0
    # Repeated chunks from the same doc don't count twice.
    assert m.doc_recall_at_k(["a", "a", "a"], expected, k=3, min_hits=2) == 0.5


def test_doc_recall_no_expected_docs_is_vacuously_one():
    assert m.doc_recall_at_k(["a"], [], k=3) == 1.0


def test_precision_at_k():
    assert m.precision_at_k([True, False, True, False], 4) == 0.5
    assert m.precision_at_k([True, False, True, False], 2) == 0.5
    # fewer chunks than k: denominator is what was actually retrieved
    assert m.precision_at_k([True], 6) == 1.0
    assert m.precision_at_k([], 6) == 0.0


# ---- MRR / nDCG ------------------------------------------------------------


def test_reciprocal_rank():
    assert m.reciprocal_rank([True, False]) == 1.0
    assert m.reciprocal_rank([False, False, True]) == pytest.approx(1 / 3)
    assert m.reciprocal_rank([False, False]) == 0.0
    assert m.reciprocal_rank([]) == 0.0


def test_ndcg_perfect_and_degraded_ordering():
    assert m.ndcg_at_k([True, True, False, False], 4) == pytest.approx(1.0)

    flags = [False, True, False, True]
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    ideal = 1 / math.log2(2) + 1 / math.log2(3)
    assert m.ndcg_at_k(flags, 4) == pytest.approx(dcg / ideal)
    assert m.ndcg_at_k(flags, 4) < 1.0


def test_ndcg_zero_when_nothing_relevant():
    assert m.ndcg_at_k([False, False], 2) == 0.0
    assert m.ndcg_at_k([], 5) == 0.0


def test_ndcg_respects_cutoff():
    # the only relevant chunk is outside the top-2
    assert m.ndcg_at_k([False, False, True], 2) == 0.0


# ---- keyword checks --------------------------------------------------------


def test_missing_keywords_case_insensitive_and_alternatives():
    answer = "The plan is FREE and covers Identity and devices."
    assert m.missing_keywords(answer, ["free", "identity"]) == []
    assert m.missing_keywords(answer, ["multi-factor|mfa"]) == ["multi-factor|mfa"]
    assert m.missing_keywords("we use MFA", ["multi-factor|mfa"]) == []


def test_forbidden_found():
    assert m.forbidden_found("It costs $99 a month", ["$"]) == ["$"]
    assert m.forbidden_found("Pricing is not published", ["$"]) == []


def test_mean_or_none():
    assert m.mean_or_none([1.0, None, 3.0]) == 2.0
    assert m.mean_or_none([None]) is None


# ---- judge parsing ---------------------------------------------------------


def test_parse_judge_output_plain_json():
    scores = parse_judge_output(
        '{"faithfulness": 5, "relevance": 4, "completeness": 3,'
        ' "unsupported_claims": [], "reason": "ok"}'
    )
    assert (scores.faithfulness, scores.relevance, scores.completeness) == (5, 4, 3)


def test_parse_judge_output_json_wrapped_in_prose_and_clamped():
    scores = parse_judge_output(
        'Here you go: {"faithfulness": 9, "relevance": 0, "completeness": 2.6,'
        ' "unsupported_claims": ["x"], "reason": "r"} done'
    )
    assert scores.faithfulness == 5
    assert scores.relevance == 1
    assert scores.completeness == 3
    assert scores.unsupported_claims == ["x"]


@pytest.mark.parametrize("bad", ["", "not json", '{"faithfulness": 5}', "[1, 2]"])
def test_parse_judge_output_rejects_unusable_replies(bad):
    assert parse_judge_output(bad) is None


def test_build_judge_prompt_includes_all_parts():
    prompt = build_judge_prompt("Q?", ["chunk one", "chunk two"], "An answer", ["fact a"])
    for expected in ("Q?", "[1] chunk one", "[2] chunk two", "An answer", "- fact a"):
        assert expected in prompt