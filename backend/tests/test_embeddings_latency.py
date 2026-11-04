"""Embedding cache, batching, retry and hedging (app/retrieval/embeddings.py).

The Azure client is mocked; no network or database is used.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest

from app.core.config import get_settings
from app.core.quota import quota_monitor
from app.retrieval import embeddings

# pylint: disable=protected-access  # the module's private clients are mocked

settings = get_settings()
_REQUEST = httpx.Request("POST", "https://ci-test-resource.openai.azure.com/")


def _vector(text: str) -> list[float]:
    return [float(len(text)), 0.5, 0.25]


def _response(inputs: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        data=[
            SimpleNamespace(index=i, embedding=_vector(t)) for i, t in enumerate(inputs)
        ],
        usage=SimpleNamespace(prompt_tokens=3 * len(inputs)),
    )


async def _echo(**kwargs: list[str]) -> SimpleNamespace:
    return _response(kwargs["input"])


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    embeddings.embedding_cache.clear()
    monkeypatch.setattr(
        embeddings,
        "current_model_settings",
        lambda: SimpleNamespace(embedding_model="test-embed"),
    )
    monkeypatch.setattr(quota_monitor, "record_embedding", MagicMock())
    monkeypatch.setattr(settings, "embedding_hedge_enabled", False)
    monkeypatch.setattr(settings, "embedding_retry_backoff_seconds", 0.0)


def _mock_interactive(monkeypatch: pytest.MonkeyPatch, **kwargs) -> AsyncMock:
    create = AsyncMock(**kwargs)
    monkeypatch.setattr(embeddings._interactive_client.embeddings, "create", create)
    return create


def _mock_bulk(monkeypatch: pytest.MonkeyPatch, **kwargs) -> AsyncMock:
    create = AsyncMock(**kwargs)
    monkeypatch.setattr(embeddings._embedding_client.embeddings, "create", create)
    return create


def _timeout() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=_REQUEST)


def _bad_request() -> openai.BadRequestError:
    return openai.BadRequestError(
        "bad", response=httpx.Response(400, request=_REQUEST), body=None
    )


# --- clients ------------------------------------------------------------------


def test_interactive_client_short_timeout_no_sdk_retries() -> None:
    client = embeddings._interactive_client
    assert client.timeout == settings.embedding_timeout_seconds
    assert client.max_retries == 0
    # Shares the bulk client's connection pool.
    assert client._client is embeddings._embedding_client._client


# --- cache --------------------------------------------------------------------


async def test_repeat_query_served_from_cache(monkeypatch) -> None:
    create = _mock_interactive(monkeypatch, side_effect=_echo)
    first = await embeddings.embed_text("What is  Essential Eight?")
    second = await embeddings.embed_text("  What is Essential Eight? \n")
    assert first == second == _vector("What is Essential Eight?")
    assert create.await_count == 1
    stats = embeddings.embedding_cache_stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1


async def test_cache_keyed_by_model(monkeypatch) -> None:
    create = _mock_interactive(monkeypatch, side_effect=_echo)
    await embeddings.embed_text("q")
    monkeypatch.setattr(
        embeddings,
        "current_model_settings",
        lambda: SimpleNamespace(embedding_model="other-model"),
    )
    await embeddings.embed_text("q")
    assert create.await_count == 2
    assert create.await_args.kwargs["model"] == "other-model"


async def test_batch_sends_only_unique_misses_in_one_request(monkeypatch) -> None:
    create = _mock_interactive(monkeypatch, side_effect=_echo)
    await embeddings.embed_text("cached")
    vectors = await embeddings.embed_texts(["a", "cached", "bb", "a"])
    assert vectors == [_vector("a"), _vector("cached"), _vector("bb"), _vector("a")]
    assert create.await_count == 2
    assert create.await_args.kwargs["input"] == ["a", "bb"]


async def test_tokens_sink_only_for_sent_texts(monkeypatch) -> None:
    _mock_interactive(monkeypatch, side_effect=_echo)
    sink: list[tuple[str, int]] = []
    token = embeddings.embedding_tokens_sink.set(sink)
    try:
        await embeddings.embed_texts(["x", "y"], stage="guardrail")
        await embeddings.embed_texts(["x"], stage="guardrail")
    finally:
        embeddings.embedding_tokens_sink.reset(token)
    assert sink == [("guardrail", 6)]


def test_lru_evicts_oldest() -> None:
    cache = embeddings.EmbeddingCache(2)
    cache.put(("m", "a"), [1.0])
    cache.put(("m", "b"), [2.0])
    assert cache.get(("m", "a")) == [1.0]  # refreshes "a"
    cache.put(("m", "c"), [3.0])
    assert cache.get(("m", "b")) is None
    assert cache.get(("m", "a")) == [1.0]
    assert cache.get(("m", "c")) == [3.0]


def test_zero_size_cache_disabled() -> None:
    cache = embeddings.EmbeddingCache(0)
    cache.put(("m", "a"), [1.0])
    assert cache.get(("m", "a")) is None


# --- bulk path ----------------------------------------------------------------


async def test_ingest_uses_bulk_client_and_bypasses_cache(monkeypatch) -> None:
    interactive = _mock_interactive(monkeypatch, side_effect=_echo)
    bulk = _mock_bulk(monkeypatch, side_effect=_echo)
    texts = ["chunk  one", "chunk two"]
    await embeddings.embed_texts(texts, "RETRIEVAL_DOCUMENT", stage="ingest")
    await embeddings.embed_texts(texts, stage="ingest")
    assert bulk.await_count == 2
    assert bulk.await_args.kwargs["input"] == texts  # sent verbatim
    assert interactive.await_count == 0
    assert embeddings.embedding_cache_stats()["size"] == 0


async def test_large_batch_is_bulk(monkeypatch) -> None:
    monkeypatch.setattr(settings, "embedding_interactive_max_texts", 2)
    bulk = _mock_bulk(monkeypatch, side_effect=_echo)
    await embeddings.embed_texts(["a", "b", "c"])
    assert bulk.await_count == 1


# --- retry --------------------------------------------------------------------


async def test_transient_failure_retried_once(monkeypatch) -> None:
    monkeypatch.setattr(settings, "embedding_max_retries", 1)
    create = _mock_interactive(monkeypatch, side_effect=[_timeout(), _response(["q"])])
    assert await embeddings.embed_text("q") == _vector("q")
    assert create.await_count == 2


async def test_retry_budget_exhausted_raises_and_records_error(monkeypatch) -> None:
    monkeypatch.setattr(settings, "embedding_max_retries", 1)
    record = MagicMock()
    monkeypatch.setattr(quota_monitor, "record_embedding", record)
    create = _mock_interactive(monkeypatch, side_effect=_timeout())
    with pytest.raises(openai.APITimeoutError):
        await embeddings.embed_text("q")
    assert create.await_count == 2
    record.assert_called_once_with(0, is_error=True, is_429=False)


async def test_client_error_not_retried(monkeypatch) -> None:
    create = _mock_interactive(monkeypatch, side_effect=_bad_request())
    with pytest.raises(openai.BadRequestError):
        await embeddings.embed_text("q")
    assert create.await_count == 1


# --- hedging ------------------------------------------------------------------


async def test_slow_first_request_is_hedged(monkeypatch) -> None:
    monkeypatch.setattr(settings, "embedding_hedge_enabled", True)
    monkeypatch.setattr(settings, "embedding_hedge_after_seconds", 0.01)
    first_cancelled = asyncio.Event()
    calls = 0

    async def create(**kwargs: list[str]) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 1:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                first_cancelled.set()
                raise
        return _response(kwargs["input"])

    _mock_interactive(monkeypatch, side_effect=create)
    assert await asyncio.wait_for(embeddings.embed_text("q"), 2) == _vector("q")
    assert calls == 2
    await asyncio.wait_for(first_cancelled.wait(), 1)


async def test_fast_request_not_hedged(monkeypatch) -> None:
    monkeypatch.setattr(settings, "embedding_hedge_enabled", True)
    monkeypatch.setattr(settings, "embedding_hedge_after_seconds", 5)
    create = _mock_interactive(monkeypatch, side_effect=_echo)
    await embeddings.embed_text("q")
    assert create.await_count == 1


async def test_hedge_survives_one_failed_request(monkeypatch) -> None:
    monkeypatch.setattr(settings, "embedding_hedge_enabled", True)
    monkeypatch.setattr(settings, "embedding_hedge_after_seconds", 0.01)
    monkeypatch.setattr(settings, "embedding_max_retries", 0)
    calls = 0

    async def create(**kwargs: list[str]) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(0.05)
            raise _timeout()
        await asyncio.sleep(0.1)
        return _response(kwargs["input"])

    _mock_interactive(monkeypatch, side_effect=create)
    assert await asyncio.wait_for(embeddings.embed_text("q"), 2) == _vector("q")
    assert calls == 2
