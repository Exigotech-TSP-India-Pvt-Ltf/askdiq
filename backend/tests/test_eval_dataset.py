"""Sanity checks that catch typos in the evaluation dataset itself."""

from collections import Counter
from pathlib import Path

from app.evaluation.dataset import ALL_DOCS, EVAL_CASES
from app.evaluation.metrics import _matches


def test_case_ids_are_unique():
    duplicates = [i for i, n in Counter(c.id for c in EVAL_CASES).items() if n > 1]
    assert not duplicates, f"duplicate case ids: {duplicates}"


def test_expected_docs_exist_in_corpus():
    for case in EVAL_CASES:
        unknown = set(case.expected_docs) - ALL_DOCS
        assert not unknown, f"{case.id}: unknown docs {unknown}"


def test_min_hits_is_satisfiable():
    for case in EVAL_CASES:
        if case.expected_docs:
            assert 1 <= case.min_expected_docs_hit <= len(case.expected_docs), case.id


def test_history_roles_and_nonempty_content():
    for case in EVAL_CASES:
        for role, content in case.history:
            assert role in {"user", "assistant"}, case.id
            assert content.strip(), case.id


def test_headings_and_keywords_need_expected_docs_or_a_real_answer():
    for case in EVAL_CASES:
        if case.expected_headings:
            assert case.expected_docs, f"{case.id}: headings without expected_docs"
        # Updated: keywords are now checked on every non-empty reply (fallbacks
        # and refusals included), so only blocked cases are exempt.
        if case.case_type in {"unsafe", "spam"}:
            assert not case.expected_keywords, (
                f"{case.id}: blocked cases return a canned reply"
            )


def test_blocked_cases_expect_a_flag_and_no_grounding():
    for case in EVAL_CASES:
        if case.case_type in {"unsafe", "spam", "off_topic"}:
            assert case.expect_grounded is False, case.id
            assert case.expect_flags_any, case.id


def test_known_issues_have_a_reason():
    for case in EVAL_CASES:
        if case.known_issue is not None:
            assert case.known_issue.strip(), case.id


def test_dataset_is_bigger_than_the_original_baseline():
    assert len(EVAL_CASES) >= 40
    # the original 13 stay first, so old runs remain comparable
    assert [c.id for c in EVAL_CASES[:3]] == [
        "deploy_iq_overview",
        "essential_eight",
        "zero_trust",
    ]


def test_variants_point_at_existing_cases_and_abstentions_are_ungrounded():
    ids = {c.id for c in EVAL_CASES}
    for case in EVAL_CASES:
        if case.variant_of:
            assert case.variant_of in ids, case.id
            assert case.perturbation, case.id
        if case.expect_abstain:
            assert case.expect_grounded is False, case.id


def test_extended_categories_are_covered():
    # Updated: partial / ambiguous / contradictory cases no longer expect a
    # refusal, so the category is read from every case that carries one.
    cats = {c.abstain_category for c in EVAL_CASES if c.abstain_category}
    assert {
        "not_in_corpus",
        "partially_answerable",
        "ambiguous",
        "contradictory_documents",
        "out_of_scope",
        "real_time",
        "no_permission",
    } <= cats
    perts = {c.perturbation for c in EVAL_CASES if c.perturbation}
    assert {
        "typo",
        "short_vague",
        "very_long",
        "acronym",
        "synonym",
        "long_conversation_history",
    } <= perts
    # "repeated_question" variants were identical to their base case and are
    # gone; repeat consistency comes from run_evaluation.py --repeat.
    assert "repeated_question" not in perts


_DOCS_DIR = Path(__file__).resolve().parents[1] / "data" / "real_docs"
_NO_KB_KEYWORDS = {"negative", "abstention", "safety", "clarification", "spam"}


def _kb_text(names: list[str]) -> str:
    # real_docs markdown backslash-escapes punctuation ("v7\.0\.0")
    return "\n".join(
        (_DOCS_DIR / n).read_text(encoding="utf-8").replace("\\", "") for n in names
    )


def test_v2_expected_keywords_occur_in_expected_docs():
    """Expectations must be KB facts, so they cannot silently drift again."""
    problems = []
    for case in EVAL_CASES:
        if (
            not case.id.startswith("v2_")
            or case.case_type in _NO_KB_KEYWORDS
            or not case.expected_docs
        ):
            continue
        text = _kb_text(case.expected_docs)
        problems += [
            f"{case.id}: {spec!r} not in {case.expected_docs}"
            for spec in case.expected_keywords
            if not _matches(text, spec)
        ]
    assert not problems, problems


def test_replay_turns_returns_the_history_user_turns_in_order():
    case = next(c for c in EVAL_CASES if c.id == "rob_long_history_followup")
    turns = case.replay_turns()
    assert turns == [c for r, c in case.history if r == "user"]
    assert turns
    assert (
        next(c for c in EVAL_CASES if c.id == "v2_company_based").replay_turns() == []
    )
