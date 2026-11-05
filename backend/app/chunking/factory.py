"""Selects and constructs a chunking strategy by name.

`auto` is the section-tree strategy (one chunk per self-contained section,
with a page/section breadcrumb), the default for every upload; callers may
still force another strategy for comparison/testing.
"""

from app.chunking.base import ChunkingStrategy
from app.chunking.fixed_size import FixedSizeChunker
from app.chunking.metadata_aware import MetadataAwareChunker
from app.chunking.section_tree import SectionTreeChunker
from app.chunking.semantic import SemanticChunker
from app.chunking.structure_aware import StructureAwareChunker
from app.retrieval.embeddings import embed_texts

STRUCTURE_TYPES = {"markdown", "md", "html", "code"}

# Semantic chunking embeds sentences to find topic-shift breakpoints — this
# is a *document-side* embedding (its output is compared sentence-to-sentence,
# not against a query), so RETRIEVAL_DOCUMENT is the right task type here.
_semantic_embed_fn = embed_texts


def get_strategy(
    name: str,
    source_type: str = "text",  # noqa: ARG001  # pylint: disable=unused-argument  # public kwarg, callers pass it
) -> ChunkingStrategy:
    if name == "auto":
        name = "section_tree"

    base: ChunkingStrategy
    if name == "fixed_size":
        base = FixedSizeChunker()
    elif name == "semantic":
        base = SemanticChunker(embed_fn=_semantic_embed_fn)
    elif name == "structure_aware":
        base = StructureAwareChunker()
    elif name == "section_tree":
        base = SectionTreeChunker()
    else:
        raise ValueError(f"Unknown chunking strategy: {name}")

    # Wrap every base strategy with metadata-aware enrichment by default.
    return MetadataAwareChunker(base_strategy=base)
