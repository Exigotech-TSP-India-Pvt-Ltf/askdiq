"""Constrained entity-document fetch: rescues documents outside the cosine pool.

Uses the real `data/real_docs/*.md` chunks with synthetic cosines and a fake
session that emulates the main query and the entity-constrained query; no
embedding, DB or LLM call is involved.
"""

import re
import uuid

import pytest

from app.retrieval import reranker
from app.retrieval.reranker import (
    entities_missing_from_pool,
    entity_sql_pattern,
    pick_entity_documents,
)
from app.retrieval.vector_store import VectorStore

from .test_retrieval_misses import (
    CORPUS,
    DOC_IDS,
    NAME_BY_ID,
    Q_ISO,
    Q_STAGES,
    Q_TRUST,
    _cosines,
    _heading,
)

ZT = "zero-trust-learn-more.md"
ISO = "iso-27001-technical-controls.md"
TRUST = "trust-center.md"


class _Rows:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def all(self) -> list[tuple]:
        return self._rows


class _FakeSession:
    """Main query: all chunks by distance. Entity query: regex-filtered by name."""

    def __init__(self, cosine: dict[uuid.UUID, float]) -> None:
        self.rows = [
            (chunk, 1 - cosine[chunk.id], None, False, None, name)
            for name, chunks in CORPUS.items()
            for chunk in chunks
        ]
        self.main_limits: list[int] = []
        self.entity_limits: list[int] = []

    async def execute(self, stmt) -> _Rows:
        pattern = next(
            (
                value
                for value in stmt.compile().params.values()
                if isinstance(value, str) and "[^a-z0-9]" in value
            ),
            None,
        )
        ordered = sorted(self.rows, key=lambda row: row[1])
        if pattern is None:
            self.main_limits.append(stmt._limit)
            return _Rows(ordered[: stmt._limit])
        self.entity_limits.append(stmt._limit)
        matched = [
            row for row in ordered if re.search(pattern, row[5].lower(), re.IGNORECASE)
        ]
        seen: dict[uuid.UUID, int] = {}
        ranked = []
        for row in matched:  # breadth first: rank within each document
            seen[row[0].document_id] = seen.get(row[0].document_id, 0) + 1
            ranked.append((seen[row[0].document_id], row[1], row))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return _Rows([item[2] for item in ranked][: stmt._limit])

    async def commit(self) -> None:
        return None


def _generic_cosines(named: dict[str, float]) -> dict[uuid.UUID, float]:
    """Every document scores high except the named ones (low = outside pool)."""
    docs = dict.fromkeys(CORPUS, (0.7, None, 0.002))
    docs.update({name: (score, None, 0.01) for name, score in named.items()})
    return _cosines(docs)


async def _search(cosine, query, top_k=5, max_documents=None):
    session = _FakeSession(cosine)
    store = VectorStore(session)
    results = await store.similarity_search(
        [0.1, 0.2], top_k=top_k, query_text=query, max_documents=max_documents
    )
    return session, store, results


def _docs(results) -> list[str]:
    return [NAME_BY_ID[chunk.document_id] for chunk, _ in results]


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(reranker.settings, "enable_reranker", False)
    for name, value in {
        "retrieval_hybrid_enabled": True,
        "retrieval_entity_min_chunks": 2,
        "retrieval_entity_fetch_enabled": True,
        "retrieval_entity_fetch_k": 4,
    }.items():
        monkeypatch.setattr(reranker.settings, name, value)


async def test_missing_entity_doc_is_fetched_selected_and_merged():
    cosine = _generic_cosines({ZT: 0.20})
    session, store, results = await _search(cosine, Q_STAGES)

    assert session.entity_limits == [4]
    assert _docs(results).count(ZT) >= 2
    assert len(results) == 5
    # Deduped by chunk id; reported scores stay the raw cosine.
    ids = [chunk.id for chunk, _ in results]
    assert len(ids) == len(set(ids))
    assert all(score == pytest.approx(cosine[chunk.id]) for chunk, score in results)
    assert DOC_IDS[ZT] in store.document_links


async def test_not_issued_when_doc_already_represented():
    cosine = _generic_cosines({ZT: 0.80})  # top of the pool with several chunks
    session, _store, results = await _search(cosine, Q_STAGES)

    assert session.entity_limits == []
    assert _docs(results).count(ZT) >= 2


async def test_iso_and_trust_center_questions_fetch_their_documents():
    cosine = _generic_cosines({ISO: 0.20, TRUST: 0.20})
    session, _store, results = await _search(cosine, Q_ISO)
    assert len(session.entity_limits) == 1
    assert _docs(results).count(ISO) >= 2

    session, _store, results = await _search(cosine, Q_TRUST)
    assert len(session.entity_limits) == 1
    assert _docs(results).count(TRUST) >= 2


async def test_no_entity_means_no_extra_query():
    cosine = _generic_cosines({ZT: 0.20})
    session, _store, _results = await _search(cosine, "Who founded the company?")
    assert session.entity_limits == []
    assert len(session.main_limits) == 1


async def test_disabled_by_setting(monkeypatch):
    monkeypatch.setattr(reranker.settings, "retrieval_entity_fetch_enabled", False)
    session, _store, results = await _search(_generic_cosines({ZT: 0.20}), Q_STAGES)
    assert session.entity_limits == []
    assert ZT not in _docs(results)


async def test_disabled_with_hybrid_off(monkeypatch):
    monkeypatch.setattr(reranker.settings, "retrieval_hybrid_enabled", False)
    session, _store, _results = await _search(_generic_cosines({ZT: 0.20}), Q_STAGES)
    assert session.entity_limits == []


async def test_fetch_k_is_configurable(monkeypatch):
    monkeypatch.setattr(reranker.settings, "retrieval_entity_fetch_k", 2)
    session, _store, _results = await _search(_generic_cosines({ZT: 0.20}), Q_STAGES)
    assert session.entity_limits == [2]


async def test_at_most_two_entity_documents_are_kept():
    cosine = _generic_cosines({ZT: 0.20, ISO: 0.21, TRUST: 0.22})
    query = "How do Zero Trust, ISO 27001 and the Trust Center relate?"
    session, _store, results = await _search(cosine, query, top_k=6, max_documents=3)

    assert len(session.entity_limits) == 1  # still one extra round trip
    fetched_docs = {
        NAME_BY_ID[chunk.document_id] for chunk, score in results if score < 0.5
    }
    assert 1 <= len(fetched_docs) <= 2


def test_pick_entity_documents_prefers_one_per_entity_and_caps():
    names = {
        "a": "e8-learn-more.md",
        "b": "e8-service-page.md",
        "c": "iso-27001-technical-controls.md",
    }
    best = {"a": 0.9, "b": 0.8, "c": 0.1}
    picked = pick_entity_documents(["essential eight", "iso 27001"], names, best, 2)
    assert picked == ["a", "c"]
    assert pick_entity_documents(["essential eight"], names, best, 2) == ["a", "b"]


def test_entities_missing_from_pool_counts_chunks():
    doc, other = uuid.uuid4(), uuid.uuid4()
    names = {doc: "zero-trust-learn-more.md", other: "about-us.md"}

    class _C:
        def __init__(self, document_id):
            self.document_id = document_id

    one = [(_C(doc), 0.5), (_C(other), 0.4)]
    two = [(_C(doc), 0.5), (_C(doc), 0.4)]
    assert entities_missing_from_pool(["zero trust"], one, names, 2) == ["zero trust"]
    assert entities_missing_from_pool(["zero trust"], two, names, 2) == []


def test_sql_patterns_match_catalogue_documents():
    pattern = re.compile(entity_sql_pattern(["zero trust", "essential eight"]), re.I)
    assert pattern.search("zero-trust-learn-more.md ")
    assert pattern.search("e8-learn-more.md ")
    assert not pattern.search("iso-27001-technical-controls.md Decision")
    assert not re.search(entity_sql_pattern(["cis"]), "decision.md", re.I)
    assert re.search(entity_sql_pattern(["cis"]), "cis-benchmark-assessment.md", re.I)


def test_fixture_corpus_has_delivery_and_iso_chunks():
    assert "Assess" in {_heading(c) for c in CORPUS[ZT]}
    assert len(CORPUS[ISO]) >= 2
