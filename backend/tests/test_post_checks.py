import pytest

from app.guardrails import post_checks
from app.guardrails.post_checks import (
    _cosine_similarity,
    _NEGATIVE_ANSWER_RE,
    _unsupported_figures,
    check_groundedness,
    check_has_sources,
    run_post_checks,
)
from app.schemas.query import SourceChunk


def _source(content: str, score: float = 0.8) -> SourceChunk:
    return SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        content=content,
        score=score,
        metadata={},
    )


# ---------------------------------------------------------------------
# Negative-answer regex
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "The documents do not specify exact pricing.",
        "This information is not provided in the supplied context.",
        "Certifications are not yet claimed.",
        "There is no information about that in the documents.",
    ],
)
def test_negative_answer_regex_matches_declines(answer):
    assert _NEGATIVE_ANSWER_RE.search(answer) is not None


@pytest.mark.parametrize(
    "answer",
    [
        "Deploy IQ manages Essential Eight compliance end-to-end.",
        "Full transparency into what the system is doing and why.",
        "Deploy IQ applies Zero Trust principles across identity and devices.",
    ],
)
def test_negative_answer_regex_does_not_match_positive_answers(answer):
    assert _NEGATIVE_ANSWER_RE.search(answer) is None


# ---------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------


def test_cosine_similarity_identical_vectors_is_one():
    assert _cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_is_zero():
    assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_zero_vector_is_zero():
    assert _cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


# ---------------------------------------------------------------------
# check_has_sources
# ---------------------------------------------------------------------


def test_check_has_sources_fails_when_empty():
    ok, flag = check_has_sources([], min_score=0.3)
    assert ok is False
    assert flag == "no_retrieved_sources"


def test_check_has_sources_fails_below_threshold():
    ok, flag = check_has_sources([_source("x", score=0.1)], min_score=0.3)
    assert ok is False
    assert flag == "low_retrieval_confidence"


def test_check_has_sources_passes_above_threshold():
    ok, flag = check_has_sources([_source("x", score=0.5)], min_score=0.3)
    assert ok is True
    assert flag is None


# ---------------------------------------------------------------------
# check_groundedness (embeddings mocked — no real Azure calls)
# ---------------------------------------------------------------------


def test_check_groundedness_empty_answer():
    grounded, confidence, flag = check_groundedness("", [_source("x")], 0.55)
    assert grounded is False
    assert confidence == 0.0
    assert flag == "empty_answer"


def test_check_groundedness_negative_answer_is_grounded_by_definition(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("should not embed a declined answer")

    monkeypatch.setattr(post_checks, "embed_texts_sync", _boom)

    grounded, confidence, flag = check_groundedness(
        "The documents do not provide that information.",
        [_source("x")],
        0.55,
    )
    assert grounded is True
    assert confidence == 1.0
    assert flag is None


def test_check_groundedness_high_similarity_passes(monkeypatch):
    def _fake_embed(texts):
        # Same vector for answer and source -> cosine similarity 1.0.
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(post_checks, "embed_texts_sync", _fake_embed)

    grounded, confidence, flag = check_groundedness(
        "Deploy IQ manages Essential Eight compliance end-to-end.",
        [_source("Essential Eight content")],
        0.55,
    )
    assert grounded is True
    assert confidence == pytest.approx(1.0)
    assert flag is None


def test_check_groundedness_low_similarity_flags_hallucination(monkeypatch):
    calls = {"n": 0}

    def _fake_embed(texts):
        calls["n"] += 1
        # First call = answer embedding, second = source embeddings.
        return [[1.0, 0.0]] if calls["n"] == 1 else [[0.0, 1.0]]

    monkeypatch.setattr(post_checks, "embed_texts_sync", _fake_embed)

    grounded, confidence, flag = check_groundedness(
        "Something unrelated to the sources.",
        [_source("Unrelated source content")],
        0.55,
    )
    assert grounded is False
    assert confidence == pytest.approx(0.0)
    assert flag == "possible_hallucination"


def test_check_groundedness_embedding_failure_is_ungrounded(monkeypatch):
    def _boom(_texts):
        raise RuntimeError("Azure unavailable")

    monkeypatch.setattr(post_checks, "embed_texts_sync", _boom)

    grounded, confidence, flag = check_groundedness(
        "Some answer.", [_source("x")], 0.55
    )
    assert grounded is False
    assert confidence == 0.0
    assert flag == "groundedness_check_failed"


def test_check_groundedness_reuses_precomputed_source_embeddings(monkeypatch):
    # Sources already carrying an embedding (as retriever.py populates them
    # from the ingested chunk) must NOT be re-embedded — only the new
    # answer text needs a fresh embedding call.
    calls = {"n": 0, "texts": []}

    def _fake_embed(texts):
        calls["n"] += 1
        calls["texts"].append(list(texts))
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(post_checks, "embed_texts_sync", _fake_embed)

    source_with_embedding = SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        content="Essential Eight content",
        score=0.8,
        metadata={},
        embedding=[1.0, 0.0, 0.0],
    )

    grounded, confidence, flag = check_groundedness(
        "Deploy IQ manages Essential Eight compliance end-to-end.",
        [source_with_embedding],
        0.55,
    )

    assert grounded is True
    assert confidence == pytest.approx(1.0)
    assert flag is None
    # Only the answer was embedded — the source's precomputed embedding was reused.
    assert calls["n"] == 1
    assert calls["texts"] == [
        ["Deploy IQ manages Essential Eight compliance end-to-end."]
    ]


# ---------------------------------------------------------------------
# Unsupported $/% figure anchor check
# ---------------------------------------------------------------------


def test_unsupported_figures_flags_invented_dollar_amount():
    result = _unsupported_figures(
        "The service costs $50,000 per year.",
        "Deploy IQ delivers Essential Eight controls with no published pricing.",
    )
    assert result == ["$50,000"]


def test_unsupported_figures_allows_amount_present_in_source():
    result = _unsupported_figures(
        "The Zero Trust Starter plan is $0 (free).",
        "Zero Trust – Starter (Free) plan: $0 self-service tier.",
    )
    assert result == []


def test_unsupported_figures_ignores_small_bare_numbers():
    # Bare small numbers (list counts, etc.) are intentionally out of scope —
    # only $/% figures are checked, to avoid word/digit mismatch false positives.
    result = _unsupported_figures(
        "Deploy IQ covers 3 pillars of Zero Trust.",
        "Deploy IQ covers three pillars of Zero Trust.",
    )
    assert result == []


def test_unsupported_figures_flags_invented_percentage():
    result = _unsupported_figures(
        "This reduces risk by 42%.",
        "Deploy IQ reduces cyber risk through governed controls.",
    )
    assert result == ["42%"]


def test_check_groundedness_rejects_invented_dollar_figure_even_if_on_topic(
    monkeypatch,
):
    # Same-vector embeddings would otherwise pass the semantic check —
    # the figure anchor must catch this independently, before embedding.
    def _fail_if_called(*_a, **_k):
        raise AssertionError("should short-circuit before embedding")

    monkeypatch.setattr(post_checks, "embed_texts_sync", _fail_if_called)

    grounded, confidence, flag = check_groundedness(
        "Essential Eight implementation costs $12,500.",
        [_source("Deploy IQ delivers Essential Eight with no published pricing.")],
        0.55,
    )
    assert grounded is False
    assert confidence == 0.0
    assert flag == "unsupported_numeric_claim"


# ---------------------------------------------------------------------
# run_post_checks (combines both checks)
# ---------------------------------------------------------------------


def test_run_post_checks_fails_fast_with_no_sources():
    grounded, confidence, flags = run_post_checks("answer", [], 0.55)
    assert grounded is False
    assert confidence == 0.0
    assert flags == ["no_retrieved_sources"]


def test_run_post_checks_grounded_end_to_end(monkeypatch):
    monkeypatch.setattr(
        post_checks, "embed_texts_sync", lambda texts: [[1.0, 0.0] for _ in texts]
    )

    grounded, confidence, flags = run_post_checks(
        "Deploy IQ does X.", [_source("Deploy IQ does X.", score=0.9)], 0.55
    )
    assert grounded is True
    assert flags == []
