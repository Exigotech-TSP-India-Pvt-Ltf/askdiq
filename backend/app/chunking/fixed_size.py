"""Baseline fixed-size chunking with configurable token overlap.

This is the "naive" strategy the requirements explicitly say NOT to submit
alone — kept here as the baseline/fallback and for A/B comparison against
the smarter strategies.
"""
from app.chunking.base import ChunkingStrategy, ChunkResult


class FixedSizeChunker(ChunkingStrategy):
    name = "fixed_size"

    def __init__(self, chunk_size: int = 512, overlap: int = 77):
        # overlap ~15% of chunk_size by default
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, text: str, source_metadata: dict | None = None) -> list[ChunkResult]:
        words = text.split()
        step = max(self.chunk_size - self.overlap, 1)
        results = []
        for i, start in enumerate(range(0, len(words), step)):
            piece = words[start : start + self.chunk_size]
            if not piece:
                break
            results.append(
                ChunkResult(
                    content=" ".join(piece),
                    chunk_index=i,
                    metadata={**(source_metadata or {}), "strategy": self.name},
                )
            )
            if start + self.chunk_size >= len(words):
                break
        return results
