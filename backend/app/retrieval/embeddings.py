"""Azure OpenAI embeddings.

Interactive calls (query / guardrail) go through an in-process LRU cache keyed
by ``(model, whitespace-normalised text)``, one app-level retry layer on a
short timeout (the SDK's own retries are off) and an optional hedged second
request when the first is slow. Bulk calls (ingest, document-side chunking,
very large batches) keep the long outbound timeout + SDK retries and bypass
the cache so they cannot evict hot query vectors.
"""

from __future__ import annotations

import asyncio
import threading
import time
from array import array
from collections import OrderedDict
from contextvars import ContextVar
from http import HTTPStatus
from typing import Any

from openai import AsyncAzureOpenAI

from app.core.circuit import is_transient_failure
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.quota import quota_monitor
from app.model_settings.service import current_model_settings

logger = get_logger(__name__)
settings = get_settings()

BULK_STAGE = "ingest"
BULK_TASK_TYPE = "RETRIEVAL_DOCUMENT"

# Bulk / verification client: long timeout, SDK retries (unchanged behaviour).
_embedding_client = AsyncAzureOpenAI(
    api_key=settings.azure_openai_api_key,
    azure_endpoint=settings.azure_openai_endpoint,
    api_version=settings.azure_openai_api_version,
    timeout=settings.outbound_timeout_seconds,
    max_retries=settings.azure_openai_max_retries,
)
# Interactive client: same connection pool, short timeout, no SDK retries —
# the retry/hedge layer below owns retries.
_interactive_client = _embedding_client.with_options(
    timeout=settings.embedding_timeout_seconds, max_retries=0
)


def get_embedding_client() -> AsyncAzureOpenAI:
    """Shared Azure embeddings client."""
    return _embedding_client


# Per-request sink for query-embedding time, read by the latency middleware.
embedding_timing_sink: ContextVar[list[float] | None] = ContextVar(
    "embedding_timing_sink", default=None
)

# Per-request sink for embedding tokens tagged by stage: (stage, token_count)
# Stages: "query", "guardrail", "ingest"
embedding_tokens_sink: ContextVar[list[tuple[str, int]] | None] = ContextVar(
    "embedding_tokens_sink", default=None
)
current_embedding_stage: ContextVar[str] = ContextVar(
    "current_embedding_stage", default="query"
)

try:
    import tiktoken

    _EMBED_ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception:  # pylint: disable=broad-exception-caught
    _EMBED_ENCODING = None


def normalise_text(text: str) -> str:
    """Collapse whitespace runs and trim: the cache key and the text sent."""
    return " ".join(text.split())


class EmbeddingCache:
    """Thread-safe LRU of embedding vectors (stored as float32 arrays)."""

    def __init__(self, max_entries: int) -> None:
        self.max_entries = max(0, max_entries)
        self._data: OrderedDict[tuple[str, str], array] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: tuple[str, str]) -> list[float] | None:
        if not self.max_entries:
            return None
        with self._lock:
            vector = self._data.get(key)
            if vector is None:
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
        return vector.tolist()

    def put(self, key: tuple[str, str], vector: list[float]) -> None:
        if not self.max_entries:
            return
        packed = array("f", vector)
        with self._lock:
            self._data[key] = packed
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict[str, float]:
        with self._lock:
            lookups = self.hits + self.misses
            return {
                "size": len(self._data),
                "max_entries": self.max_entries,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": self.hits / lookups if lookups else 0.0,
            }


embedding_cache = EmbeddingCache(settings.embedding_cache_size)


def embedding_cache_stats() -> dict[str, float]:
    """Process-wide cache counters (size, hits, misses, hit_rate)."""
    return embedding_cache.stats()


def _is_bulk(texts: list[str], task_type: str | None, stage: str) -> bool:
    return (
        stage == BULK_STAGE
        or task_type == BULK_TASK_TYPE
        or len(texts) > settings.embedding_interactive_max_texts
    )


async def _create(client: AsyncAzureOpenAI, model: str, inputs: list[str]) -> Any:
    return await client.embeddings.create(model=model, input=inputs)


async def _hedged_create(model: str, inputs: list[str]) -> Any:
    """One request; a second identical one if the first is still out after
    ``embedding_hedge_after_seconds``. First success wins, the other is
    cancelled."""
    if not settings.embedding_hedge_enabled:
        return await _create(_interactive_client, model, inputs)

    tasks = [asyncio.create_task(_create(_interactive_client, model, inputs))]
    try:
        done, _ = await asyncio.wait(
            tasks, timeout=settings.embedding_hedge_after_seconds
        )
        if not done:
            logger.info("embedding_hedge_fired", texts_count=len(inputs))
            tasks.append(
                asyncio.create_task(_create(_interactive_client, model, inputs))
            )
        pending = set(tasks)
        error: BaseException | None = None
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                exc = task.exception()
                if exc is None:
                    return task.result()
                error = exc
        assert error is not None  # noqa: S101 - loop exits only after a failure
        raise error
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()


async def _interactive_create(model: str, inputs: list[str]) -> Any:
    """Short-timeout request with ``embedding_max_retries`` app-level retries
    on transient failures (timeout, connection, 429, 5xx)."""
    attempts = 1 + max(0, settings.embedding_max_retries)
    for attempt in range(1, attempts + 1):
        try:
            return await _hedged_create(model, inputs)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            if attempt >= attempts or not is_transient_failure(exc):
                raise
            logger.warning(
                "embedding_retry",
                attempt=attempt,
                error_type=type(exc).__name__,
            )
            await asyncio.sleep(settings.embedding_retry_backoff_seconds)
    raise AssertionError("unreachable")  # pragma: no cover


def _count_tokens(response: Any, texts: list[str]) -> int:
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
    if prompt_tokens is None:
        if _EMBED_ENCODING is not None:
            prompt_tokens = sum(len(_EMBED_ENCODING.encode(t)) for t in texts)
        else:
            prompt_tokens = sum(len(t.split()) for t in texts) * 4 // 3
    return int(prompt_tokens)


async def _request_vectors(
    model: str, inputs: list[str], bulk: bool
) -> tuple[list[list[float]], int]:
    """Embed ``inputs`` in one request; returns (vectors in order, tokens)."""
    try:
        if bulk:
            response = await _create(_embedding_client, model, inputs)
        else:
            response = await _interactive_create(model, inputs)
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        is_429 = status_code == HTTPStatus.TOO_MANY_REQUESTS or "429" in str(exc)
        quota_monitor.record_embedding(0, is_error=True, is_429=is_429)
        if is_429:
            logger.error(
                "embedding_throttle_429",
                model=model,
                texts_count=len(inputs),
                error=str(exc),
            )
        raise
    data = sorted(response.data, key=lambda item: getattr(item, "index", 0))
    return [item.embedding for item in data], _count_tokens(response, inputs)


async def _embed_interactive(
    model: str, texts: list[str]
) -> tuple[list[list[float]], int, int, int]:
    """Cache-first: (vectors, texts sent, cache hits, tokens). Unique misses
    go out whitespace-normalised in ONE request."""
    normalised = [normalise_text(t) for t in texts]
    results = [embedding_cache.get((model, n)) for n in normalised]
    slots: dict[str, list[int]] = {}
    for i, (norm, vec) in enumerate(zip(normalised, results, strict=True)):
        if vec is None:
            slots.setdefault(norm, []).append(i)
    tokens = 0
    if slots:
        to_send = list(slots)
        vectors, tokens = await _request_vectors(model, to_send, bulk=False)
        for norm, vector in zip(to_send, vectors, strict=True):
            embedding_cache.put((model, norm), vector)
            for i in slots[norm]:
                results[i] = vector
    cache_hits = len(texts) - sum(len(v) for v in slots.values())
    return [v for v in results if v is not None], len(slots), cache_hits, tokens


async def embed_texts(
    texts: list[str],
    task_type: str | None = None,
    stage: str | None = None,
) -> list[list[float]]:
    """Generate embeddings using Azure OpenAI and capture stage tokens.

    All texts not served from the cache go out in ONE request. Returns one
    vector per input text, in input order.
    """
    if not texts:
        return []

    model = current_model_settings().embedding_model
    effective_stage = stage or current_embedding_stage.get()
    t_start = time.perf_counter()

    if _is_bulk(texts, task_type, effective_stage):
        vectors, tokens = await _request_vectors(model, list(texts), bulk=True)
        sent, cache_hits = len(texts), 0
    else:
        vectors, sent, cache_hits, tokens = await _embed_interactive(model, texts)

    if sent:
        quota_monitor.record_embedding(tokens)
        sink = embedding_tokens_sink.get()
        if sink is not None:
            sink.append((effective_stage, tokens))

    logger.info(
        "embedding_batch_complete" if sent else "embedding_cache_served",
        stage=effective_stage,
        texts_count=len(texts),
        sent=sent,
        cache_hits=cache_hits,
        tokens=tokens,
        latency_ms=round((time.perf_counter() - t_start) * 1000, 2),
    )
    return vectors


async def embed_text(
    text: str,
    task_type: str | None = None,
    stage: str | None = None,
) -> list[float]:
    """Generate a single embedding using Azure OpenAI."""
    embeddings = await embed_texts([text], task_type=task_type, stage=stage)
    return embeddings[0]
