"""Online evaluation rules that need no database (the session is faked)."""

from __future__ import annotations

import pytest

from app.governance import online_eval
from app.models.db_models import EvaluationRecord


class _FakeSession:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def add(self, obj: object) -> None:
        self._sink.append(obj)

    async def commit(self) -> None:
        return None


@pytest.fixture
def stored(monkeypatch) -> list:
    sink: list = []
    monkeypatch.setattr(online_eval, "AsyncSessionLocal", lambda: _FakeSession(sink))
    return sink


async def _evaluate(outcome: str) -> None:
    await online_eval._run_evaluation_task(
        request_id=f"rule-{outcome}",
        query="What is Deploy IQ?",
        answer="",
        context_chunks=[],
        outcome=outcome,
    )


@pytest.mark.parametrize("outcome", ["llm_failed", "empty_llm_output"])
async def test_llm_failures_never_pass(stored, outcome) -> None:
    await _evaluate(outcome)
    record = next(o for o in stored if isinstance(o, EvaluationRecord))
    assert record.passed is False


async def test_answered_turn_can_pass(stored) -> None:
    await _evaluate("answered")
    record = next(o for o in stored if isinstance(o, EvaluationRecord))
    assert record.passed is True


def test_judge_sampling_honours_the_rate(monkeypatch) -> None:
    assert online_eval.judge_sampled(1.0) is True
    assert online_eval.judge_sampled(0.0) is False
    monkeypatch.setattr(online_eval.random, "random", lambda: 0.05)
    assert online_eval.judge_sampled(0.10) is True
    monkeypatch.setattr(online_eval.random, "random", lambda: 0.5)
    assert online_eval.judge_sampled(0.10) is False


async def test_judge_runs_only_for_sampled_answers(stored, monkeypatch) -> None:
    calls: list = []

    async def fake_judge(**_kwargs):
        calls.append(1)

    monkeypatch.setattr(online_eval, "judge_answer", fake_judge)
    monkeypatch.setattr(online_eval.random, "random", lambda: 0.9)
    settings = online_eval.get_settings()
    monkeypatch.setattr(settings, "online_eval_enabled", True)
    monkeypatch.setattr(settings, "online_eval_sample_rate", 0.10)

    await online_eval._run_evaluation_task(
        request_id="rule-unsampled",
        query="q",
        answer="An answer.",
        context_chunks=["context"],
    )
    assert calls == []


def test_mean_faithfulness_is_none_before_any_sample() -> None:
    tracker = online_eval.OnlineQualityTracker()
    assert tracker.get_kpis()["mean_faithfulness"] is None
    tracker.record([], faithfulness=4.0)
    assert tracker.get_kpis()["mean_faithfulness"] == 4.0
