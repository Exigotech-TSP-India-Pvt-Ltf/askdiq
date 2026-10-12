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


# Keeps any single embedding input far below the model's 8192-token limit
# (e.g. CSV/JSON/log text that has no sentence punctuation).
MAX_SENTENCE_CHARS = 2000
MAX_CHUNK_CHARS = 4000


def _split_long(sentence: str) -> list[str]:
    if len(sentence) <= MAX_SENTENCE_CHARS:
        return [sentence]
    parts, current = [], ""
    for word in sentence.split():
        while len(word) > MAX_SENTENCE_CHARS:
            if current:
                parts.append(current)
                current = ""
            parts.append(word[:MAX_SENTENCE_CHARS])
            word = word[MAX_SENTENCE_CHARS:]
        if current and len(current) + 1 + len(word) > MAX_SENTENCE_CHARS:
            parts.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        parts.append(current)
    return parts


class SemanticChunker(ChunkingStrategy):
    name = "semantic"

    def __init__(self, embed_fn: Callable[[list[str]], list[list[float]]], similarity_threshold: float = 0.6):
        self.embed_fn = embed_fn
        self.similarity_threshold = similarity_threshold

    def chunk(self, text: str, source_metadata: dict | None = None) -> list[ChunkResult]:
        sentences = [
            piece
            for s in re.split(r"(?<=[.!?])\s+|\n+", text)
            if s.strip()
            for piece in _split_long(s.strip())
        ]
        if not sentences:
            return []

        embeddings = self.embed_fn(sentences)

        chunks: list[list[str]] = [[sentences[0]]]
        for i in range(1, len(sentences)):
            sim = _cosine_sim(embeddings[i - 1], embeddings[i])
            size = sum(len(s) for s in chunks[-1]) + len(sentences[i])
            if sim >= self.similarity_threshold and size <= MAX_CHUNK_CHARS:
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
