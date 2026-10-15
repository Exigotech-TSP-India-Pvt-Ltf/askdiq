"""Common interface all chunking strategies implement.

Every strategy takes raw text + source metadata and returns a list of
ChunkResult objects. Keeping this interface uniform is what lets the
factory swap strategies (or blend them) without touching the rest of
the pipeline.
"""

import asyncio
from abc import ABC
from dataclasses import dataclass, field


@dataclass
class ChunkResult:
    content: str
    chunk_index: int
    metadata: dict = field(default_factory=dict)


class ChunkingStrategy(ABC):  # noqa: B024 - concrete defaults provided; subclasses override chunk_sync or chunk
    name: str = "base"

    def chunk_sync(
        self, text: str, source_metadata: dict | None = None
    ) -> list[ChunkResult]:
        """CPU-only splitting; strategies without I/O implement this."""
        raise NotImplementedError

    async def chunk(
        self, text: str, source_metadata: dict | None = None
    ) -> list[ChunkResult]:
        """Split `text` into ChunkResult objects without blocking the event loop."""
        return await asyncio.to_thread(self.chunk_sync, text, source_metadata)
