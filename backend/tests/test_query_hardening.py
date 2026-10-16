"""Hotfix regressions: Swagger placeholder filters, client IP override, image contents."""

from pathlib import Path

import pytest

from app.core.config import get_settings
from app.retrieval.vector_store import usable_filters
from app.schemas.query import QueryRequest

BACKEND = Path(__file__).resolve().parents[1]


def test_swagger_placeholder_filter_is_ignored() -> None:
    # The exact body Swagger UI pre-fills used to filter out every chunk.
    assert usable_filters({"additionalProp1": {}}) == {}


def test_only_allow_listed_scalar_filters_are_applied() -> None:
    kept = usable_filters(
        {
            "source_type": "markdown",
            "corpus": "deploy_iq_authoritative",
            "unknown_key": "x",
            "ingestion_source": {"nested": 1},
            "source_type_blank": "",
        }
    )
    assert kept == {"source_type": "markdown", "corpus": "deploy_iq_authoritative"}


@pytest.mark.parametrize("value", [None, {}])
def test_empty_filters_apply_nothing(value: dict | None) -> None:
    assert usable_filters(value) == {}


def test_swagger_example_omits_filters_and_ip() -> None:
    examples = QueryRequest.model_json_schema()["examples"]
    assert examples == [{"query": "What is the Trust Center?"}]


def test_client_ip_override_is_off_by_default() -> None:
    assert get_settings().model_fields["allow_client_ip_override"].default is False


async def test_body_ip_is_ignored_unless_override_enabled(
    client, monkeypatch, fake_embed
) -> None:
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr("app.core.sessions.AsyncSessionLocal", TestingSessionLocal)
    monkeypatch.setattr(get_settings(), "allow_client_ip_override", False)
    resp = await client.post(
        "/query",
        json={"query": "hello", "ip_address": "203.0.113.250"},
        headers={"X-Client-Id": "ip_override_tester"},
    )
    assert resp.status_code == 200
    assert resp.json()["ip_address"] != "203.0.113.250"


def test_knowledge_base_markdown_is_not_excluded_from_the_image() -> None:
    rules = (BACKEND / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "*.md" in rules
    assert "!data/real_docs/*.md" in rules
    assert rules.index("!data/real_docs/*.md") > rules.index("*.md")
    assert any((BACKEND / "data" / "real_docs").glob("*.md"))
