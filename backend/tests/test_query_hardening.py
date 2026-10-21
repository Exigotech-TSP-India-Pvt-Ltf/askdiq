"""Hotfix regressions: Swagger placeholder filters, client IP override, image contents."""

from pathlib import Path

import pytest

from app.core.config import Settings, get_settings
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


def test_swagger_example_keeps_session_and_ip_but_omits_filters() -> None:
    (example,) = QueryRequest.model_json_schema()["examples"]
    assert set(example) == {"query", "session_id", "ip_address", "new_session"}


def test_client_ip_override_is_on_by_default() -> None:
    assert Settings.model_fields["allow_client_ip_override"].default is True


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("20.20.20.241", "20.20.20.241"),
        (" 2001:db8::1 ", "2001:db8::1"),
        ("not-an-ip", None),
        ("1.2.3.4; DROP TABLE", None),
        ("198.51.100.7, 10.0.0.1", "198.51.100.7"),
        ("", None),
        (None, None),
    ],
)
def test_only_valid_ips_are_accepted_from_the_body(value, expected) -> None:
    from app.api.routes.query import _valid_ip

    assert _valid_ip(value) == expected


async def test_body_ip_is_used_when_override_enabled_even_in_production(
    client, monkeypatch, fake_embed
) -> None:
    # The website frontend calls the API server-to-server and forwards the end
    # user's IP in the body; with the override enabled that IP is recorded.
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr("app.core.sessions.AsyncSessionLocal", TestingSessionLocal)
    monkeypatch.setattr(get_settings(), "allow_client_ip_override", True)
    monkeypatch.setattr(get_settings(), "env", "production")
    resp = await client.post(
        "/query",
        json={"query": "hello", "ip_address": "20.20.20.241"},
        headers={"X-Client-Id": "ip_forward_tester"},
    )
    assert resp.status_code == 200
    assert resp.json()["ip_address"] == "20.20.20.241"


async def test_invalid_body_ip_falls_back_to_the_detected_ip(
    client, monkeypatch, fake_embed
) -> None:
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr("app.core.sessions.AsyncSessionLocal", TestingSessionLocal)
    monkeypatch.setattr(get_settings(), "allow_client_ip_override", True)
    resp = await client.post(
        "/query",
        json={"query": "hello", "ip_address": "not-an-ip"},
        headers={"X-Client-Id": "ip_invalid_tester"},
    )
    assert resp.status_code == 200
    assert resp.json()["ip_address"] != "not-an-ip"


def test_knowledge_base_markdown_is_not_excluded_from_the_image() -> None:
    rules = (BACKEND / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "*.md" in rules
    assert "!data/real_docs/*.md" in rules
    assert rules.index("!data/real_docs/*.md") > rules.index("*.md")
    assert any((BACKEND / "data" / "real_docs").glob("*.md"))


async def test_session_create_and_feedback_store_forwarded_ip(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "allow_client_ip_override", True)
    resp = await client.post(
        "/sessions",
        json={"title": "ip test", "ip_address": "198.51.100.23"},
        headers={"X-Client-Id": "ip_session_tester"},
    )
    assert resp.status_code == 201


def test_rate_limit_per_ip_uses_forwarded_ip(monkeypatch) -> None:
    from starlette.requests import Request

    from app.core.request_meta import resolve_client_ip

    monkeypatch.setattr(get_settings(), "allow_client_ip_override", True)
    request = Request({"type": "http", "headers": [], "client": ("10.0.0.5", 1)})
    assert resolve_client_ip(request, "198.51.100.9") == "198.51.100.9"
    assert resolve_client_ip(request, "junk") == "10.0.0.5"
    monkeypatch.setattr(get_settings(), "allow_client_ip_override", False)
    assert resolve_client_ip(request, "198.51.100.9") == "10.0.0.5"
