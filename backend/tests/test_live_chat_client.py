"""Unit tests for scripts/live_chat_client.py (mocked transport, no network)."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "live_chat_client.py"
_spec = importlib.util.spec_from_file_location("live_chat_client", _PATH)
lc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lc)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(lc, "BACKOFF_BASE_S", 0)


def _ok(**over):
    base = {
        "answer": "DeployIQ is a deployment tool.",
        "sources": [{"chunk_id": "c"}],
        "outcome": "answered",
        "guardrail_flags": [],
        "session_id": "s1",
    }
    return {**base, **over}


def _transport(handler):
    return httpx.MockTransport(handler)


def test_ask_sends_headers_and_returns_wall_time():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers["x-api-key"]
        seen["cid"] = request.headers["x-client-id"]
        return httpx.Response(200, json=_ok())

    out = lc.ask("hi", new_session=True, transport=_transport(handler), api_key="k")
    assert seen["body"] == {"query": "hi", "new_session": True}
    assert seen["key"] == "k"
    assert out["client_id"] == seen["cid"]
    assert out["wall_ms"] >= 0


def test_retries_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        status = 429 if len(calls) < 3 else 200
        return httpx.Response(status, json=_ok())

    out = lc.ask("hi", transport=_transport(handler), api_key="k")
    assert len(calls) == 3
    assert out["outcome"] == "answered"


def test_gives_up_after_max_attempts():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, text="down")

    with pytest.raises(lc.LiveClientError):
        lc.ask("hi", transport=_transport(handler), api_key="k")
    assert len(calls) == 3


def test_client_error_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, text="no")

    with pytest.raises(lc.LiveClientError):
        lc.ask("hi", transport=_transport(handler), api_key="k")
    assert len(calls) == 1


def test_conversation_runner_keeps_session():
    bodies, cids = [], []

    def handler(request):
        bodies.append(json.loads(request.content))
        cids.append(request.headers["x-client-id"])
        return httpx.Response(200, json=_ok())

    run = lc.ConversationRunner(transport=_transport(handler), api_key="k")
    run.ask("one")
    run.ask("two")
    assert "session_id" not in bodies[0]
    assert bodies[1]["session_id"] == "s1"
    assert len(set(cids)) == 1
    assert len(run.turns) == 2


def test_check_answer_clean():
    assert lc.check_answer(_ok()) == []


def test_internal_and_assurance_words():
    resp = _ok(answer="Based on the knowledge base it is guaranteed and 100% safe.")
    problems = lc.check_answer(resp)
    assert any("knowledge base" in p for p in problems)
    assert any("guaranteed" in p for p in problems)
    assert any("100% safe" in p for p in problems)
    allowed = {"allow_assurance": True, "allow_internal": True}
    assert lc.check_answer(resp, allowed) == []


def test_empty_answer():
    assert "empty answer" in lc.check_answer(_ok(answer="  "))


def test_fallback_needs_specialist():
    resp = _ok(outcome="ungrounded", sources=[], answer="I could not find that.")
    assert any("specialist" in p for p in lc.check_answer(resp))
    resp["answer"] = "Please contact a specialist."
    assert lc.check_answer(resp) == []


def test_refusal_must_have_no_sources():
    resp = _ok(outcome="blocked", guardrail_flags=["spam_detected"])
    assert lc.response_kind(resp) == "refusal"
    assert any("sources" in p for p in lc.check_answer(resp))
    resp["sources"] = []
    assert lc.check_answer(resp) == []


def test_clarification_structure():
    question = {"id": "Q1", "question": "Which?", "options": [{"id": "O1"}]}
    resp = _ok(outcome="clarify", sources=[], clarification={"questions": [question]})
    assert any("1 options" in p for p in lc.check_answer(resp))
    question["options"].append({"id": "O2"})
    assert lc.check_answer(resp) == []
    assert lc.check_answer(_ok(outcome="clarify", sources=[]))
