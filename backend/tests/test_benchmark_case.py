"""Benchmark report cases in the evaluation drawer shape (VA-12, VA-14).

A value the report file does not carry stays ``None`` ("not measured"):
it is never filled with a plausible score, and the response text is passed
through when the report stores it.
"""

from __future__ import annotations

from app.api.routes.metrics import _benchmark_case


def test_missing_values_stay_none_not_invented() -> None:
    case = _benchmark_case({"id": "c1", "query": "Q?", "type": "answerable"})
    assert case["source"] == "benchmark"
    for key in (
        "answer",
        "passed",
        "grounded",
        "confidence",
        "latency_ms",
        "faithfulness",
        "relevance",
        "completeness",
        "citation_precision",
        "citation_coverage",
        "recall",
        "judge_reason",
        "safety_passed",
    ):
        assert case[key] is None, key
    assert case["flags"] == []
    assert case["unsupported_claims"] == []


def test_measured_values_and_response_text_pass_through() -> None:
    case = _benchmark_case(
        {
            "id": "c2",
            "query": "What is Essential Eight?",
            "answer": "A set of mitigation strategies.",
            "passed": False,
            "grounded": True,
            "guardrail_flags": ["pii_leak_detected"],
            "safety_ok": False,
            "judge": {
                "faithfulness": 4.0,
                "relevance": 3.5,
                "reason": "Partly supported.",
            },
            "citation": {"precision": 0.5, "coverage": 0.25},
            "retrieval_metrics": {"recall@k": 0.75},
            "latency_ms": 812.0,
        }
    )
    assert case["answer"] == "A set of mitigation strategies."
    assert case["passed"] is False
    assert case["faithfulness"] == 4.0
    assert case["relevance"] == 3.5
    assert case["completeness"] is None
    assert case["citation_precision"] == 0.5
    assert case["recall"] == 0.75
    assert case["flags"] == ["pii_leak_detected"]
    assert case["safety_passed"] is False
    assert case["judge_reason"] == "Partly supported."


def test_deep_judge_correctness_is_the_faithfulness_fallback() -> None:
    case = _benchmark_case({"id": "c3", "deep_judge": {"correctness": 3.0}})
    assert case["faithfulness"] == 3.0
    blank = _benchmark_case({"id": "c4", "answer": ""})
    assert blank["answer"] is None
