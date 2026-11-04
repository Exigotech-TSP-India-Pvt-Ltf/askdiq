"""Page-link requests: detection, metadata-only page resolution, rendering."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.harness import page_links
from app.harness.page_links import (
    SitePage,
    attach_page_link,
    is_page_link_request,
    resolve_page,
)
from app.harness.prompt_catalog import NO_ANSWER_LINE, PAGE_LINK_LINE
from app.schemas.query import Citation, QueryResponse, SourceChunk

BASE = "https://staging.deployiq.co"

# The 19 site pages (route, front-matter title) as ingested from data/*.md.
PAGES = [
    SitePage(f"d{i}", route, title)
    for i, (route, title) in enumerate(
        [
            ("/", "DeployIQ | AI-Powered Cybersecurity & Compliance"),
            ("/platform", "Deploy IQ Platform"),
            ("/services", "Services - Deploy IQ"),
            ("/zero-trust", "Zero Trust Services - Deploy IQ"),
            ("/essential-eight", "Essential Eight Compliance - Deploy IQ"),
            (
                "/microsoft-365-security-baseline-assessment",
                "Microsoft 365 Security Baseline Assessment - Deploy IQ",
            ),
            (
                "/iso-27001-microsoft-365-technical-controls",
                "ISO 27001 Technical Controls in Microsoft 365 - Deploy IQ",
            ),
            (
                "/cis-microsoft-365-foundations-benchmark-assessment",
                "CIS Microsoft 365 Foundations Benchmark Assessment - Deploy IQ",
            ),
            (
                "/iso-42001-ai-management-system",
                "ISO 42001 Technical Controls for AI in Microsoft 365 - Deploy IQ",
            ),
            ("/packages", "Microsoft 365 Security Packages - Deploy IQ"),
            ("/responsible-ai", "Responsible AI & Governance Framework - Deploy IQ"),
            ("/secure-ai", "Secure AI - Deploy IQ"),
            ("/about-us", "About Us - Deploy IQ"),
            ("/our-vision", ""),
            ("/contact-us", "Contact Us - Deploy IQ"),
            ("/get-started", "Get Started | DeployIQ"),
            ("/booking", "Book a Consultation - Deploy IQ"),
            ("/privacy", "Privacy Policy - Deploy IQ"),
            ("/terms", "Terms & Conditions - Deploy IQ"),
        ]
    )
]


@pytest.mark.parametrize(
    ("query", "route"),
    [
        ("give me the link for the Zero Trust page", "/zero-trust"),
        ("link to the home page", "/"),
        ("url for your homepage", "/"),
        ("send me the page for the platform", "/platform"),
        ("where can I read about your services?", "/services"),
        ("link to essential 8", "/essential-eight"),
        ("url for the E8 page", "/essential-eight"),
        ("link to the Microsoft 365 security baseline assessment", "/microsoft-365-security-baseline-assessment"),
        ("where can I read about ISO 27001 technical controls", "/iso-27001-microsoft-365-technical-controls"),
        ("give me the link for the CIS benchmark page", "/cis-microsoft-365-foundations-benchmark-assessment"),
        ("url for ISO/IEC 42001", "/iso-42001-ai-management-system"),
        ("link to the packages page", "/packages"),
        ("give me the link for the responsible AI page", "/responsible-ai"),
        ("send me the page for secure AI", "/secure-ai"),
        ("link to the about us page", "/about-us"),
        ("where can I find your vision page", "/our-vision"),
        ("give me the link for the contact page", "/contact-us"),
        ("link to the get started page", "/get-started"),
        ("link to the booking page", "/booking"),
        ("url to book a consultation", "/booking"),
        ("where can I read your privacy policy", "/privacy"),
        ("link to the terms and conditions", "/terms"),
        ("give me the link for the T&Cs", "/terms"),
    ],
)  # fmt: skip
def test_resolves_every_page(query: str, route: str) -> None:
    assert is_page_link_request(query)
    page = resolve_page(query, PAGES)
    assert page is not None
    assert page.route == route


@pytest.mark.parametrize(
    "query",
    [
        "link to your pricing page",
        "give me the link for the careers page",
        "url for your blog",
        "link to the AI page",  # secure-ai vs responsible-ai: ambiguous
    ],
)
def test_unknown_or_ambiguous_page_is_none(query: str) -> None:
    assert resolve_page(query, PAGES) is None


def test_tie_prefers_cited_document() -> None:
    page = resolve_page("link to the AI page", PAGES, preferred_ids=["d11"])
    assert page is not None
    assert page.route == "/secure-ai"


def test_no_topic_uses_first_cited_routed_page() -> None:
    page = resolve_page("send me the link", PAGES, preferred_ids=["x", "d3"])
    assert page is not None
    assert page.route == "/zero-trust"


@pytest.mark.parametrize(
    "query",
    ["What is Zero Trust?", "How does the Essential Eight work?", "hi"],
)
def test_not_a_link_request(query: str) -> None:
    assert not is_page_link_request(query)


def test_detection_is_linear_on_hostile_input() -> None:
    hostile = "where " + "can " * 5000 + "link " * 5000
    started = time.perf_counter()
    is_page_link_request(hostile)
    page_links.topic_tokens(hostile)
    assert time.perf_counter() - started < 0.5  # noqa: PLR2004


# --- rendering -------------------------------------------------------------------


def _chunk(doc_id: str, route: str) -> SourceChunk:
    return SourceChunk(
        chunk_id=f"c-{doc_id}",
        document_id=doc_id,
        content="Zero Trust: verify every request.",
        score=0.0,
        metadata={"source_name": f"{route.strip('/') or 'home'}.md"},
        url=f"{BASE}{route}#section",
        title="Zero Trust Services - Deploy IQ",
    )


def _response(outcome: str, **kw: object) -> QueryResponse:
    fields: dict = {
        "answer": "Zero Trust verifies every request. [1]",
        "sources": [],
        "citations": [],
        "grounded": outcome == "answered",
        "confidence": 0.9,
        "request_id": "r1",
        "latency_ms": 1.0,
        "guardrail_flags": [],
        "outcome": outcome,
        "token_details": {},
    }
    fields.update(kw)
    return QueryResponse(**fields)


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> object:
    async def pages(_store: object) -> list[SitePage]:
        return PAGES

    async def chunks(_store: object, route: str) -> list[SourceChunk]:
        page = next(p for p in PAGES if p.route == route)
        return [_chunk(page.document_id, route)]

    monkeypatch.setattr(page_links, "site_pages", pages)
    monkeypatch.setattr(page_links, "route_sources", chunks)
    monkeypatch.setattr(page_links, "get_settings", _FakeSettings)
    return object()


class _FakeSettings:
    site_base_url = BASE


def test_answered_reply_gets_line_and_page_citation(fake_store: object) -> None:
    other = _chunk("dx", "/services")
    cited = Citation(n=1, document_id="dx", title="t", url=None, label="x")
    response = _response("answered", sources=[other], citations=[cited])
    out = asyncio.run(
        attach_page_link("link to the zero trust page", response, fake_store)
    )
    url = f"{BASE}/zero-trust"
    assert out.answer.endswith(PAGE_LINK_LINE.format(url=url))
    assert out.outcome == "answered"
    assert [c.document_id for c in out.citations] == ["dx", "d3"]
    assert out.citations[-1].url == url
    assert out.citations[-1].n == len(out.sources)
    assert out.token_details["page_link"]["route"] == "/zero-trust"


def test_declined_reply_becomes_link_alone(fake_store: object) -> None:
    response = _response(
        "ungrounded",
        answer=NO_ANSWER_LINE,
        guardrail_flags=["not_covered"],
    )
    out = asyncio.run(
        attach_page_link("link to the booking page", response, fake_store)
    )
    url = f"{BASE}/booking"
    assert out.answer == PAGE_LINK_LINE.format(url=url)
    assert out.outcome == "answered"
    assert out.grounded is True
    assert [c.url for c in out.citations] == [url]
    assert out.token_details["outcome"] == "answered"


def test_failed_answer_call_still_gets_the_link(fake_store: object) -> None:
    response = _response(
        "empty_llm_output", answer="Sorry.", guardrail_flags=["empty_llm_output"]
    )
    out = asyncio.run(
        attach_page_link("link to the packages page", response, fake_store)
    )
    assert out.answer == PAGE_LINK_LINE.format(url=f"{BASE}/packages")
    assert out.outcome == "answered"


def test_missing_page_keeps_not_covered_reply(fake_store: object) -> None:
    response = _response(
        "ungrounded",
        answer=NO_ANSWER_LINE,
        guardrail_flags=["not_covered"],
    )
    out = asyncio.run(
        attach_page_link("link to your pricing page", response, fake_store)
    )
    assert out == response
    assert "http" not in out.answer


def test_policy_refusal_is_never_replaced(fake_store: object) -> None:
    response = _response("blocked", answer="No.", guardrail_flags=["injection"])
    out = asyncio.run(attach_page_link("link to the terms page", response, fake_store))
    assert out == response


def test_non_link_question_unchanged(fake_store: object) -> None:
    response = _response("answered")
    out = asyncio.run(attach_page_link("What is Zero Trust?", response, fake_store))
    assert out == response


def _wrapped_run(seen: dict) -> object:
    async def run_query(
        query: str,
        vector_store: object,
        boost_document_ids: list[str] | None = None,
        clarification_reply: bool = False,
    ) -> QueryResponse:
        seen.update(
            query=query,
            boost=boost_document_ids,
            clarification_reply=clarification_reply,
        )
        return _response("answered")

    return page_links.with_page_link(run_query)


def test_wrapper_answers_link_ask_from_the_named_page(fake_store: object) -> None:
    seen: dict = {}
    run = _wrapped_run(seen)
    out = asyncio.run(run(query="url for the privacy policy", vector_store=fake_store))
    assert seen == {
        "query": "Tell me about Privacy Policy",
        "boost": ["d17"],
        "clarification_reply": True,
    }
    assert out.answer.endswith(f"{BASE}/privacy")


def test_wrapper_leaves_other_questions_alone(fake_store: object) -> None:
    seen: dict = {}
    run = _wrapped_run(seen)
    out = asyncio.run(run(query="What is Zero Trust?", vector_store=fake_store))
    assert seen["query"] == "What is Zero Trust?"
    assert seen["clarification_reply"] is False
    assert "http" not in out.answer


@pytest.mark.parametrize(
    ("route", "title", "question"),
    [
        ("/", "DeployIQ | AI-Powered Cybersecurity & Compliance", "Tell me about DeployIQ"),
        ("/zero-trust", "Zero Trust Services - Deploy IQ", "Tell me about Zero Trust Services"),
        ("/get-started", "Get Started | DeployIQ", "Tell me about Get Started"),
        ("/our-vision", "", "Tell me about our vision"),
    ],
)  # fmt: skip
def test_page_question(route: str, title: str, question: str) -> None:
    assert page_links.page_question(SitePage("d", route, title)) == question
