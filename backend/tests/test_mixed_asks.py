"""Mixed asks: one part in the docs, another not; abbreviation expansion.

Fake retrieve / LLM stubs only: no embedding, DB or LLM call.
"""

import types
import uuid

import pytest

from app.harness import orchestrator
from app.retrieval import retriever
from app.retrieval.retriever import (
    PartRetrieval,
    decompose_query,
    expand_retrieval_query,
    part_retrieval_sink,
)
from app.schemas.query import SourceChunk

PRODUCT = "Deploy IQ is the Exigo Tech platform for the Essential Eight."
ABOUT = "Exigo Tech is headquartered in Australia."


def _source(cid: str, content: str, score: float = 0.6) -> SourceChunk:
    return SourceChunk(
        chunk_id=cid,
        document_id=f"doc-{cid}",
        content=content,
        score=score,
        metadata={"source_name": f"{cid}.md"},
    )


def _part(query: str, score: float, cid: str, content: str) -> PartRetrieval:
    return PartRetrieval(query, score, frozenset({cid}), (content,))


async def _run(
    monkeypatch, *, query, sources, parts, answer="Deploy IQ is a platform [1]."
):
    monkeypatch.setattr(orchestrator, "run_pre_checks", lambda *_a, **_k: [])
    seen: dict[str, object] = {}

    async def fake_retrieve(*_a, **_k):
        sink = part_retrieval_sink.get()
        assert sink is not None
        sink[:] = parts
        return sources

    async def fake_call_llm(llm_query, chunks, _history=None, **_k):
        seen["llm_query"] = llm_query
        seen["chunks"] = [c.chunk_id for c in chunks]
        return answer, {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    async def fake_post_checks(*_a, **_k):
        return True, 0.9, []

    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    monkeypatch.setattr(orchestrator, "call_llm", fake_call_llm)
    monkeypatch.setattr(orchestrator, "run_post_checks", fake_post_checks)
    response = await orchestrator.run_query(
        query=query,
        top_k=6,
        filters=None,
        vector_store=None,
        request_id="t",
        stage_timings={},
    )
    return response, seen


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("e8 ml3??", "Essential Eight maturity level three??"),
        ("e8 ml2", "Essential Eight maturity level two"),
        ("ml1", "maturity level one"),
        ("maturity level 3 e8", "maturity level three Essential Eight"),
        (
            "what is the Essential Eight ml3",
            "what is the Essential Eight maturity level three",
        ),
        ("What is Deploy IQ?", "What is Deploy IQ?"),
    ],
)
def test_expand_retrieval_query_abbreviations(raw, expected):
    assert expand_retrieval_query(raw) == expected


def test_expand_retrieval_query_adds_company_terms_for_location():
    out = expand_retrieval_query("where are you based")
    assert out.startswith("where are you based")
    assert "headquarters" in out
    assert out == expand_retrieval_query(out)


@pytest.mark.parametrize(
    ("query", "count"),
    [
        ("can u tell me about ur product and also the weather", 2),
        ("how much is zero trust and where are you based", 2),
        ("what is essential eight and who is the CEO", 2),
        ("umm so like what is this thing and can it fix my laptop", 2),
        ("e8 ml3??", 1),
        ("WHAT IS THE ESSENTIAL EIGHT???", 1),
        ("tell me about SSO and MFA support please", 1),
        ("what does the platform also include for backups", 1),
    ],
)
def test_decompose_mixed_asks(query, count):
    assert len(decompose_query(query)) == count


async def test_where_are_you_based_part_retrieves_company_chunk(monkeypatch):
    about = types.SimpleNamespace(
        id=uuid.uuid4(), document_id=uuid.uuid4(), content=ABOUT, chunk_metadata={}
    )
    zt = types.SimpleNamespace(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        content="Zero Trust pricing tiers.",
        chunk_metadata={},
    )
    searched: list[str] = []

    class FakeStore:
        document_links: dict = {}  # noqa: RUF012
        rank_boosts: dict = {}  # noqa: RUF012

        async def similarity_search(self, _emb, *, query_text, **_k):
            searched.append(query_text)
            if "headquarters" in query_text:
                return [(about, 0.55), (zt, 0.2)]
            return [(zt, 0.6)]

        async def fetch_embeddings(self, _ids):
            return {}

    async def fake_embed(_q):
        return [0.0]

    async def fake_embed_many(texts, **_k):
        return [[0.0] for _ in texts]

    monkeypatch.setattr(retriever, "embed_query", fake_embed)
    monkeypatch.setattr(retriever, "embed_texts", fake_embed_many)
    sink: list[PartRetrieval] = []
    token = part_retrieval_sink.set(sink)
    try:
        sources = await retriever.retrieve(
            FakeStore(), "how much is zero trust and where are you based", top_k=5
        )
    finally:
        part_retrieval_sink.reset(token)

    assert len(searched) == 2
    assert [p.query for p in sink] == ["how much is zero trust", "where are you based"]
    assert sink[1].max_score == 0.55
    assert str(about.id) in sink[1].chunk_ids
    assert any(s.content == ABOUT for s in sources)
