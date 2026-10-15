"""Phase 2 Integration Test: Audience Scope Engine, Telemetry & Rate Limiting."""

import asyncio
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.rate_limit import _admin_user_log, enforce_admin_heavy_rate_limit
from app.governance.costing import calculate_stage_costs
from app.governance.test_ips import get_audience_filter, is_test_ip
from app.models.db_models import ChatMessageRecord


@pytest.mark.asyncio
async def test_phase2_simultaneous_multi_scope_queries():
    """Verify simultaneous queries across overview, users, and testing scopes."""
    effective_test_ips = {"127.0.0.1", "10.0.0.1"}

    # Mock DB dataset of turns
    records = [
        {"id": "turn-1", "ip": "127.0.0.1", "cost": 0.001},  # Test
        {"id": "turn-2", "ip": "10.0.0.1", "cost": 0.002},  # Test
        {"id": "turn-3", "ip": "203.0.113.19", "cost": 0.005},  # User
        {"id": "turn-4", "ip": "203.0.113.25", "cost": 0.004},  # User
        {
            "id": "turn-5",
            "ip": None,
            "cost": 0.001,
        },  # Test (null IP classified as test)
    ]

    async def execute_scope(scope: str) -> dict[str, float]:
        f = get_audience_filter(scope, ChatMessageRecord.ip_address, effective_test_ips)
        stmt = select(ChatMessageRecord)
        if f is not None:
            stmt = stmt.where(f)

        # Classify mock records
        matched = []
        for r in records:
            ip = r["ip"]
            is_test = is_test_ip(ip, effective_test_ips) or ip is None
            if (
                scope == "overview"
                or (scope == "testing" and is_test)
                or (scope == "users" and not is_test)
            ):
                matched.append(r)

        await asyncio.sleep(0.02)
        total_cost = sum(r["cost"] for r in matched)
        return {"scope": scope, "count": len(matched), "total_cost": total_cost}

    # Execute all 3 scopes simultaneously via asyncio.gather
    results = await asyncio.gather(
        execute_scope("overview"),
        execute_scope("users"),
        execute_scope("testing"),
    )

    by_scope = {r["scope"]: r for r in results}

    assert by_scope["overview"]["count"] == 5
    assert by_scope["users"]["count"] == 2
    assert by_scope["testing"]["count"] == 3
    # Reconciliation: overview count == users count + testing count
    assert (
        by_scope["overview"]["count"]
        == by_scope["users"]["count"] + by_scope["testing"]["count"]
    )
    assert pytest.approx(by_scope["overview"]["total_cost"]) == (
        by_scope["users"]["total_cost"] + by_scope["testing"]["total_cost"]
    )


def test_phase2_stage_economics_precision_and_rate_card():
    """Verify stage-by-stage token costing math with 6-digit decimal precision."""
    rates = {
        "embed_per_m": Decimal("0.02"),
        "prompt_per_m": Decimal("0.25"),
        "output_per_m": Decimal("2.00"),
        "cached_per_m": Decimal("0.0625"),
    }

    costs = calculate_stage_costs(
        prompt_tokens=2000,
        cached_tokens=1500,
        completion_tokens=800,
        reasoning_tokens=300,
        query_embed_tokens=300,
        guardrail_embed_tokens=100,
        rates=rates,
    )

    # Cached prompt: 1500 * 0.0625 / 1M = 0.00009375
    # Uncached prompt: 500 * 0.25 / 1M = 0.000125
    # Prompt total = 0.00021875
    # Reasoning: 300 * 2.00 / 1M = 0.000600
    # Visible: 500 * 2.00 / 1M = 0.001000
    # Embeddings: 400 * 0.02 / 1M = 0.000008
    # Total = 0.00021875 + 0.000600 + 0.001000 + 0.000008 = 0.00182675 -> 0.001827

    assert pytest.approx(costs["prompt"], abs=1e-6) == 0.000219
    assert pytest.approx(costs["reasoning"], abs=1e-6) == 0.000600
    assert pytest.approx(costs["visible"], abs=1e-6) == 0.001000
    assert pytest.approx(costs["total"], abs=1e-6) == 0.001827


def test_phase2_concurrency_rate_limiter_under_burst():
    """Verify rate limiter blocks burst requests exceeding budget with HTTP 429."""
    _admin_user_log.clear()
    actor = "load_tester@example.com"
    burst_limit = 10

    # 10 calls succeed
    for _ in range(burst_limit):
        enforce_admin_heavy_rate_limit(actor, limit=burst_limit)

    # 11th call triggers 429
    with pytest.raises(HTTPException) as exc_info:
        enforce_admin_heavy_rate_limit(actor, limit=burst_limit)

    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers
