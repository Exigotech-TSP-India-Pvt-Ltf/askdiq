"""STEP 5 reranker over the broad cosine-recall candidate pool.

Chosen algorithm: cross-encoder reranking, scoring (query, chunk) pairs with
`Xenova/ms-marco-MiniLM-L-6-v2` via `fastembed`'s ONNX runtime. This is the
PREFERRED option for a small local corpus (per the task spec) without adding
a torch/sentence-transformers dependency — `fastembed` + `onnxruntime` are
already in requirements.txt. MMR is intentionally not layered on top: dedupe
by chunk id (STEP 2) plus cross-document quota (STEP 3) already provide the
diversity MMR would add, on a corpus this small.

The cross-encoder is used to REORDER the candidate pool only — its raw
logits aren't on a comparable, query-independent scale (empirically they can
be strongly negative even for on-topic-but-narrow questions), so the
reported score stays the original cosine similarity. That keeps the
off-topic / low-confidence gates in orchestrator.py well-calibrated.
"""

import logging
import threading
import time
from contextvars import ContextVar
from functools import lru_cache

from app.core.config import get_settings
from app.models.db_models import Chunk

logger = logging.getLogger(__name__)

settings = get_settings()

# Optional per-request sink: the evaluation harness sets a list here and each
# rerank call appends its duration in ms (None outside evaluation).
rerank_timing_sink: ContextVar[list[float] | None] = ContextVar(
    "rerank_timing_sink", default=None
)


_rerank_slots = threading.BoundedSemaphore(max(1, settings.rerank_max_concurrency))
_encoder_lock = threading.Lock()


@lru_cache(maxsize=1)
def _load_encoder():
    from fastembed.rerank.cross_encoder import TextCrossEncoder  # noqa: PLC0415 - heavy model dependency, loaded on first use

    return TextCrossEncoder(model_name=settings.reranker_model)


def _get_encoder():
    # Lock so concurrent first callers don't each load the model / race imports.
    with _encoder_lock:
        return _load_encoder()


def preload_reranker() -> None:
    """Force-load the cross-encoder at process startup instead of on the
    first real request.

    `_get_encoder()` is `@lru_cache`'d, so without this the ONNX model load
    (plus a first-run ~90MB download from HF Hub) happens lazily inside the
    very first user's /query call, making that request take much longer
    than every one after it. Calling this once at startup moves that cost
    out of the request path entirely.
    """
    if not settings.enable_reranker:
        return

    try:
        _get_encoder()
        logger.info("reranker_preload status=ok model=%s", settings.reranker_model)
    except Exception as exc:
        logger.error("reranker_preload status=failed error=%s", exc)


def is_reranker_loaded() -> bool:
    """Return True if the reranker model weights are loaded in memory."""
    return _load_encoder.cache_info().currsize > 0


def rerank(
    query: str,
    candidates: list[tuple[Chunk, float]],
) -> list[tuple[Chunk, float]]:
    """Reorder (chunk, cosine_score) candidates by cross-encoder relevance.

    The cosine score is preserved (only the order changes). Falls back to
    the original cosine ordering if the reranker model can't be loaded (e.g.
    no network on first run), so retrieval never hard-fails.
    """

    if not settings.enable_reranker or len(candidates) <= 1:
        return candidates

    try:
        encoder = _get_encoder()
        documents = [chunk.content for chunk, _ in candidates]
        with _rerank_slots:
            # Timed inside the slot: pure compute, excluding queue wait.
            started = time.perf_counter()
            raw_scores = list(
                encoder.rerank(query, documents, batch_size=settings.rerank_batch_size)
            )
            compute_ms = (time.perf_counter() - started) * 1000
        sink = rerank_timing_sink.get()
        if sink is not None:
            sink.append(compute_ms)
    except Exception:
        logger.warning(
            "Reranker unavailable, falling back to cosine order", exc_info=True
        )
        return candidates

    order = sorted(range(len(candidates)), key=lambda i: raw_scores[i], reverse=True)

    return [candidates[i] for i in order]
