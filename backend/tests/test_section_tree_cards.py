"""Card-per-chunk sections, hero/diagram split and sample framing (no DB)."""

from __future__ import annotations

from pathlib import Path

from app.chunking.section_tree import BREADCRUMB_SEPARATOR, SectionTreeChunker
from app.ingestion.cleanup import SAMPLE_SECTION_TEXT, clean_markdown
from app.ingestion.quality import dedupe_across_pages
from app.ingestion.site_pages import parse_site_page

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

CARDS = """# Page

## Our principles

Three things guide us.

### Transparency
Full transparency into what the system is doing and why, at every step.

### Encryption
End-to-end encryption across data, configurations, and outputs always.

### Approval
Nothing moves forward without your explicit approval, from start to end.
"""


def _chunks(text: str) -> list[str]:
    return [c.content for c in SectionTreeChunker().chunk_sync(text, {})]


def _home_chunks() -> list[str]:
    page = parse_site_page((DATA_DIR / "home.md").read_text(encoding="utf-8"))
    return _chunks(clean_markdown(clean_markdown(page.body)))


def test_card_set_is_one_chunk_per_card_with_breadcrumb() -> None:
    chunks = _chunks(CARDS)
    crumb = BREADCRUMB_SEPARATOR.join(("Page", "Our principles"))
    for title in ("Transparency", "Encryption", "Approval"):
        card = [
            c for c in chunks if c.startswith(f"{crumb}{BREADCRUMB_SEPARATOR}{title}\n")
        ]
        assert len(card) == 1
    intro = next(c for c in chunks if c.startswith(f"{crumb}\n"))
    assert "Three things guide us." in intro
    assert "- Transparency" in intro  # the intro names the cards
    assert "End-to-end encryption" not in intro


def test_one_phrase_cards_stay_together() -> None:
    text = "# Page\n\n## Future\n\nIntro.\n\n### A\nAlways-on\n\n### B\nHuman-in-loop\n\n### C\nEverywhere\n"
    chunks = _chunks(text)
    assert len(chunks) == 1
    assert "Human-in-loop" in chunks[0]


def test_page_level_sections_are_not_a_card_set() -> None:
    text = "# Page\n\n## One\nShort copy one here.\n\n## Two\nShort copy two here.\n\n## Three\nShort copy three.\n"
    assert len(_chunks(text)) == 1


def test_illustrative_note_is_kept_as_framing_and_idempotent() -> None:
    text = (
        "# Page\n\n## Score\n\n### Overall score\n\n_(Illustrative sample score card)_\n\n"
        "67/100\n\n- **23** Critical Vulnerabilities\n"
    )
    once = clean_markdown(text)
    twice = clean_markdown(once)
    assert once == twice
    assert f"Illustrative sample score card. {SAMPLE_SECTION_TEXT}" in once
    assert "67/100" not in once
    assert "23" not in once


def test_sample_framing_survives_cross_page_dedupe() -> None:
    framed = clean_markdown("# A\n\n## S\n\nSample Results\n\n90%\n")
    pages = {"a.md": framed, "b.md": framed.replace("# A", "# B")}
    deduped, _ = dedupe_across_pages(pages, {"a.md": "/", "b.md": "/b"})
    assert all(SAMPLE_SECTION_TEXT in text for text in deduped.values())


def test_mobile_list_copy_is_dropped_and_diagram_is_own_section() -> None:
    text = (
        "# Page\n\n## [Section: Hero]\n\nLead copy for the page hero.\n\n"
        "### [Hero diagram: Orchestration cycle]\n\n"
        "- **01 Onboard** — Connect your tenant\n- **02 Assess** — Agents analyze\n"
        "- **03 Apply** — Changes applied\n\n"
        "_(Mobile only: collapsible panel)_ **Explore how it works**\n\n"
        "1. Onboard: Connect your tenant\n2. Assess: Agents analyze\n3. Apply: Changes applied\n\n"
        "## [Section: Framework strip]\n\nBuilt around the standards\n\n## Next\n\nMore copy.\n"
    )
    cleaned = clean_markdown(text)
    assert "Explore how it works" not in cleaned
    assert cleaned.count("Connect your tenant") == 1
    assert "### Orchestration cycle" in cleaned
    # Framework-strip copy belongs to the page, not to the diagram section.
    assert cleaned.index("Built around the standards") < cleaned.index(
        "### Orchestration"
    )


def test_home_carousel_hero_and_scorecard_chunks() -> None:
    chunks = _home_chunks()
    for phrase in (
        "Full transparency into what the system is doing and why",
        "End-to-end encryption across data, configurations, and outputs",
    ):
        hits = [c for c in chunks if phrase in c]
        assert len(hits) == 1
        assert len(hits[0]) < 500  # one card, not the whole carousel
    hero = chunks[0]
    assert "Powered by AI, Perfected by Humans" in hero
    assert "Connect your Microsoft 365 tenant" not in hero
    assert sum("Connect your Microsoft 365 tenant" in c for c in chunks) == 1
    scorecard = next(
        c for c in chunks if "Overall Security Posture Assessment Score" in c
    )
    assert "Illustrative sample score card" in scorecard
    assert SAMPLE_SECTION_TEXT in scorecard
    assert all(len(c.split("\n\n", 1)[-1]) <= 1200 for c in chunks)


def test_a_term_label_stays_with_its_definition() -> None:
    """The Terms page's oversized Definitions list is packed by paragraph:
    a cut never separates "“Agent”" from the sentence that defines it."""
    page = parse_site_page((DATA_DIR / "terms.md").read_text(encoding="utf-8"))
    chunks = _chunks(clean_markdown(page.body))
    definition = "A governed, domain-specific AI capability"
    holder = [c for c in chunks if definition in c]
    assert len(holder) == 1
    assert "“Agent”\n\n" + definition in holder[0]
    assert not any(c.rstrip().endswith("“Agent”") for c in chunks)
