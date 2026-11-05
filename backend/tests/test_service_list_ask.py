"""'What services do you have?' is answered with the services list, never a
'which service?' card, and never blended with E8 phases or T&C wording."""

from __future__ import annotations

import pytest

from app.harness import claims
from app.harness.clarify import is_company_or_generic
from app.retrieval.retriever import (
    SERVICE_LIST_SECTION,
    expand_retrieval_query,
    is_service_list_ask,
    service_list_only,
)
from app.schemas.query import SourceChunk

CATALOGUE = [
    "What are the services you have?",
    "What services do you offer?",
    "what services do u have",
    "Which services do you provide?",
    "tell me all your services",
    "list your services",
    "What solutions do you offer?",
]
NOT_CATALOGUE = [
    "What does the Essential Eight service include?",
    "What services does Deploy IQ offer for Essential Eight?",
    "Which service suits a small business?",
    "How much does it cost?",
]


@pytest.mark.parametrize("query", CATALOGUE)
def test_catalogue_ask_is_detected_and_not_clarified(query: str) -> None:
    assert is_service_list_ask(query)
    assert is_company_or_generic(query)
    assert expand_retrieval_query(query).endswith("Our Services")


@pytest.mark.parametrize("query", NOT_CATALOGUE)
def test_named_or_other_asks_are_not_catalogue(query: str) -> None:
    assert not is_service_list_ask(query)


def _src(section: str | None) -> SourceChunk:
    return SourceChunk(
        chunk_id=section or "none",
        document_id="d",
        content=section or "",
        score=0.5,
        metadata={"section_path": [section]} if section else {},
    )


def test_catalogue_ask_keeps_only_the_services_list() -> None:
    listed = _src(SERVICE_LIST_SECTION)
    sources = [_src("Essential Eight Security and Compliance"), listed, _src(None)]
    assert service_list_only("What services do you offer?", sources) == [listed]


def test_without_the_list_chunk_sources_are_unchanged() -> None:
    sources = [_src("Essential Eight Security and Compliance")]
    assert service_list_only("What services do you offer?", sources) == sources
    other = [_src(SERVICE_LIST_SECTION), _src("Pricing")]
    assert service_list_only("How much is Zero Trust?", other) == other


# --- rendering: every listed service ships ---------------------------------

_ZT = (
    "Deploy IQ delivers Zero Trust as a structured security service, aligned to "
    "organisational context, risk profile, and operational reality."
)
_E8 = (
    "Deploy IQ delivers the ACSC Essential Eight through secure, phased "
    "implementation across all maturity levels, producing audit-ready outcomes "
    "across policy and technical controls."
)
_REST = [
    "Microsoft 365 Security Baseline Assessment",
    "CIS Microsoft 365 Foundations Benchmark Assessment",
    "ISO/IEC 27001 Technical Controls in Microsoft 365",
    "ISO 42001 Technical Controls for AI in Microsoft 365",
]
_CHUNK = (
    "Security Services Delivered with Control and Intent \u203a Our Services\n\n"
    f"Zero Trust Services\n{_ZT} Delivery is phased, evaluated, and governed at "
    f"every stage.\n\nEssential Eight (E8) Security\n{_E8}\n\n"
    + "\n\n".join(f"- {n}" for n in _REST)
)


def _ground(head: list[claims.Claim]) -> claims.GroundedAnswer:
    source = SourceChunk(
        chunk_id="c",
        document_id="d",
        content=_CHUNK,
        score=0.5,
        metadata={
            "section_path": [SERVICE_LIST_SECTION],
            "section_heading": "Our Services",
        },
    )
    items = [claims.Claim(n, 1, n, 0, "item") for n in _REST]
    draft = claims.Draft("answer", [claims.Part("our services", True)], [*head, *items])
    return claims.ground_answer(draft, [source], "What services do you offer?")


def test_bare_names_before_the_list_are_list_entries() -> None:
    answer = _ground(
        [
            claims.Claim("Zero Trust Services.", 1, "Zero Trust Services"),
            claims.Claim(
                "Essential Eight (E8) Security.", 1, "Essential Eight (E8) Security"
            ),
        ]
    )
    assert "- Zero Trust Services" in answer.text
    assert "- Essential Eight (E8) Security" in answer.text


def test_label_and_description_quoting_only_the_heading_verifies() -> None:
    answer = _ground(
        [
            claims.Claim(f"Zero Trust Services: {_ZT}", 1, "Zero Trust Services"),
            claims.Claim(
                f"Essential Eight (E8) Security: {_E8}",
                1,
                "Essential Eight (E8) Security",
            ),
        ]
    )
    assert answer.dropped == []
    assert "Zero Trust Services:" in answer.text


def test_a_description_the_chunk_lacks_is_still_dropped() -> None:
    answer = _ground(
        [
            claims.Claim(
                "Zero Trust Services: Deploy IQ guarantees zero breaches.",
                1,
                "Zero Trust Services",
            )
        ]
    )
    assert "guarantees" not in answer.text
    assert answer.dropped


def test_list_names_reads_every_service_of_the_section() -> None:
    source = SourceChunk(
        chunk_id="c", document_id="d", content=_CHUNK, score=0.5, metadata={}
    )
    names = claims.list_names(source)
    assert names == ["Zero Trust Services", "Essential Eight (E8) Security", *_REST]
    assert claims.missing_list_names("- Zero Trust Services", names) == names[1:]
    answer = claims.ground_answer(
        claims.list_draft(names, 1, "What services do you offer?"),
        [source],
        "What services do you offer?",
    )
    assert claims.missing_list_names(answer.text, names) == []
