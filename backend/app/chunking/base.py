"""Common interface all chunking strategies implement.

Every strategy takes raw text + source metadata and returns a list of
ChunkResult objects. Keeping this interface uniform is what lets the
factory swap strategies (or blend them) without touching the rest of
the pipeline.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ChunkResult:
    content: str
    chunk_index: int
    metadata: dict = field(default_factory=dict)


class ChunkingStrategy(ABC):
    name: str = "base"

    @abstractmethod
    def chunk(self, text: str, source_metadata: dict | None = None) -> list[ChunkResult]:
        """Split `text` into ChunkResult objects."""
        raise NotImplementedError
