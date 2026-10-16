"""Test RAG evaluation report endpoint and regression fix for undefined files variable."""

import json

from app.api.routes.metrics import _read_latest_eval_report


def test_eval_report_file_returned(tmp_path, monkeypatch):
    """Subtask 1.1.1: Ensure evaluation report returns report_file and doesn't raise NameError."""
    # Write mock evaluation report in tmp_path
    report_file = tmp_path / "eval_report_test.json"
    dummy_data = {
        "timestamp": 1700000000.0,
        "cases": [
            {
                "id": "c1",
                "type": "retrieval",
                "query": "What is DeployIQ?",
                "passed": True,
                "known_issue": False,
                "grounded": True,
                "confidence": 0.95,
                "abstained": False,
                "guardrail_flags": [],
                "latency_ms": 120.0,
            }
        ],
    }
    report_file.write_text(json.dumps(dummy_data), encoding="utf-8")

    # Point EVAL_REPORT_DIR to tmp_path
    monkeypatch.setattr("app.api.routes.metrics.EVAL_REPORT_DIR", tmp_path)

    result = _read_latest_eval_report()
    assert result is not None
    data, filename = result
    assert filename == "eval_report_test.json"
    assert "cases" in data


def test_live_comparison_helpers_never_invent_values():
    from app.api.routes.metrics import _delta, _mean, _percentiles, _scaled

    assert _mean([], 2) is None
    assert _mean([4.0, 5.0], 2) == 4.5
    assert _delta(None, 0.5, 4) == {"benchmark": None, "live": 0.5, "diff": None}
    assert _delta(0.4, 0.5, 4)["diff"] == 0.1
    assert _scaled(None, 100) is None
    assert _scaled(0.0, 100) == 0.0
    assert _percentiles([]) is None
    assert _percentiles([100.0, 200.0])["p50"] == 200.0


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _LatencyDb:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _stmt):
        return _Rows(self.rows)


async def test_live_stage_latencies_are_measured_or_absent():
    from types import SimpleNamespace

    from app.api.routes.metrics import _live_stage_latencies

    records = [SimpleNamespace(request_id="r1", latency_ms=None)]
    # total, embed, retrieval, reranker, llm, guard_pre, guard_post
    db = _LatencyDb([(1000.0, 50.0, 80.0, None, 700.0, 10.0, 5.0)])

    stages = await _live_stage_latencies(db, records)

    assert stages["e2e"]["p50"] == 1000.0
    assert stages["llm"]["p50"] == 700.0
    assert stages["pipeline_ex_llm"]["p50"] == 300.0
    assert stages["guardrails"]["p50"] == 15.0
    assert "reranker" not in stages  # no measurement -> no made-up bar

    assert await _live_stage_latencies(_LatencyDb([]), []) == {}
