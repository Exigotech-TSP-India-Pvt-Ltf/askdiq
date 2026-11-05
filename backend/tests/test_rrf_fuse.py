"""Reciprocal Rank Fusion of the cosine and full-text candidate lists."""

import uuid
from types import SimpleNamespace

from app.retrieval.vector_store import rrf_fuse


def _item(score: float) -> tuple:
    return (SimpleNamespace(id=uuid.uuid4()), score)


def test_fts_hit_in_both_lists_leads_and_fts_only_hit_joins() -> None:
    a, b, c = _item(0.9), _item(0.8), _item(0.3)
    fused = rrf_fuse([a, b], [b, c])
    assert [item[0].id for item in fused] == [b[0].id, a[0].id, c[0].id]
    assert fused[2][1] == 0.3  # FTS-only chunk keeps its cosine score


def test_empty_fts_keeps_cosine_order() -> None:
    a, b = _item(0.9), _item(0.8)
    assert rrf_fuse([a, b], []) == [a, b]
