"""Semantic chunking: split on sentence boundaries, merge sentences until
embedding similarity between consecutive sentences drops below a threshold
(i.e. a topic shift), rather than at a fixed token count.

NOTE: the embedding call is stubbed via `embed_fn` injection so this module
has no hard dependency on a specific embedding provider — wire in the real
embedding client from app.retrieval when filling this in.
"""
import re
from collections.abc import Callable

from app.chunking.base import ChunkingStrategy, ChunkResult


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


class SemanticChunker(ChunkingStrategy):
    name = "semantic"

    def __init__(self, embed_fn: Callable[[list[str]], list[list[float]]], similarity_threshold: float = 0.6):
        self.embed_fn = embed_fn
        self.similarity_threshold = similarity_threshold

    def chunk(self, text: str, source_metadata: dict | None = None) -> list[ChunkResult]:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        if not sentences:
            return []

        embeddings = self.embed_fn(sentences)

        chunks: list[list[str]] = [[sentences[0]]]
        for i in range(1, len(sentences)):
            sim = _cosine_sim(embeddings[i - 1], embeddings[i])
            if sim >= self.similarity_threshold:
                chunks[-1].append(sentences[i])
            else:
                chunks.append([sentences[i]])

        return [
            ChunkResult(
                content=" ".join(group),
                chunk_index=idx,
                metadata={**(source_metadata or {}), "strategy": self.name, "sentence_count": len(group)},
            )
            for idx, group in enumerate(chunks)
        ]
