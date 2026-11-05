"""ONNX cross-encoder latency knobs (app/retrieval/cross_encoder.py).

Uses a tiny local tokenizer and a fake ONNX session; no model download.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from tokenizers.processors import TemplateProcessing

from app.core.config import get_settings
from app.retrieval import cross_encoder
from app.retrieval.cross_encoder import TextCrossEncoder, load_tokenizer

# pytest fixtures are injected by name; the fake encoder sets private slots.
# pylint: disable=redefined-outer-name,protected-access

settings = get_settings()
_WORDS = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", *(f"w{i}" for i in range(50))]


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    vocab = {word: i for i, word in enumerate(_WORDS)}
    tokenizer = Tokenizer(WordLevel(vocab, unk_token="[UNK]"))  # noqa: S106 - vocab token, not a secret
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer.post_processor = TemplateProcessing(
        single="[CLS] $A [SEP]",
        pair="[CLS] $A [SEP] $B:1 [SEP]:1",
        special_tokens=[("[CLS]", 2), ("[SEP]", 3)],
    )
    tokenizer.save(str(tmp_path / "tokenizer.json"))
    (tmp_path / "tokenizer_config.json").write_text(
        json.dumps({"model_max_length": 512, "pad_token": "[PAD]"})
    )
    (tmp_path / "config.json").write_text(json.dumps({"pad_token_id": 0}))
    return tmp_path


def _doc(n: int) -> str:
    return " ".join(f"w{i % 50}" for i in range(n))


def test_pairs_truncated_to_max_tokens(model_dir: Path) -> None:
    tokenizer = load_tokenizer(model_dir, max_tokens=16)
    encoding = tokenizer.encode("w1 w2", _doc(100))
    assert len(encoding.ids) == 16


def test_full_context_without_max_tokens(model_dir: Path) -> None:
    tokenizer = load_tokenizer(model_dir)
    assert len(tokenizer.encode("w1", _doc(100)).ids) == 104


def test_max_tokens_never_exceeds_model_context(model_dir: Path) -> None:
    tokenizer = load_tokenizer(model_dir, max_tokens=4096)
    assert len(tokenizer.encode("w1", _doc(600)).ids) == 512


def test_session_options_threads() -> None:
    options = cross_encoder.session_options(2, 3)
    assert options.intra_op_num_threads == 2
    assert options.inter_op_num_threads == 3
    assert cross_encoder.session_options(0, 0).intra_op_num_threads == 1


def test_default_thread_and_concurrency_settings() -> None:
    assert settings.reranker_intra_op_threads >= 1
    assert settings.reranker_max_tokens == 256
    assert settings.rerank_max_concurrency >= 1


class _FakeSession:  # pylint: disable=too-few-public-methods
    """Returns logits = number of non-pad tokens per row, so order is checkable."""

    def __init__(self) -> None:
        self.batch_shapes: list[tuple[int, int]] = []

    def run(self, _outputs, inputs: dict[str, np.ndarray]) -> list[np.ndarray]:
        mask = inputs["attention_mask"]
        self.batch_shapes.append(mask.shape)
        return [mask.sum(axis=1, keepdims=True).astype(np.float32) - 0.5]


def _encoder(model_dir: Path) -> tuple[TextCrossEncoder, _FakeSession]:
    encoder = object.__new__(TextCrossEncoder)
    session = _FakeSession()
    encoder._session = session
    encoder._input_names = {"input_ids", "attention_mask", "token_type_ids"}
    encoder._tokenizer = load_tokenizer(model_dir, max_tokens=64)
    return encoder, session


def test_score_pairs_returns_raw_logits_in_order(model_dir: Path) -> None:
    encoder, session = _encoder(model_dir)
    docs = [_doc(5), _doc(1), _doc(200), _doc(10)]
    scores = encoder.score_pairs("w1", docs, batch_size=1)
    # [CLS] q [SEP] doc [SEP] = len(doc) + 4 tokens (truncated to 64), minus 0.5.
    assert scores == [8.5, 4.5, 63.5, 13.5]
    assert len(session.batch_shapes) == len(docs)


def test_score_pairs_single_batch(model_dir: Path) -> None:
    encoder, session = _encoder(model_dir)
    docs = [_doc(5), _doc(1), _doc(10)]
    assert encoder.score_pairs("w1", docs, batch_size=8) == [8.5, 4.5, 13.5]
    assert session.batch_shapes == [(3, 14)]


def test_score_pairs_default_batch_size(model_dir: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "rerank_batch_size", 2)
    encoder, session = _encoder(model_dir)
    encoder.score_pairs("w1", [_doc(3)] * 5)
    assert [shape[0] for shape in session.batch_shapes] == [2, 2, 1]


def test_score_pairs_empty(model_dir: Path) -> None:
    encoder, session = _encoder(model_dir)
    assert not encoder.score_pairs("w1", [])
    assert not session.batch_shapes
