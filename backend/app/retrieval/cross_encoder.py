"""Minimal ONNX cross-encoder (query, document) scorer.

Replaces `fastembed.rerank.cross_encoder.TextCrossEncoder` for the one thing
the app needs: scoring pairs with an HF-hosted ONNX cross-encoder such as
`Xenova/ms-marco-MiniLM-L-6-v2`. It mirrors fastembed's text path exactly
(same model file, tokenizer truncation/padding, graph optimisation and
`logits[:, 0]` output) using only `onnxruntime`, `tokenizers` and
`huggingface_hub`, so scores are identical while dropping fastembed's
`pillow<13` pin.

Latency knobs (app/core/config.py): ONNX intra/inter-op threads
(RERANKER_INTRA_OP_THREADS / RERANKER_INTER_OP_THREADS) and pair truncation
(RERANKER_MAX_TOKENS, default 256). ``score_pairs`` returns the raw logits
for a whole candidate list in one call. Pairs are run through ONNX
RERANK_BATCH_SIZE at a time (default 1): measured on the KB's ~100-token
pairs, one padded 12-pair batch is ~1.5-2x slower than 12 single runs, even
with length-sorted, padding-trimmed sub-batches.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
from huggingface_hub import model_info, snapshot_download
from tokenizers import AddedToken, Encoding, Tokenizer

from app.core.config import get_settings

MODEL_FILE = "onnx/model.onnx"
_MODEL_FILES = [
    MODEL_FILE,
    "tokenizer.json",
    "tokenizer_config.json",
    "config.json",
    "special_tokens_map.json",
]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _valid_context(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 < value <= sys.maxsize else None


def _max_context(tokenizer_config: dict[str, Any], model_dir: Path) -> int:
    candidates = [
        ctx
        for ctx in (
            _valid_context(tokenizer_config.get("model_max_length")),
            _valid_context(tokenizer_config.get("max_length")),
        )
        if ctx is not None
    ]
    if not candidates:
        raise ValueError(f"Could not determine max context length for {model_dir}")
    return min(candidates)


def _iter_special_tokens(tokens_map: dict[str, Any]) -> Iterator[Any]:
    for value in tokens_map.values():
        if isinstance(value, list):
            yield from value
        else:
            yield value


def load_tokenizer(model_dir: Path, max_tokens: int | None = None) -> Tokenizer:
    """Load the tokenizer with truncation + batch-longest padding.

    Pairs are truncated to ``max_tokens`` (capped at the model's context);
    ``None`` keeps the model's full context.
    """
    tokenizer_config = _read_json(model_dir / "tokenizer_config.json")
    config = _read_json(model_dir / "config.json")
    tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
    max_length = _max_context(tokenizer_config, model_dir)
    if max_tokens is not None:
        max_length = min(max_length, max_tokens)
    tokenizer.enable_truncation(max_length=max_length)

    for token in _iter_special_tokens(
        _read_json(model_dir / "special_tokens_map.json")
    ):
        if isinstance(token, str):
            tokenizer.add_special_tokens([token])
        elif isinstance(token, dict):
            tokenizer.add_special_tokens([AddedToken(**token)])

    padding = tokenizer.padding or {}
    pad_token = padding.get("pad_token") or tokenizer_config.get("pad_token")
    if pad_token is None:
        raise ValueError(f"Could not find a pad token for {model_dir}")
    pad_id = padding.get("pad_id", config.get("pad_token_id"))
    if pad_id is None:
        pad_id = tokenizer.token_to_id(pad_token)
    if pad_id is None:
        raise ValueError(f"Could not resolve pad token id for {model_dir}")

    tokenizer.enable_padding(
        direction=padding.get("direction", "right"),
        pad_id=pad_id,
        pad_type_id=padding.get("pad_type_id", 0),
        pad_token=pad_token,
        pad_to_multiple_of=padding.get("pad_to_multiple_of"),
        length=None,
    )
    return tokenizer


def session_options(intra_op_threads: int, inter_op_threads: int) -> ort.SessionOptions:
    """CPU session options: full graph optimisation, bounded thread pools."""
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.intra_op_num_threads = max(1, intra_op_threads)
    options.inter_op_num_threads = max(1, inter_op_threads)
    return options


class TextCrossEncoder:
    """ONNX cross-encoder returning one raw relevance logit per document."""

    def __init__(
        self,
        model_name: str,
        cache_dir: str | None = None,
        *,
        intra_op_threads: int | None = None,
        inter_op_threads: int | None = None,
        max_tokens: int | None = None,
    ) -> None:
        settings = get_settings()
        # Pin to the resolved commit sha (mirrors fastembed's own model_info().sha
        # lookup) so the download is reproducible rather than tracking a mutable
        # branch ref. nosec: revision is a resolved commit sha, not attacker input.
        revision = model_info(model_name).sha
        model_dir = Path(
            snapshot_download(
                repo_id=model_name,
                revision=revision,
                allow_patterns=_MODEL_FILES,
                cache_dir=cache_dir,
            )
        )
        options = session_options(
            intra_op_threads or settings.reranker_intra_op_threads,
            inter_op_threads or settings.reranker_inter_op_threads,
        )
        self._session = ort.InferenceSession(
            str(model_dir / MODEL_FILE),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._input_names = {node.name for node in self._session.get_inputs()}
        self._tokenizer = load_tokenizer(
            model_dir, max_tokens or settings.reranker_max_tokens
        )

    def _build_inputs(self, encodings: list[Encoding]) -> dict[str, np.ndarray]:
        inputs = {"input_ids": np.array([e.ids for e in encodings], dtype=np.int64)}
        if "token_type_ids" in self._input_names:
            inputs["token_type_ids"] = np.array(
                [e.type_ids for e in encodings], dtype=np.int64
            )
        if "attention_mask" in self._input_names:
            inputs["attention_mask"] = np.array(
                [e.attention_mask for e in encodings], dtype=np.int64
            )
        return inputs

    def _score_batch(self, query: str, documents: list[str]) -> list[float]:
        encodings = self._tokenizer.encode_batch([(query, doc) for doc in documents])
        logits = self._session.run(None, self._build_inputs(encodings))[0]
        return [float(score) for score in logits[:, 0]]

    def score_pairs(
        self, query: str, documents: Iterable[str], batch_size: int | None = None
    ) -> list[float]:
        """Raw cross-encoder logits, one per document, in input order.

        ``batch_size=None`` uses RERANK_BATCH_SIZE pairs per ONNX run.
        """
        size = batch_size or get_settings().rerank_batch_size
        return list(self.rerank(query, documents, batch_size=max(1, size)))

    def rerank(
        self, query: str, documents: Iterable[str], batch_size: int = 64
    ) -> Iterator[float]:
        """Yield a relevance score per document, in input order."""
        batch: list[str] = []
        for doc in documents:
            batch.append(doc)
            if len(batch) >= batch_size:
                yield from self._score_batch(query, batch)
                batch = []
        if batch:
            yield from self._score_batch(query, batch)
