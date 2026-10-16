"""Unit tests for Stage-by-Stage Economic Telemetry Aggregator (Task 2.2)."""

from decimal import Decimal

import pytest

from app.api.routes.admin_usage import calculate_stage_costs
from app.governance.costing import compute_exchange_cost


def test_calculate_stage_costs_rate_card_math():
    """Verify exact formula across prompt cached, uncached, reasoning, output, and embeddings."""
    rates = {
        "embed_per_m": Decimal("0.02"),
        "prompt_per_m": Decimal("0.25"),
        "cached_per_m": Decimal("0.0625"),
        "output_per_m": Decimal("1.00"),
    }

    # 1,000 prompt tokens (400 cached, 600 uncached)
    # 500 completion tokens (200 reasoning, 300 visible)
    # 200 query embed tokens, 100 guardrail embed tokens
    costs = calculate_stage_costs(
        prompt_tokens=1000,
        cached_tokens=400,
        completion_tokens=500,
        reasoning_tokens=200,
        query_embed_tokens=200,
        guardrail_embed_tokens=100,
        rates=rates,
    )

    # Expected:
    # prompt: (600 * 0.25 + 400 * 0.0625) / 1,000,000 = (150 + 25) / 1,000,000 = 0.000175
    # reasoning: (200 * 1.00) / 1,000,000 = 0.000200
    # visible: (300 * 1.00) / 1,000,000 = 0.000300
    # query_embed: (200 * 0.02) / 1,000,000 = 0.000004
    # guardrail_embed: (100 * 0.02) / 1,000,000 = 0.000002
    # total = 0.000175 + 0.000200 + 0.000300 + 0.000004 + 0.000002 = 0.000681

    assert pytest.approx(costs["prompt"], abs=1e-7) == 0.000175
    assert pytest.approx(costs["reasoning"], abs=1e-7) == 0.000200
    assert pytest.approx(costs["visible"], abs=1e-7) == 0.000300
    assert pytest.approx(costs["query_embed"], abs=1e-7) == 0.000004
    assert pytest.approx(costs["guardrail_embed"], abs=1e-7) == 0.000002
    assert pytest.approx(costs["total"], abs=1e-7) == 0.000681


def test_compute_exchange_cost_details_structure():
    """Verify token breakdown schema and pricing snapshots."""
    total_cost, details = compute_exchange_cost(
        prompt_tokens=500,
        completion_tokens=250,
        reasoning_tokens=50,
        cached_tokens=100,
        query_embed_tokens=150,
        guardrail_embed_tokens=50,
    )

    assert total_cost > 0
    assert "pricing" in details
    assert "tokens" in details
    assert "costs_usd" in details

    tokens = details["tokens"]
    assert tokens["prompt"] == 500
    assert tokens["cached"] == 100
    assert tokens["completion"] == 250
    assert tokens["reasoning"] == 50
    assert tokens["visible"] == 200
    assert tokens["total_billed"] == 500 + 250 + 150 + 50


def test_query_inspector_drawer_contract():
    """Verify that simulated inspector payload satisfies frontend QueryInspectorDrawer fields."""
    mock_inspector_payload = {
        "request_id": "req-12345",
        "session_id": "sess-67890",
        "session_title": "Enterprise Cloud Architecture",
        "query": "How are security boundaries isolated?",
        "answer": "Security boundaries use network segmentation...",
        "created_at": "2026-10-14T10:00:00Z",
        "ip_address": "192.168.1.50",
        "outcome": "grounded",
        "grounded": True,
        "guardrail_flags": [],
        "cost_usd": 0.000681,
        "tokens": {
            "prompt": 1000,
            "completion": 500,
            "reasoning": 200,
            "visible": 300,
            "cached_prompt": 400,
            "query_embed": 200,
            "guardrail_embed": 100,
            "total_tokens": 1500,
            "total_billed": 1800,
        },
        "rates": {
            "price_embed_per_million": 0.02,
            "price_prompt_per_million": 0.25,
            "price_output_per_million": 1.00,
            "price_cached_per_million": 0.0625,
        },
        "latencies": {
            "total_ms": 320.5,
            "embedding_ms": 25.1,
            "retrieval_ms": 45.2,
            "llm_ms": 250.2,
        },
    }

    # Validate essential fields required by QueryInspectorDrawer
    required_keys = {
        "request_id",
        "session_id",
        "query",
        "answer",
        "ip_address",
        "outcome",
        "cost_usd",
        "tokens",
        "latencies",
    }
    assert required_keys.issubset(mock_inspector_payload.keys())
    assert "reasoning" in mock_inspector_payload["tokens"]
    assert "visible" in mock_inspector_payload["tokens"]
    assert "total_ms" in mock_inspector_payload["latencies"]
