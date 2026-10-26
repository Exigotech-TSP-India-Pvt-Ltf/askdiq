"""Sanity checks that catch typos in the evaluation dataset itself."""

from collections import Counter

from app.evaluation.dataset import ALL_DOCS, EVAL_CASES


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
        if case.expected_keywords or case.forbidden_keywords:
            assert case.expect_grounded or case.expect_abstain, (
                f"{case.id}: keyword checks only run on grounded answers"
            )


def test_blocked_cases_expect_a_flag_and_no_grounding():
    for case in EVAL_CASES:
        if case.case_type in {"unsafe", "spam", "off_topic"}:
            assert case.expect_grounded is False, case.id
            if not case.expect_abstain:
                assert case.expect_flags_any, case.id


def test_known_issues_have_a_reason():
    for case in EVAL_CASES:
        if case.known_issue is not None:
            assert case.known_issue.strip(), case.id


def test_zero_trust_starter_free_case_has_no_stale_known_issue():
    case = next(case for case in EVAL_CASES if case.id == "fp_100_percent_free")

    assert case.known_issue is None


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
    cats = {c.abstain_category for c in EVAL_CASES if c.expect_abstain}
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
        "repeated_question",
        "long_conversation_history",
    } <= perts
