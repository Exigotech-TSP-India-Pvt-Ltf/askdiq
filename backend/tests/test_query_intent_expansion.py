"""Query-intent expansion: question words -> document heading vocabulary."""

import uuid
from types import SimpleNamespace

import pytest

from app.retrieval import reranker
from app.retrieval.reranker import (
    HybridSignal,
    compute_hybrid_signal,
    entities_missing_from_pool,
    query_intent,
)
from app.retrieval.vector_store import VectorStore


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(reranker.settings, "retrieval_hybrid_enabled", True)
    monkeypatch.setattr(reranker.settings, "retrieval_intent_expansion_enabled", True)
    monkeypatch.setattr(reranker.settings, "retrieval_entity_min_chunks", 3)
    monkeypatch.setattr(reranker.settings, "min_retrieval_score", 0.15)


def _chunk(doc: uuid.UUID, heading: str, content: str = "text"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        document_id=doc,
        content=content,
        chunk_metadata={"section_heading": heading},
    )


@pytest.mark.parametrize(
    ("question", "term"),
    [
        ("What stages does it follow?", "assess"),
        ("What is the workflow?", "approach"),
        ("What does the service cover?", "sections"),
        ("What outcomes can I expect?", "benefits"),
        ("How much does it cost?", "starter"),
        ("Who is it for?", "designed for"),
    ],
)
def test_expansion_map(question, term):
    assert term in query_intent(question).terms


def test_spread_only_for_cover_questions():
    assert query_intent("What does X cover?").spread
    assert query_intent("Which sections does X include?").spread
    assert not query_intent("What stages does X follow?").spread
    assert not query_intent("Where is Exigo Tech headquartered?")


def test_disabled_by_setting(monkeypatch):
    monkeypatch.setattr(reranker.settings, "retrieval_intent_expansion_enabled", False)
    assert not query_intent("What stages does it follow?")


def test_match_prefers_heading_and_uses_word_boundaries():
    intent = query_intent("What stages does it follow?")
    assert intent.match("Assess", "x") > intent.match("Overview", "we assess risk")
    assert intent.match("Overview", "nothing relevant") == 0.0
    plans = query_intent("What does the plan cost?")
    assert plans.match("Free tier", "") > 0
    assert plans.match("Freedom", "") == 0.0  # short terms match whole words


def test_lexical_boost_uses_expanded_terms_for_entity_chunks(monkeypatch):
    doc = uuid.uuid4()
    names = {doc: "zero-trust-learn-more.md"}
    assess = _chunk(doc, "Assess", "We assess your tenant.")
    plain = _chunk(doc, "What Is Zero Trust?", "A security model.")
    pool = [(plain, 0.6), (assess, 0.4)]
    query = "What stages does DeployIQ follow to deliver Zero Trust?"

    boosts = compute_hybrid_signal(query, pool, names).boosts
    assert boosts[assess.id] > boosts[plain.id]

    monkeypatch.setattr(reranker.settings, "retrieval_intent_expansion_enabled", False)
    boosts = compute_hybrid_signal(query, pool, names).boosts
    assert boosts[assess.id] < boosts[plain.id]  # only the literal words count


def test_no_change_without_entity_or_intent_words():
    doc = uuid.uuid4()
    pool = [(_chunk(doc, "Our Purpose", "Exigo Tech is in Sydney."), 0.5)]
    signal = compute_hybrid_signal("Where is Exigo Tech headquartered?", pool, {})
    assert signal.entity_doc_ids == ()
    assert not signal.spread
    assert query_intent("Where is Exigo Tech headquartered?").terms == ()


def test_fetch_trigger_needs_matching_heading_or_enough_chunks():
    doc = uuid.uuid4()
    names = {doc: "zero-trust-learn-more.md"}
    three = [(_chunk(doc, h), 0.5) for h in ("A", "B", "C")]
    assert entities_missing_from_pool(["zero trust"], three, names, 3) == []
    intent = query_intent("What stages does it follow?")
    assert entities_missing_from_pool(["zero trust"], three, names, 3, intent) == [
        "zero trust"
    ]
    three.append((_chunk(doc, "Assess"), 0.4))
    assert entities_missing_from_pool(["zero trust"], three, names, 3, intent) == []


def _select(candidates, signal, top_k=5):
    return VectorStore._select_diverse_chunks(candidates, top_k, signal=signal)


def _heading_of(chunk) -> str:
    return chunk.chunk_metadata["section_heading"]


def test_diversity_cap_one_chunk_per_heading_up_to_four():
    doc = uuid.uuid4()
    headings = ["Security", "Security", "Privacy", "Audit", "Audit", "Governance"]
    chunks = [_chunk(doc, h) for h in headings]
    candidates = [(c, 0.6 - 0.01 * i) for i, c in enumerate(chunks)]
    signal = HybridSignal(entity_doc_ids=(str(doc),), spread=True)

    chosen = _select(candidates, signal, top_k=6)
    assert len({_heading_of(c) for c, _ in chosen}) == 4  # all four sections kept
    assert len(chosen) == 6

    seeded = _select(candidates, signal, top_k=5)
    # 4 seeded slots hold 4 distinct headings; the 5th is backfill.
    assert len({_heading_of(c) for c, _ in seeded}) == 4

    plain = _select(candidates, HybridSignal(entity_doc_ids=(str(doc),)), top_k=3)
    assert [_heading_of(c) for c, _ in plain].count("Security") == 2  # no cap


def test_headed_chunks_preferred_over_unheaded_when_spreading():
    doc = uuid.uuid4()
    candidates = [(_chunk(doc, ""), 0.60)] + [
        (_chunk(doc, h), 0.58 - 0.01 * i) for i, h in enumerate("ABC")
    ]
    signal = HybridSignal(entity_doc_ids=(str(doc),), spread=True)
    chosen = _select(candidates, signal, top_k=3)  # 2 seeded slots + 1 backfill
    assert {"A", "B"} <= {_heading_of(c) for c, _ in chosen}

    unspread = _select(candidates, HybridSignal(entity_doc_ids=(str(doc),)), top_k=3)
    assert _heading_of(unspread[0][0]) == ""  # plain order keeps the best score first
