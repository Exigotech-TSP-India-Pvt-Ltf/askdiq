"""Retrieval misses fixed by the hybrid lexical / named-entity signal.

The corpus is the real `data/real_docs/*.md` split by the ingestion chunker;
cosine scores are synthetic (generic documents score higher than the document
the question names), so no embedding, DB or LLM call is involved.
"""

import random
import uuid
from pathlib import Path

import pytest

from app.chunking.structure_aware import StructureAwareChunker
from app.ingestion.normalizer import normalize_markdown
from app.models.db_models import Chunk
from app.retrieval import reranker
from app.retrieval.reranker import (
    HybridSignal,
    compute_hybrid_signal,
    named_entities,
    trim_pool,
)
from app.retrieval.vector_store import VectorStore

DOCS_DIR = Path(__file__).resolve().parents[1] / "data" / "real_docs"
DOC_IDS = {
    path.name: uuid.uuid5(uuid.NAMESPACE_URL, path.name)
    for path in sorted(DOCS_DIR.glob("*.md"))
}
NAME_BY_ID = {doc_id: name for name, doc_id in DOC_IDS.items()}

Q_STAGES = "What stages does DeployIQ follow to deliver Zero Trust?"
Q_ISO = "Which parts of ISO 27001 does DeployIQ leave to the customer?"
Q_TRUST = "What does the Trust Center cover?"


def _corpus() -> dict[str, list[Chunk]]:
    corpus: dict[str, list[Chunk]] = {}
    for name, doc_id in DOC_IDS.items():
        text, _ = normalize_markdown((DOCS_DIR / name).read_bytes())
        pieces = StructureAwareChunker().chunk_sync(text, {})
        corpus[name] = [
            Chunk(
                id=uuid.uuid5(doc_id, str(index)),
                document_id=doc_id,
                content=piece.content,
                embedding=[0.0, 0.0],
                strategy="structure_aware",
                chunk_index=index,
                chunk_metadata=dict(piece.metadata),
            )
            for index, piece in enumerate(pieces)
        ]
    return corpus


CORPUS = _corpus()


def _heading(chunk: Chunk) -> str:
    return chunk.chunk_metadata.get("section_heading") or ""


class _Rows:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def all(self) -> list[tuple]:
        return self._rows


class _FakeSession:
    """Returns the synthetic corpus ordered by distance, honouring LIMIT."""

    def __init__(self, cosine: dict[uuid.UUID, float], shuffle: bool = False) -> None:
        rows = [
            (chunk, 1 - cosine[chunk.id], None, False, None, name)
            for name, chunks in CORPUS.items()
            for chunk in chunks
        ]
        if shuffle:
            random.Random(7).shuffle(rows)  # noqa: S311 - deterministic test shuffle
        self._rows = rows

    async def execute(self, stmt) -> _Rows:
        ordered = sorted(self._rows, key=lambda row: row[1])
        return _Rows(ordered[: stmt._limit])

    async def commit(self) -> None:
        return None


def _cosines(docs: dict[str, tuple], default: float = 0.30) -> dict[uuid.UUID, float]:
    """doc -> (best score, best heading[, step]); other chunks step down."""
    scores: dict[uuid.UUID, float] = {}
    for name, chunks in CORPUS.items():
        base, best_heading, *rest = docs.get(name, (default, None))
        step = rest[0] if rest else 0.03
        rank = 0
        for chunk in chunks:
            if best_heading and _heading(chunk) == best_heading:
                scores[chunk.id] = base
            else:
                rank += 1
                scores[chunk.id] = base - step * rank - 0.0001 * chunk.chunk_index
    return scores


async def _search(
    cosine,
    query: str,
    top_k: int = 5,
    max_documents: int | None = None,
    shuffle: bool = False,
) -> list[tuple[Chunk, float]]:
    store = VectorStore(_FakeSession(cosine, shuffle=shuffle))
    results = await store.similarity_search(
        [0.1, 0.2], top_k=top_k, query_text=query, max_documents=max_documents
    )
    return sorted(
        results,
        key=lambda item: item[1] + store.rank_boosts.get(item[0].id, 0.0),
        reverse=True,
    )


@pytest.fixture(autouse=True)
def _hybrid_defaults(monkeypatch):
    monkeypatch.setattr(reranker.settings, "enable_reranker", False)
    monkeypatch.setattr(reranker.settings, "retrieval_hybrid_enabled", True)
    monkeypatch.setattr(reranker.settings, "retrieval_lexical_boost", 0.08)
    monkeypatch.setattr(reranker.settings, "retrieval_entity_doc_boost", 0.10)
    monkeypatch.setattr(reranker.settings, "retrieval_entity_min_chunks", 2)
    monkeypatch.setattr(reranker.settings, "retrieval_lexical_pool_extra", 8)


def _docs(results) -> list[str]:
    return [NAME_BY_ID[chunk.document_id] for chunk, _ in results]


# Scenario cosines: generic documents beat the named document.
STAGES_COS = _cosines(
    {
        "about-us.md": (0.66, "How DeployIQ Works"),
        "home-page.md": (0.60, None),
        "deploy-iq-platform.md": (0.58, None),
        "zero-trust-learn-more.md": (0.55, "Assess"),
    }
)
ISO_COS = _cosines(
    {
        "home-page.md": (0.64, None),
        "trust-center.md": (0.62, None),
        "about-us.md": (0.58, None),
        "iso-27001-technical-controls.md": (0.54, None),
    }
)
TRUST_COS = _cosines(
    {
        "trust-center.md": (0.60, "Our Approach to Trust", 0.08),
        "home-page.md": (0.58, None),
        "about-us.md": (0.56, None),
        "deploy-iq-platform.md": (0.54, None),
    }
)


def test_corpus_has_the_expected_sections():
    zt = [_heading(c) for c in CORPUS["zero-trust-learn-more.md"]]
    assert {"Assess", "Prioritise", "Design", "Implement"} <= set(zt)
    assert len(CORPUS["trust-center.md"]) >= 3
    assert len(CORPUS["iso-27001-technical-controls.md"]) >= 2


async def test_miss_1_delivery_stages_retrieves_zero_trust_doc(monkeypatch):
    monkeypatch.setattr(reranker.settings, "retrieval_hybrid_enabled", False)
    before = await _search(STAGES_COS, Q_STAGES)
    assert _docs(before)[0] != "zero-trust-learn-more.md"  # repro

    monkeypatch.setattr(reranker.settings, "retrieval_hybrid_enabled", True)
    after = await _search(STAGES_COS, Q_STAGES)

    assert _docs(after)[0] == "zero-trust-learn-more.md"
    assert _heading(after[0][0]) == "Assess"
    assert _docs(after).count("zero-trust-learn-more.md") >= 2
    # Reported score is still the raw cosine.
    assert after[0][1] == pytest.approx(0.55)


async def test_miss_2_iso_customer_responsibilities_retrieves_iso_doc(monkeypatch):
    monkeypatch.setattr(reranker.settings, "retrieval_hybrid_enabled", False)
    before = await _search(ISO_COS, Q_ISO)
    assert "iso-27001-technical-controls.md" not in _docs(before)  # repro

    monkeypatch.setattr(reranker.settings, "retrieval_hybrid_enabled", True)
    after = await _search(ISO_COS, Q_ISO)

    assert _docs(after)[0] == "iso-27001-technical-controls.md"
    assert _docs(after).count("iso-27001-technical-controls.md") >= 2


async def test_miss_3_trust_center_gets_more_than_the_intro_chunk(monkeypatch):
    monkeypatch.setattr(reranker.settings, "retrieval_hybrid_enabled", False)
    before = await _search(TRUST_COS, Q_TRUST)
    assert _docs(before).count("trust-center.md") == 2  # repro: thin context

    monkeypatch.setattr(reranker.settings, "retrieval_hybrid_enabled", True)
    after = await _search(TRUST_COS, Q_TRUST)

    assert _docs(after)[0] == "trust-center.md"
    assert _docs(after).count("trust-center.md") >= 3


async def test_query_without_entity_is_unchanged(monkeypatch):
    query = "Tell me about quarterly zebra migration"
    assert named_entities(query) == []

    cosine = STAGES_COS
    on = await _search(cosine, query)
    monkeypatch.setattr(reranker.settings, "retrieval_hybrid_enabled", False)
    off = await _search(cosine, query)

    assert [c.id for c, _ in on] == [c.id for c, _ in off]


def test_no_entity_means_no_entity_boost():
    pool = [(chunk, 0.5) for chunk in CORPUS["about-us.md"]]
    names = {DOC_IDS["about-us.md"]: "about-us.md"}
    signal = compute_hybrid_signal("Who is the team behind this?", pool, names)
    assert signal.entity_doc_ids == ()
    assert all(boost <= 0.08 + 1e-9 for boost in signal.boosts.values())


async def test_ordering_is_deterministic_and_input_order_independent():
    first = await _search(STAGES_COS, Q_STAGES)
    second = await _search(STAGES_COS, Q_STAGES)
    shuffled = await _search(STAGES_COS, Q_STAGES, shuffle=True)

    assert [c.id for c, _ in first] == [c.id for c, _ in second]
    assert [c.id for c, _ in first] == [c.id for c, _ in shuffled]


def _chunk(doc_id: uuid.UUID, index: int, content: str = "x") -> Chunk:
    return Chunk(
        id=uuid.uuid5(doc_id, f"c{index}"),
        document_id=doc_id,
        content=content,
        embedding=[0.0, 0.0],
        strategy="structure_aware",
        chunk_index=index,
        chunk_metadata={},
    )


def test_entity_guarantee_respects_max_documents():
    zt, e8, iso, other = (uuid.uuid4() for _ in range(4))
    pool = (
        [(_chunk(other, i), 0.9 - 0.01 * i) for i in range(4)]
        + [(_chunk(zt, i), 0.5 - 0.01 * i) for i in range(3)]
        + [(_chunk(e8, i), 0.45 - 0.01 * i) for i in range(3)]
        + [(_chunk(iso, i), 0.4 - 0.01 * i) for i in range(3)]
    )
    signal = HybridSignal(entity_doc_ids=(str(zt), str(e8), str(iso)))

    selected = VectorStore._select_diverse_chunks(
        pool, top_k=4, max_documents=2, signal=signal
    )
    docs = [str(c.document_id) for c, _ in selected]

    # Named documents fill the cap; the unnamed higher-scoring one is not
    # kept ahead of them, and the cap-excluded third entity is not forced in.
    assert str(zt) in docs
    assert str(e8) in docs
    assert docs.count(str(zt)) >= 2
    assert str(iso) not in docs[:2]


def test_entity_doc_never_dropped_by_document_cap():
    entity, a, b, c = (uuid.uuid4() for _ in range(4))
    pool = [
        (_chunk(d, i), 0.9 - 0.05 * n - 0.01 * i)
        for n, d in enumerate((a, b, c))
        for i in range(3)
    ] + [(_chunk(entity, i), 0.4 - 0.01 * i) for i in range(3)]
    legacy = VectorStore._select_diverse_chunks(pool, top_k=3, max_documents=3)
    assert str(entity) not in {str(ch.document_id) for ch, _ in legacy}

    signal = HybridSignal(entity_doc_ids=(str(entity),))
    guarded = VectorStore._select_diverse_chunks(
        pool, top_k=3, max_documents=3, signal=signal
    )
    docs = [str(ch.document_id) for ch, _ in guarded]
    assert docs.count(str(entity)) >= 2


def test_entity_guarantee_skips_chunks_below_the_floor(monkeypatch):
    monkeypatch.setattr(reranker.settings, "min_retrieval_score", 0.15)
    entity, other = uuid.uuid4(), uuid.uuid4()
    pool = [(_chunk(other, i), 0.8 - 0.01 * i) for i in range(5)] + [
        (_chunk(entity, 0), 0.30),
        (_chunk(entity, 1), 0.05),  # below the floor: not forced in
    ]
    signal = HybridSignal(entity_doc_ids=(str(entity),))

    selected = VectorStore._select_diverse_chunks(pool, top_k=3, signal=signal)
    entity_chunks = [c for c, _ in selected if c.document_id == entity]

    assert [c.chunk_index for c in entity_chunks] == [0]


def test_trim_pool_keeps_entity_document_chunks():
    entity, other = uuid.uuid4(), uuid.uuid4()
    pool = [(_chunk(other, i), 0.9 - 0.01 * i) for i in range(10)] + [
        (_chunk(entity, 0), 0.40),
        (_chunk(entity, 1), 0.39),
    ]
    signal = HybridSignal(entity_doc_ids=(str(entity),))  # no boosts at all

    trimmed = trim_pool(pool, signal, 8)

    assert len(trimmed) == 8
    assert sum(1 for c, _ in trimmed if c.document_id == entity) == 2


async def test_pool_size_for_rerank_is_capped(monkeypatch):
    seen: list[int] = []

    def _spy(_query, candidates, _boosts=None):
        seen.append(len(candidates))
        return candidates

    monkeypatch.setattr("app.retrieval.vector_store.rerank", _spy)
    await _search(STAGES_COS, Q_STAGES)

    assert seen == [
        max(
            5 * reranker.settings.retrieval_candidate_multiplier,
            reranker.settings.retrieval_candidate_min,
        )
    ]


async def test_boosts_are_configurable(monkeypatch):
    monkeypatch.setattr(reranker.settings, "retrieval_lexical_boost", 0.0)
    monkeypatch.setattr(reranker.settings, "retrieval_entity_doc_boost", 0.0)
    pool = [(c, 0.5) for c in CORPUS["zero-trust-learn-more.md"]]
    names = {DOC_IDS["zero-trust-learn-more.md"]: "zero-trust-learn-more.md"}

    assert compute_hybrid_signal(Q_STAGES, pool, names).boosts == {}

    monkeypatch.setattr(reranker.settings, "retrieval_entity_doc_boost", 0.25)
    boosts = compute_hybrid_signal(Q_STAGES, pool, names).boosts
    assert set(boosts.values()) == {0.25}

    # Explicit arguments override the settings.
    boosts = compute_hybrid_signal(Q_STAGES, pool, names, entity_doc_boost=0.1).boosts
    assert set(boosts.values()) == {0.1}

    monkeypatch.setattr(reranker.settings, "retrieval_hybrid_enabled", False)
    assert not compute_hybrid_signal(Q_STAGES, pool, names).boosts


def test_lexical_boost_is_bounded_and_prefers_matching_heading():
    pool = [(c, 0.5) for c in CORPUS["zero-trust-learn-more.md"]]
    names = {DOC_IDS["zero-trust-learn-more.md"]: "zero-trust-learn-more.md"}
    signal = compute_hybrid_signal(
        "How are controls prioritised based on threat likelihood and exposure?",
        pool,
        names,
        entity_doc_boost=0.0,
    )

    assert max(signal.boosts.values()) <= 0.08 + 1e-9
    best = max(pool, key=lambda item: signal.boosts.get(item[0].id, 0.0))[0]
    assert _heading(best) == "Prioritise"


def test_named_entities_catalogue():
    assert named_entities("Is DeployIQ ISO 27001 certified?") == ["iso 27001"]
    assert named_entities("What is E8 and Zero-Trust?") == [
        "zero trust",
        "essential eight",
    ]
    assert "trust center" in named_entities("Tell me about the Trust Centre")
    assert "cis" in named_entities("What does the CIS assessment cover?")
    assert "security baseline" in named_entities("security baseline assessment")
    assert named_entities("What is DeployIQ?") == []


class _FakeEncoder:
    def __init__(self, scores):
        self._scores = scores

    def rerank(self, _query, _documents, batch_size=None):
        return self._scores


def test_rerank_applies_hybrid_boosts_after_cross_encoder(monkeypatch):
    monkeypatch.setattr(reranker.settings, "enable_reranker", True)
    monkeypatch.setattr(reranker, "_get_encoder", lambda: _FakeEncoder([2.0, 1.0]))
    doc = uuid.uuid4()
    generic, named = _chunk(doc, 0), _chunk(doc, 1)
    pool = [(generic, 0.6), (named, 0.5)]

    assert [c for c, _ in reranker.rerank("q", pool)] == [generic, named]
    assert [c for c, _ in reranker.rerank("q", pool, [0.0, 0.0])] == [generic, named]
    assert [c for c, _ in reranker.rerank("q", pool, [0.0, 1.2])] == [named, generic]
