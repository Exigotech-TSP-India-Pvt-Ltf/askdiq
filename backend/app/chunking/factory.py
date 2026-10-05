"""Selects and constructs a chunking strategy by name.

`auto` picks a strategy based on source_type — e.g. markdown/code docs go
structure-aware, prose goes semantic — giving a sensible default while
still letting callers force a specific strategy for comparison/testing.
"""


from app.chunking.base import ChunkingStrategy
from app.chunking.fixed_size import FixedSizeChunker
from app.chunking.metadata_aware import MetadataAwareChunker
from app.chunking.semantic import SemanticChunker
from app.chunking.structure_aware import StructureAwareChunker
from app.retrieval.embeddings import embed_texts_sync

STRUCTURE_TYPES = {"markdown", "md", "html", "code"}

# Semantic chunking embeds sentences to find topic-shift breakpoints — this
# is a *document-side* embedding (its output is compared sentence-to-sentence,
# not against a query), so RETRIEVAL_DOCUMENT is the right task type here.
_semantic_embed_fn = embed_texts_sync


def get_strategy(name: str, source_type: str = "text") -> ChunkingStrategy:
    if name == "auto":
        name = "structure_aware" if source_type in STRUCTURE_TYPES else "semantic"

    base: ChunkingStrategy
    if name == "fixed_size":
        base = FixedSizeChunker()
    elif name == "semantic":
        base = SemanticChunker(embed_fn=_semantic_embed_fn)
    elif name == "structure_aware":
        base = StructureAwareChunker()
    else:
        raise ValueError(f"Unknown chunking strategy: {name}")

    # Wrap every base strategy with metadata-aware enrichment by default.
    return MetadataAwareChunker(base_strategy=base)
