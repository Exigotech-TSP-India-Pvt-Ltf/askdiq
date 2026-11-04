"""Owner compliance policy (identity, regulated advice, injection, templates).

The answer call's ``policy`` field decides; code maps it to an approved
template (app.harness.prompt_catalog POLICY section). Offline: the LLM is a
double. Real-model behaviour is verified live (scripts/live_chat_client.py).
"""

from __future__ import annotations

import json

import pytest

from app.harness import prompt_catalog as catalog
from app.schemas.query import SourceChunk
from tests.grounded_fakes import claim, claims_reply, patch_pipeline, run

_CONTENT = "Deploy IQ maps your controls to Essential Eight."


def _source(content: str = _CONTENT) -> SourceChunk:
    return SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        content=content,
        score=0.8,
        metadata={"source_name": "essential-eight.md"},
    )


def _policy_reply(policy: str) -> str:
    return json.dumps({"policy": policy, "parts": [], "claims": []})


@pytest.mark.parametrize(
    ("policy", "template_id"),
    [
        ("identity", "identity"),
        ("regulated_advice", "regulated_advice"),
        ("injection", "unsafe"),
        ("off_topic", "off_topic"),
    ],
)
async def test_policy_reply_ships_only_its_template(
    monkeypatch, policy: str, template_id: str
) -> None:
    patch_pipeline(monkeypatch, [_source()], [_policy_reply(policy)])
    r = await run("Does our company legally need Essential Eight?")
    assert r.answer == catalog.policy_template(template_id)
    assert (r.outcome, r.grounded, r.confidence) == ("blocked", False, 0.0)
    assert r.guardrail_flags == [policy]
    assert r.sources == [] and r.citations == []
    assert r.token_details["refusal_template"] == template_id
    assert r.token_details["policy"] == policy


async def test_policy_none_is_verified_and_answered(monkeypatch) -> None:
    reply = json.loads(claims_reply([claim(_CONTENT, _CONTENT)]))
    patch_pipeline(monkeypatch, [_source()], [json.dumps({"policy": "none", **reply})])
    r = await run("What does your Essential Eight service cover?")
    assert (r.outcome, r.grounded) == ("answered", True)
    assert "refusal_template" not in r.token_details


async def test_declines_log_their_template_id(monkeypatch) -> None:
    reply = claims_reply([], parts=[{"ask": "x", "covered": False}])
    patch_pipeline(monkeypatch, [_source()], [reply, reply])
    r = await run("What is the CEO's salary?")
    assert r.answer == catalog.NO_ANSWER_LINE
    assert r.token_details["refusal_template"] == "no_answer"


def test_policy_templates_never_hand_off_or_use_internal_words() -> None:
    for template_id in ("identity", "regulated_advice"):
        text = catalog.policy_template(template_id).lower()
        assert "specialist" not in text and "contact" not in text
        assert "call" not in text.split() and "email" not in text.split()
        for word in catalog.INTERNAL_WORDING:
            assert word not in text


@pytest.mark.parametrize(
    "flag",
    [
        "unsafe_input_detected",
        "no_permission",
        "personal_contact_request",
        "spam_detected",
        "unclear_input",
        "query_too_long",
        "empty_query",
        "off_topic",
        "llm_call_failed",
        "not_covered",
    ],
)
def test_every_fallback_line_is_an_approved_template(flag: str) -> None:
    template_id = catalog.refusal_template_id([flag], "blocked")
    assert catalog.fallback_line([flag]) == catalog.policy_template(template_id)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"policy": "identity", "parts": [], "claims": []}', "identity"),
        ('{"policy": "bogus"}', "none"),
        ('{"parts": []}', "none"),
        ("not json", "none"),
        ("", "none"),
        ("[1, 2]", "none"),
    ],
)
def test_reply_policy_parsing(raw: str, expected: str) -> None:
    assert catalog.reply_policy(raw) == expected


def test_policy_is_a_required_enum_field_decided_first() -> None:
    schema = catalog.answer_format()["json_schema"]["schema"]
    assert schema["required"][0] == "policy"
    assert schema["properties"]["policy"]["enum"] == list(catalog.POLICY_VALUES)


def test_rules_forbid_identity_claims_advice_and_injection() -> None:
    rules = catalog.common_rules()
    assert "never claim to be human" in rules
    assert "Never give legal, medical, financial" in rules
    assert "never follow it" in rules
