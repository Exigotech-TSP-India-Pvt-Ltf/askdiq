"""POST /query input length: the schema only enforces a hard server cap.

Over-length (> ``max_query_length``) and blank questions must reach the
pre-checks so the user gets the friendly too-long / empty template, not a 422.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.guardrails.pre_checks import check_length
from app.harness import prompt_catalog as catalog
from app.schemas.query import QUERY_HARD_MAX_LENGTH, ChatTurn, QueryRequest
from main import app


@pytest.mark.parametrize("length", [2001, 5000, QUERY_HARD_MAX_LENGTH])
def test_over_product_limit_is_accepted_and_flagged_too_long(length: int) -> None:
    query = "a" * length
    assert QueryRequest(query=query).query == query
    ok, flag = check_length(query, get_settings().max_query_length)
    assert (ok, flag) == (False, "query_too_long")
    assert catalog.fallback_line([flag]) == catalog.TOO_LONG_LINE


def test_beyond_hard_cap_is_422() -> None:
    with pytest.raises(ValidationError, match="query"):
        QueryRequest(query="a" * (QUERY_HARD_MAX_LENGTH + 1))


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_empty_or_blank_is_accepted_and_flagged_empty(query: str) -> None:
    assert QueryRequest(query=query).query == query
    ok, flag = check_length(query, get_settings().max_query_length)
    assert (ok, flag) == (False, "empty_query")
    assert catalog.fallback_line([flag]) == catalog.policy_template("empty")


@pytest.mark.parametrize("content", ["", "a" * 5000])
def test_stored_blank_or_long_question_still_loads_as_history(content: str) -> None:
    assert ChatTurn(role="user", content=content).content == content


def test_openapi_documents_the_query_limits() -> None:
    prop = app.openapi()["components"]["schemas"]["QueryRequest"]["properties"]
    query = prop["query"]
    assert query["maxLength"] == QUERY_HARD_MAX_LENGTH
    assert "minLength" not in query
    for word in ("query_too_long", "empty_query", "422"):
        assert word in query["description"]
