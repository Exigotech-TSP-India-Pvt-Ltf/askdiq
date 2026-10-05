from app.chunking.structure_aware import StructureAwareChunker


def _headings(chunks):
    return [c.metadata["section_heading"] for c in chunks]


def test_lone_h1_title_folds_forward_into_first_real_section():
    text = (
        "# Deploy IQ Platform\n\n"
        "## Platform Overview\n\n"
        "Deploy IQ provides governed security.\n\n"
        "## Get Started\n\n"
        "Bring structure to your program.\n"
    )
    chunker = StructureAwareChunker()
    chunks = chunker.chunk(text)

    # The lone (non-recurring) H1 must not swallow the rest of the doc into
    # one giant chunk — H2 is the recurring level, so each H2 is its own chunk.
    assert len(chunks) == 2
    assert _headings(chunks) == ["Platform Overview", "Get Started"]
    # The H1 title text isn't lost — it's folded into the first section's body.
    assert "Deploy IQ Platform" in chunks[0].content


def test_nested_h3_folds_into_parent_h2_not_separate_chunks():
    text = (
        "## How Our Platform Works\n\n"
        "Deploy IQ operates as a cycle:\n\n"
        "### Understand\n\n"
        "Captures intent and context.\n\n"
        "### Recommend\n\n"
        "Suggests risk-aligned actions.\n\n"
        "## Who It Is For\n\n"
        "Any organisation.\n"
    )
    chunker = StructureAwareChunker()
    chunks = chunker.chunk(text)

    assert _headings(chunks) == ["How Our Platform Works", "Who It Is For"]
    assert "Understand" in chunks[0].content
    assert "Recommend" in chunks[0].content


def test_bold_wrapped_heading_text_is_stripped():
    text = "## **Platform Overview**\n\nSome body text.\n\n## **Get Started**\n\nMore text.\n"
    chunker = StructureAwareChunker()
    chunks = chunker.chunk(text)

    assert _headings(chunks) == ["Platform Overview", "Get Started"]


def test_standalone_bold_lines_never_create_a_section_boundary():
    text = (
        "## Platform Overview\n\n"
        "Deploy IQ provides governed security.\n\n"
        "**1. Unified Design**\n\n"
        "A coherent design model.\n\n"
        "**2. Continuous System**\n\n"
        "Keeps posture aligned.\n\n"
        "## Get Started\n\n"
        "Start now.\n"
    )
    chunker = StructureAwareChunker()
    chunks = chunker.chunk(text)

    # Only the two real ATX headings create chunks — the bold numbered
    # items must stay folded inside "Platform Overview".
    assert _headings(chunks) == ["Platform Overview", "Get Started"]
    assert "Unified Design" in chunks[0].content
    assert "Continuous System" in chunks[0].content


def test_no_headings_returns_single_untitled_chunk():
    text = "Just plain prose with no markdown headings at all."
    chunker = StructureAwareChunker()
    chunks = chunker.chunk(text)

    assert len(chunks) == 1
    assert chunks[0].metadata["section_heading"] == ""
    assert chunks[0].content == text


def test_single_non_recurring_heading_still_opens_its_own_section():
    # Mirrors trust-center.md: exactly one heading in the whole document.
    text = "## Trust Center\n\nTrust is built on transparency and governance.\n"
    chunker = StructureAwareChunker()
    chunks = chunker.chunk(text)

    assert len(chunks) == 1
    assert chunks[0].metadata["section_heading"] == "Trust Center"


def test_oversized_section_splits_at_paragraph_boundaries_same_heading():
    # Each paragraph is well under max_chunk_chars on its own, so the split
    # must be grouping/breaking at paragraph boundaries, not mid-paragraph.
    paragraphs = [f"Paragraph {i} about Deploy IQ security posture." for i in range(6)]
    text = "## Platform Overview\n\n" + "\n\n".join(paragraphs)

    chunker = StructureAwareChunker(max_chunk_chars=120)
    chunks = chunker.chunk(text)

    assert len(chunks) > 1
    assert all(c.metadata["section_heading"] == "Platform Overview" for c in chunks)
    for chunk in chunks:
        body = chunk.content.removeprefix("Section: Platform Overview\n\n")
        assert len(body) <= 120


def test_chunk_indices_are_sequential():
    text = "## A\n\nbody a\n\n## B\n\nbody b\n\n## C\n\nbody c\n"
    chunker = StructureAwareChunker()
    chunks = chunker.chunk(text)

    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_source_metadata_is_preserved_on_every_chunk():
    text = "## A\n\nbody a\n"
    chunker = StructureAwareChunker()
    chunks = chunker.chunk(text, source_metadata={"source_name": "about-us.md"})

    assert chunks[0].metadata["source_name"] == "about-us.md"
    assert chunks[0].metadata["strategy"] == "structure_aware"
