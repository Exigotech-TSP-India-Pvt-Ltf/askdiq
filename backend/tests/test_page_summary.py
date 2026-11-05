"""Summarize / explain a named or linked page: that page alone answers it."""

from __future__ import annotations

import pytest

from app.guardrails.pre_checks import check_spam
from app.harness.decision import PartVerdict, Route, RouteDecision
from app.retrieval.retriever import summary_page_keys, trust_page_summary
from app.schemas.query import ChatTurn


@pytest.mark.parametrize(
    ("query", "key"),
    [
        (
            "summarize this page for me: https://staging.deployiq.co/services",
            "page:services",
        ),
        ("give me a summary of the about us page", "page:about us"),
        ("summarise the packages page", "packages"),
        ("explain https://deployiq.co/zero-trust", "zero trust"),
        ("https://deployiq.co/packages what is on this page?", "packages"),
    ],
)
def test_summary_of_a_named_page(query: str, key: str) -> None:
    assert summary_page_keys(query) == [key]


@pytest.mark.parametrize(
    "query",
    [
        "Explain Zero Trust",
        "What services do you offer?",
        "summarize https://www.google.com",
    ],
)
def test_not_a_page_summary(query: str) -> None:
    assert summary_page_keys(query) == []


def test_follow_up_points_back_at_the_page() -> None:
    history = [
        ChatTurn(role="user", content="summarize https://deployiq.co/services"),
        ChatTurn(role="assistant", content="Deploy IQ delivers security services."),
    ]
    assert summary_page_keys("explain it in simpler words", history) == [
        "page:services"
    ]
    assert summary_page_keys("explain it in simpler words") == []


def test_a_low_scored_page_summary_is_answered() -> None:
    low = RouteDecision(
        Route.NOT_COVERED, parts=(PartVerdict("summary", False, -10.6, ("d",)),)
    )
    assert trust_page_summary(low).route is Route.ANSWER
    off = RouteDecision(Route.OFF_TOPIC, parts=(PartVerdict("x", False, -14.0, ()),))
    assert trust_page_summary(off) is off


@pytest.mark.parametrize(
    "query",
    ["explain https://staging.deployiq.co/zero-trust", "https://deployiq.co/services"],
)
def test_one_site_link_is_not_spam(query: str) -> None:
    assert check_spam(query) == (True, None)


def test_link_spam_is_still_spam() -> None:
    assert check_spam("cheap pills https://x.com")[0] is False
    assert check_spam("https://a.com https://b.com")[0] is False


def test_reply_language_is_not_an_off_question_term() -> None:
    from app.guardrails.task_type import strip_reply_language  # noqa: PLC0415
    from app.harness.orchestrator import _ask_terms  # noqa: PLC0415

    terms = " ".join(_ask_terms(strip_reply_language("What is Zero Trust in German?")))
    assert "german" not in terms.lower()
    assert "zero trust" in terms.lower()


@pytest.mark.parametrize(
    ("query", "key"),
    [
        ("expand https://deployiq.co/secure-ai", "secure ai"),
        ("describe the responsible ai page", "responsible ai"),
        ("https://deployiq.co/secure-ai", "secure ai"),
    ],
)
def test_expand_describe_and_bare_links_are_page_asks(query: str, key: str) -> None:
    assert summary_page_keys(query) == [key]


def test_a_named_topic_without_a_page_is_not_a_page_ask() -> None:
    assert summary_page_keys("expand zero trust") == []
