"""Async-only guard: no blocking endpoints or helpers."""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
import weakref

import pytest
from fastapi.routing import RouteContext

from app.harness import orchestrator
from app.ingestion import service as ingestion_service
from app.insights import classifier
from app.retrieval import embeddings, reranker, vector_store
from app.schemas.query import SourceChunk
from main import app
from tests import test_retrieval_misses as misses
from tests.grounded_fakes import claim, claims_reply, patch_pipeline, run
from tests.route_helpers import flat_routes, is_api_route


def _endpoint_routes() -> list[RouteContext]:
    return [route for route in flat_routes(app) if is_api_route(route)]


def test_app_exposes_routes():
    assert len(_endpoint_routes()) > 10


def test_every_endpoint_is_async_def():
    offenders = [
        f"{sorted(route.methods)} {route.path} -> {route.endpoint.__qualname__}"
        for route in _endpoint_routes()
        if not inspect.iscoroutinefunction(route.endpoint)
    ]
    assert not offenders, "Plain def endpoints tie up a threadpool slot:\n" + (
        "\n".join(offenders)
    )


def test_unused_sync_embedding_helpers_are_gone():
    assert not hasattr(embeddings, "embed_texts_sync")
    assert not hasattr(embeddings, "embed_text_sync")


# --- CPU-bound pipeline steps run off the event-loop thread ------------------
# Deterministic and offline: each spy records the thread it ran on.


def _thread_spy(func, seen: list[int]):
    def spy(*args, **kwargs):
        seen.append(threading.get_ident())
        return func(*args, **kwargs)

    return spy


async def test_claim_grounding_runs_off_the_event_loop(monkeypatch):
    content = "Deploy IQ manages Essential Eight compliance."
    seen: list[int] = []
    monkeypatch.setattr(
        orchestrator, "ground_answer", _thread_spy(orchestrator.ground_answer, seen)
    )
    patch_pipeline(
        monkeypatch,
        [
            SourceChunk(
                chunk_id="00000000-0000-0000-0000-000000000001",
                document_id="00000000-0000-0000-0000-000000000002",
                content=content,
                score=0.8,
                metadata={"source_name": "about-us.md"},
            )
        ],
        claims_reply([claim(content, content)]),
    )

    response = await run("What is Deploy IQ?")

    assert response.answer == f"{content} [1]"  # behaviour unchanged
    assert seen
    assert threading.get_ident() not in seen


async def test_ingest_normalization_runs_off_the_event_loop(monkeypatch):
    seen: list[int] = []

    class _StopError(Exception):
        pass

    def spy(_content):
        seen.append(threading.get_ident())
        raise _StopError

    monkeypatch.setattr(ingestion_service, "normalize_markdown", spy)
    with pytest.raises(_StopError):
        await ingestion_service.ingest_text(None, source_name="a.md", content="hi")

    assert seen
    assert threading.get_ident() not in seen


@pytest.mark.parametrize("reranker_on", [True, False])
async def test_hybrid_rerank_and_selection_run_off_the_event_loop(
    monkeypatch, reranker_on
):
    seen: list[int] = []
    monkeypatch.setattr(reranker.settings, "enable_reranker", reranker_on)
    monkeypatch.setattr(reranker.settings, "retrieval_hybrid_enabled", True)
    monkeypatch.setattr(
        vector_store,
        "compute_hybrid_signal",
        _thread_spy(vector_store.compute_hybrid_signal, seen),
    )
    monkeypatch.setattr(
        vector_store, "rerank", _thread_spy(lambda _q, c, _b=None, _l=None: c, seen)
    )

    results = await misses._search(misses.STAGES_COS, misses.Q_STAGES)  # pylint: disable=protected-access

    assert results
    assert len(seen) == 2
    assert threading.get_ident() not in seen


async def test_rerank_slots_cap_concurrency_without_blocking_threads(monkeypatch):
    monkeypatch.setattr(reranker.settings, "rerank_max_concurrency", 1)
    monkeypatch.setattr(reranker, "_loop_slots", weakref.WeakKeyDictionary())
    lock = threading.Lock()
    active = peak = 0

    def work() -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return threading.get_ident()

    idents = await asyncio.gather(
        *(reranker.run_in_rerank_slot(work) for _ in range(3))
    )

    assert peak == 1
    assert threading.get_ident() not in idents


async def test_insight_rule_pass_runs_off_the_event_loop(monkeypatch):
    seen: list[int] = []

    def spy(_turns, _taxonomy):
        seen.append(threading.get_ident())
        return [], []

    monkeypatch.setattr(classifier, "split_by_rule", spy)
    result = await classifier.classify_turns(
        [], None, batch_size=10, budget_remaining=None
    )

    assert result.labels == []
    assert seen
    assert threading.get_ident() not in seen
