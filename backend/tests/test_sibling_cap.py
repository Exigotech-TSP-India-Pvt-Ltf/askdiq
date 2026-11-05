"""with_section_siblings: expands a section within the context cap and never
drops a chunk retrieval chose (a 3-part ask keeps its contact chunk)."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.retrieval.retriever import with_section_siblings
from app.schemas.query import SourceChunk


def _chunk(doc: str, path: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        document_id=doc,
        content="/".join(path),
        chunk_metadata={"section_path": path},
    )


def _src(chunk: SimpleNamespace, score: float = 0.5) -> SourceChunk:
    return SourceChunk(
        chunk_id=str(chunk.id),
        document_id=str(chunk.document_id),
        content=chunk.content,
        score=score,
        metadata=chunk.chunk_metadata,
    )


class _Store:
    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self.chunks = chunks
        self.document_links: dict = {}

    async def document_chunks(self, document_id: str) -> list[SimpleNamespace]:
        return [c for c in self.chunks if c.document_id == document_id]


def _plain(n: int) -> list[SourceChunk]:
    return [_src(_chunk("x", [f"p{i}"])) for i in range(n)]


def test_nothing_expands_returns_sources_unchanged() -> None:
    sources = _plain(12)
    out = asyncio.run(with_section_siblings(_Store([]), sources))
    assert out is sources


def test_group_expands_in_page_order_within_the_cap() -> None:
    steps = [_chunk("a", ["Steps", f"{i:02d}"]) for i in range(1, 6)]
    sources = [_src(steps[4]), _src(steps[2]), *_plain(2)]
    out = asyncio.run(with_section_siblings(_Store(steps), sources))
    assert [s.chunk_id for s in out[:5]] == [str(c.id) for c in steps]
    assert out[5:] == sources[2:]


def test_expansion_over_the_cap_is_skipped_not_trimmed() -> None:
    pillars = [_chunk("b", ["Pillars", f"P{i}"]) for i in range(1, 8)]
    sources = [_src(pillars[0]), _src(pillars[1]), *_plain(6)]
    out = asyncio.run(with_section_siblings(_Store(pillars), sources))
    assert out == sources  # 8 + 5 more > 10: nothing retrieval chose is lost


def test_a_multi_part_ask_keeps_every_retrieved_chunk() -> None:
    steps = [_chunk("a", ["Steps", f"{i:02d}"]) for i in range(1, 4)]
    contact = _src(_chunk("c", ["Contact"]))
    sources = [_src(steps[0]), _src(steps[1]), *_plain(9), contact]
    out = asyncio.run(with_section_siblings(_Store(steps), sources))
    assert contact in out
    assert len(out) >= len(sources)
