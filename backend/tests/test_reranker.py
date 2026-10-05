from dataclasses import dataclass

from app.retrieval import reranker


@dataclass
class _FakeChunk:
    content: str


class _FakeEncoder:
    def __init__(self, scores):
        self._scores = scores

    def rerank(self, _query, _documents):
        return self._scores


def test_rerank_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(reranker.settings, "enable_reranker", True)
    candidates = [(_FakeChunk("a"), 0.5), (_FakeChunk("b"), 0.9)]

    monkeypatch.setattr(reranker.settings, "enable_reranker", False)
    result = reranker.rerank("query", candidates)

    assert result == candidates


def test_rerank_noop_for_single_candidate(monkeypatch):
    monkeypatch.setattr(reranker.settings, "enable_reranker", True)
    candidates = [(_FakeChunk("only one"), 0.5)]

    result = reranker.rerank("query", candidates)

    assert result == candidates


def test_rerank_reorders_by_cross_encoder_but_keeps_cosine_scores(monkeypatch):
    monkeypatch.setattr(reranker.settings, "enable_reranker", True)

    low_score_chunk = _FakeChunk("mentions the topic only in passing")
    high_score_chunk = _FakeChunk("directly and specifically answers the question")

    # Cosine recall ranked the low-relevance chunk first...
    candidates = [(low_score_chunk, 0.42), (high_score_chunk, 0.40)]

    # ...but the cross-encoder correctly prefers the second document.
    monkeypatch.setattr(
        reranker, "_get_encoder", lambda: _FakeEncoder([-5.0, 8.0])
    )

    result = reranker.rerank("What directly answers this?", candidates)

    assert [chunk for chunk, _ in result] == [high_score_chunk, low_score_chunk]
    # Cosine scores travel with their chunk, unchanged by reranking.
    assert dict((id(c), s) for c, s in result) == dict((id(c), s) for c, s in candidates)


def test_rerank_falls_back_to_cosine_order_on_encoder_failure(monkeypatch):
    monkeypatch.setattr(reranker.settings, "enable_reranker", True)

    def _boom():
        raise RuntimeError("model download failed")

    monkeypatch.setattr(reranker, "_get_encoder", _boom)

    candidates = [(_FakeChunk("a"), 0.5), (_FakeChunk("b"), 0.9)]
    result = reranker.rerank("query", candidates)

    assert result == candidates
