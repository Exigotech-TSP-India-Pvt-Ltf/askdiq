"""Subject attribution in app.harness.claims: a claim about a named subject
quotes that subject's own section, in the page's own strength of wording.

Built from the real KB pages (data/*.md) chunked the way ingestion chunks
them. The LLM reply is a fixed JSON draft: these tests pin the verifier's
contract only; behaviour evidence comes from live /query runs.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from app.chunking.section_tree import SectionTreeChunker
from app.harness.claims import GroundedAnswer, ground_answer, parse_draft
from app.ingestion.cleanup import clean_markdown
from app.ingestion.site_pages import parse_site_page
from app.schemas.query import SourceChunk

_DATA = Path(__file__).resolve().parents[1] / "data"


@cache
def _chunks(name: str) -> tuple[SourceChunk, ...]:
    page = parse_site_page((_DATA / name).read_text(encoding="utf-8"))
    chunks = SectionTreeChunker().chunk_sync(
        clean_markdown(page.body), {"source_name": name, "page_title": page.title}
    )
    return tuple(
        SourceChunk(
            chunk_id=f"{name}-{n}",
            document_id=f"doc-{name}",
            content=chunk.content,
            score=0.8,
            metadata=chunk.metadata,
        )
        for n, chunk in enumerate(chunks)
    )


def _section(name: str, *path: str) -> SourceChunk:
    """The chunk of ``data/<name>`` whose section path is ``path``."""
    return next(
        c for c in _chunks(name) if c.metadata.get("section_path") == list(path)
    )


def _card(leaf: str) -> SourceChunk:
    return _section("packages.md", "Package cards", leaf)


def _home(parent: str, leaf: str) -> SourceChunk:
    return _section("home.md", parent, leaf)


_FIT = "Not sure which service fits?"
_STAGES = "Build the right capability at every stage."


def _ground(
    claims: list[tuple[str, str, int, str]], sources: list[SourceChunk], query: str
) -> GroundedAnswer:
    reply = {
        "parts": [{"ask": query, "covered": True}],
        "claims": [
            {"text": t, "quote": q, "source": s, "part": 0, "kind": k}
            for t, q, s, k in claims
        ],
    }
    draft = parse_draft(json.dumps(reply))
    assert draft is not None
    return ground_answer(draft, sources, query)


def _reasons(result: GroundedAnswer) -> dict[str, str]:
    return {c.text: r for c, r in result.dropped}


_LEAD_S = (
    "Everything in Foundation, plus:",
    "Everything in Foundation, plus:",
    1,
    "sentence",
)
_RISK = (
    "Risk-based access: sign-in decisions that adapt to user and session risk.",
    "Risk-based access: sign-in decisions that adapt to user and session risk",
    1,
    "item",
)
_MDR = (
    "24/7 Managed Detection & Response: round-the-clock threat monitoring, hunting "
    "and response by a dedicated security operations team.",
    "24/7 Managed Detection & Response: round-the-clock threat monitoring, hunting "
    "and response by a dedicated security operations team",
    2,
    "item",
)


def test_sibling_card_item_is_not_the_asked_package() -> None:
    # tx-buying-03: the Frontier card's MDR bullet under the Strengthen lead.
    result = _ground(
        [_LEAD_S, _RISK, _MDR],
        [_card("Strengthen"), _card("Frontier")],
        "What's the price of the Strengthen package?",
    )
    assert "Risk-based access" in result.text
    assert "Managed Detection" not in result.text
    assert _reasons(result) == {_MDR[0]: "other_section_subject"}


def test_claim_naming_the_asked_package_from_a_sibling_card_is_dropped() -> None:
    text = "The Strengthen package includes AI-ready data governance."
    quote = "AI-ready data governance: make sure Copilot and AI agents only reach data"
    result = _ground(
        [_RISK, (text, quote, 2, "sentence")],
        [_card("Strengthen"), _card("Frontier")],
        "What does Strengthen include?",
    )
    assert _reasons(result) == {text: "other_section_subject"}


def test_parent_card_list_marks_siblings_without_the_asked_card() -> None:
    # Only the Frontier card and the "Package cards" list were retrieved.
    mdr = (*_MDR[:2], 1, "item")
    result = _ground(
        [mdr],
        [_card("Frontier"), _section("packages.md", "Package cards")],
        "What does the Strengthen package include?",
    )
    assert not result.answered
    assert _reasons(result) == {mdr[0]: "other_section_subject"}


def test_everything_in_lists_pass_on_their_own_card() -> None:
    lead = (
        "Everything in Strengthen, plus:",
        "Everything in Strengthen, plus:",
        2,
        "sentence",
    )
    mdr = (*_MDR[:2], 2, "item")
    oversight = (
        "AI and agent oversight: visibility and control over how AI is used.",
        "AI and agent oversight: visibility and control over how AI is used",
        2,
        "item",
    )
    result = _ground(
        [lead, mdr, oversight],
        [_card("Strengthen"), _card("Frontier")],
        "What does the Frontier package include?",
    )
    assert result.dropped == []
    assert "Everything in Strengthen, plus" in result.text
    assert "Managed Detection" in result.text


def test_strengthen_lead_on_its_own_card_passes() -> None:
    result = _ground(
        [_LEAD_S, _RISK],
        [_card("Strengthen"), _card("Foundation"), _card("Frontier")],
        "What does the Strengthen package add on top of Foundation?",
    )
    assert result.dropped == []


def test_same_named_item_of_another_kind_is_dropped() -> None:
    # home.md's "Strengthen" service-fit card is not the Strengthen package.
    cis = (
        "The aligned framework is CIS Benchmark v7.0.0.",
        "Aligned framework: CIS Benchmark v7.0.0",
        2,
        "sentence",
    )
    checks = ("Configuration checks", "Configuration checks", 2, "item")
    result = _ground(
        [_RISK, cis, checks],
        [_card("Strengthen"), _home(_FIT, "Strengthen")],
        "What's the price of the Strengthen package?",
    )
    assert _reasons(result) == {
        cis[0]: "other_kind_subject",
        checks[0]: "other_kind_subject",
    }
    named = (
        "The Strengthen package is aligned to the CIS Benchmark v7.0.0.",
        "Aligned framework: CIS Benchmark v7.0.0",
        2,
        "sentence",
    )
    result = _ground(
        [_RISK, named],
        [_card("Strengthen"), _home(_FIT, "Strengthen")],
        "What's the price?",
    )
    assert _reasons(result) == {named[0]: "other_kind_subject"}


def test_same_page_stage_cards_back_the_stage_question() -> None:
    # home-strengthen-stage: the service-fit card answers "the Strengthen stage".
    cis = (
        "The Strengthen stage is aligned to CIS Benchmark v7.0.0.",
        "Aligned framework: CIS Benchmark v7.0.0",
        2,
        "sentence",
    )
    result = _ground(
        [cis],
        [_home(_STAGES, "Strengthen"), _home(_FIT, "Strengthen"), _card("Strengthen")],
        "Which CIS Benchmark version is the Strengthen stage aligned to?",
    )
    assert result.dropped == []
    assert "v7.0.0" in result.text


def test_cross_page_fact_about_the_same_subject_passes() -> None:
    # The compare table (another section) states Strengthen's own row.
    row = (
        "Application allowlisting is included in Strengthen and Frontier.",
        "Application allowlisting: included in Strengthen, Frontier",
        2,
        "sentence",
    )
    result = _ground(
        [_RISK, row],
        [
            _card("Strengthen"),
            _section("packages.md", "Compare Packages"),
            _card("Frontier"),
        ],
        "Does the Strengthen package include application allowlisting?",
    )
    assert result.dropped == []


def test_kindless_question_keeps_the_other_page_card() -> None:
    # No kind word: nothing says which "Strengthen" is meant.
    checks = ("Configuration checks", "Configuration checks", 2, "item")
    result = _ground(
        [_RISK, checks],
        [_card("Strengthen"), _home(_FIT, "Strengthen")],
        "What do I get with Strengthen?",
    )
    assert result.dropped == []


_E8 = "essential-eight.md"
_TAGLINE = "Comprehensive protection across all critical security domains"


def _e8_strategies() -> SourceChunk:
    return next(c for c in _chunks(_E8) if _TAGLINE in c.content)


def test_degree_word_stronger_than_the_page_is_dropped() -> None:
    # mt-zt-e8-levels / fin-kb-uni-08: "Comprehensive" restated as "full".
    full = "The Essential Eight gives full protection across all critical security domains."
    result = _ground([(full, _TAGLINE, 1, "sentence")], [_e8_strategies()], "E8")
    assert _reasons(result) == {full: "stretched_wording"}


def test_page_strength_wording_passes() -> None:
    full = (
        "Foundation helps you get full value from the Microsoft 365 security you already pay for.",
        "Get full value from the Microsoft 365 security you already pay for",
        1,
        "sentence",
    )
    result = _ground([full], [_card("Foundation")], "What is Foundation?")
    assert result.dropped == []
    complete = (
        "Frontier gives complete protection for organizations adopting Copilot and AI agents.",
        "Complete protection for organizations adopting Copilot and AI agents",
        1,
        "sentence",
    )
    result = _ground([complete], [_card("Frontier")], "What is Frontier?")
    assert result.dropped == []
