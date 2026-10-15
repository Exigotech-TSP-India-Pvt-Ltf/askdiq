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
