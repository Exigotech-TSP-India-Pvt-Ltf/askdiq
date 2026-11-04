"""Offline doubles for run_query around the quote-backed answer call.

They pin code contracts only (mocked LLM = not behaviour evidence; the live
golden eval is). ``patch_pipeline`` fakes pre-checks, the intent classifier,
retrieval + route gate and the LLM; ``claims_reply`` builds the strict JSON
the answer call returns.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from app.guardrails.intent import IntentMatch
from app.harness import orchestrator
from app.harness.decision import Intent, IntentResult, PartVerdict, Route, RouteDecision
from app.retrieval.retriever import PartRetrieval, RetrievalResult
from app.schemas.query import SourceChunk

USAGE = {"prompt_tokens": 10, "completion_tokens": 5}


def claim(
    text: str,
    quote: str | None = None,
    source: int = 1,
    part: int = 0,
    kind: str = "sentence",
) -> dict:
    return {
        "text": text,
        "quote": quote if quote is not None else text,
        "source": source,
        "part": part,
        "kind": kind,
    }


def claims_reply(claims: list[dict], parts: list[dict] | None = None) -> str:
    return json.dumps(
        {"parts": parts or [{"ask": "the question", "covered": True}], "claims": claims}
    )


def patch_pipeline(
    monkeypatch,
    sources: Sequence[SourceChunk],
    reply: str | BaseException | list[str | BaseException],
    *,
    route: Route = Route.ANSWER,
    intent: Intent = Intent.QUESTION,
    calls: list | None = None,
) -> None:
    """``reply``: one LLM reply (or exception), or a list consumed per call."""
    replies = list(reply) if isinstance(reply, list) else None
    monkeypatch.setattr(orchestrator, "run_pre_checks", lambda *_a, **_k: [])

    async def fake_intent(_q):
        return IntentMatch(IntentResult(intent, 0.9))

    async def fake_retrieve(*_a, **_k):
        part = PartRetrieval(
            query="q",
            max_score=0.8,
            chunk_ids=frozenset(s.chunk_id for s in sources),
            contents=tuple(s.content for s in sources),
            sources=tuple(sources),
        )
        return RetrievalResult(sources=list(sources), parts=[part])

    def fake_route(_parts, *, buying=False):
        covered = route in (Route.ANSWER, Route.PARTIAL)
        return RouteDecision(route, parts=(PartVerdict("q", covered),), buying=buying)

    async def fake_llm(query, chunks, history=None, **kwargs):
        if calls is not None:
            calls.append({"query": query, "chunks": chunks, "kwargs": kwargs})
        current = replies.pop(0) if replies is not None else reply
        if isinstance(current, BaseException):
            raise current
        return current, dict(USAGE)

    monkeypatch.setattr(orchestrator, "classify_intent", fake_intent)
    monkeypatch.setattr(orchestrator, "retrieve_parts", fake_retrieve)
    monkeypatch.setattr(orchestrator, "decide_route", fake_route)
    monkeypatch.setattr(orchestrator, "clarify_allowed", lambda *_a, **_k: False)
    monkeypatch.setattr(orchestrator, "call_llm", fake_llm)


async def run(query: str, **kwargs):
    return await orchestrator.run_query(
        query=query,
        top_k=5,
        filters=None,
        vector_store=None,
        request_id="offline",
        stage_timings={},
        **kwargs,
    )
