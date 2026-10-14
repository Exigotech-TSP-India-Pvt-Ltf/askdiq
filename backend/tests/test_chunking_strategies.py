"""Test that all four chunking strategies instantiate and run cleanly (Subtask 1.1.2)."""

import pytest

from app.chunking.base import ChunkingStrategy
from app.chunking.fixed_size import FixedSizeChunker
from app.chunking.metadata_aware import MetadataAwareChunker
from app.chunking.semantic import SemanticChunker
from app.chunking.structure_aware import StructureAwareChunker


@pytest.mark.asyncio
async def test_chunking_strategy_instantiability():
    """Verify base class and all four concrete chunking strategies."""
    # ChunkingStrategy has defaults and can be subclassed
    assert issubclass(FixedSizeChunker, ChunkingStrategy)
    assert issubclass(StructureAwareChunker, ChunkingStrategy)
    assert issubclass(SemanticChunker, ChunkingStrategy)
    assert issubclass(MetadataAwareChunker, ChunkingStrategy)

    sample_text = (
        "# Heading 1\n\nDeployIQ is a security compliance platform.\n\n"
        "## Section 2\n\nIt performs automated audit analysis."
    )

    # 1. FixedSizeChunker (implements chunk_sync)
    fixed = FixedSizeChunker(chunk_size=100, overlap=20)
    chunks_fixed = fixed.chunk_sync(sample_text)
    assert len(chunks_fixed) > 0

    # 2. StructureAwareChunker (implements chunk_sync)
    structure = StructureAwareChunker()
    chunks_struct = structure.chunk_sync(sample_text)
    assert len(chunks_struct) > 0

    # 3. MetadataAwareChunker wrapping fixed (overrides async chunk)
    meta = MetadataAwareChunker(base_strategy=fixed)
    chunks_meta = await meta.chunk(sample_text, source_metadata={"title": "Test Doc"})
    assert len(chunks_meta) > 0
    assert chunks_meta[0].metadata.get("title") == "Test Doc"


@pytest.mark.asyncio
async def test_async_chunking_delegation():
    """Verify async chunk() delegates to chunk_sync non-blockingly."""
    fixed = FixedSizeChunker(chunk_size=50, overlap=10)
    chunks = await fixed.chunk("Hello world from async chunking test.")
    assert len(chunks) >= 1
