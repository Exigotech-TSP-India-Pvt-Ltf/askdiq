"""Metadata-aware chunking wrapper.

Wraps another base strategy and tags every chunk with which strategy
produced it, so chunking approaches can be A/B compared later.

STEP 6 note: this used to also build `parent_context`/`sibling_range` for
expanding a selected "child" chunk with neighboring context at answer time,
but nothing downstream ever read it, and it was unused dead code. Since the
STEP 1 structure-aware fix now yields coherent, full-section chunks (a
heading plus its entire body), the LLM already gets full context without a
separate parent-expansion step — so that dead metadata was removed instead
of wiring it up.
"""

from app.chunking.base import ChunkingStrategy, ChunkResult


class MetadataAwareChunker(ChunkingStrategy):
    name = "metadata_aware"

    def __init__(self, base_strategy: ChunkingStrategy):
        self.base_strategy = base_strategy

    def chunk(
        self, text: str, source_metadata: dict | None = None
    ) -> list[ChunkResult]:
        children = self.base_strategy.chunk(text, source_metadata)

        for child in children:
            child.metadata.update(
                {
                    "strategy": self.name,
                    "base_strategy": self.base_strategy.name,
                }
            )
        return children
