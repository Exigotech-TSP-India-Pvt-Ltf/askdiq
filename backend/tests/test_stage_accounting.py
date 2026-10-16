"""Integration tests for Phase 2: Stage-by-Stage Token & Cost Accounting Engine.

Validates:
- Pure Decimal costing math across all 5 stages (Subtask 2.4.4).
- Query stage embedding and guardrail token capture (Tasks 2.1 & 2.3).
- Reasoning tokens and cached prompt token capture (Task 2.2).
- Retry accumulation across failed/empty attempts (Subtask 2.2.4).
- Failure path cost retention (Subtask 2.3.3).
- Database persistence of stage columns & token_details snapshot (Task 2.4).
- Multi-tier Admin usage & cost breakdown API (Task 2.5).
- Single query inspector route GET /admin/usage/queries/{request_id} (Subtask 2.5.5).
"""

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sessions import drain_pending_writes
from app.governance.costing import compute_exchange_cost
from app.harness import orchestrator
from app.models.db_models import ChatMessageRecord
from app.retrieval.vector_store import VectorStore
from app.schemas.query import SourceChunk


def _make_source(
    content: str = "DeployIQ security architecture content", score: float = 0.9
):
    return SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        content=content,
        score=score,
        metadata={"source_name": "security.md"},
        embedding=[0.05] * 1536,
    )


@pytest.mark.asyncio
async def test_pure_costing_math():
    """Verify Decimal arithmetic and exact formula across 5 stages."""
    rates = {
        "embed_per_m": Decimal("0.02"),
        "prompt_per_m": Decimal("0.25"),
        "output_per_m": Decimal("2.00"),
        "cached_per_m": Decimal("0.25"),
    }
    cost, details = compute_exchange_cost(
        prompt_tokens=1000,
        completion_tokens=500,
        reasoning_tokens=100,
        cached_tokens=200,
        query_embed_tokens=100,
        guardrail_embed_tokens=50,
        pricing_snapshot=rates,
    )

    # Expected:
    # embed: 150 * 0.02 / 1e6 = 0.000003
    # prompt: 800 * 0.25 / 1e6 + 200 * 0.25 / 1e6 = 0.000250
    # output: 500 * 2.00 / 1e6 = 0.001000
    # Total = 0.001253
    assert cost == pytest.approx(0.001253, abs=1e-6)
    assert details["tokens"]["total_billed"] == 1650
    assert details["tokens"]["reasoning"] == 100
    assert details["tokens"]["visible"] == 400
    assert details["pricing"]["output_per_m"] == 2.0


@pytest.mark.asyncio
async def test_query_stage_tokens_and_cost_persisted(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch,
    fake_embed,
    fake_llm,
):
    """Test /query endpoint populates stage tokens, cost, and persists to DB."""
    actor = "stage_test_client"
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr("app.core.sessions.AsyncSessionLocal", TestingSessionLocal)

    async def fake_retrieve(*a, **k):
        return [_make_source()]

    async def fake_post_checks(*a, **k):
        return True, 0.95, []

    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    monkeypatch.setattr(orchestrator, "run_post_checks", fake_post_checks)

    fake_llm["set_response"](
        "DeployIQ implements Zero Trust architecture and Australian compliance.",
        {
            "prompt_tokens": 120,
            "completion_tokens": 80,
            "total_tokens": 200,
            "reasoning_tokens": 25,
            "visible_tokens": 55,
            "cached_tokens": 0,
        },
    )

    resp = await client.post(
        "/query",
        json={"query": "What security architecture does DeployIQ use?"},
        headers={"X-Client-Id": actor},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["prompt_tokens"] == 120
    assert data["completion_tokens"] == 80
    assert data["reasoning_tokens"] == 25
    assert data["cost_usd"] is not None
    assert data["cost_usd"] > 0
    assert data["outcome"] == "answered"
    assert "token_details" in data
    assert data["token_details"]["tokens"]["reasoning"] == 25

    # Drain background write
    await drain_pending_writes(actor)

    # Check DB row
    msg_id = uuid.UUID(data["assistant_message_id"])
    msg = await db_session.get(ChatMessageRecord, msg_id)
    assert msg is not None
    assert msg.prompt_tokens == 120
    assert msg.completion_tokens == 80
    assert msg.reasoning_tokens == 25
    assert msg.cost_usd == pytest.approx(data["cost_usd"], abs=1e-6)
    assert msg.token_details is not None
    assert msg.token_details["tokens"]["prompt"] == 120


@pytest.mark.asyncio
async def test_retry_accumulation(
    monkeypatch,
    db_session: AsyncSession,
    fake_embed,
):
    """Verify multiple LLM attempts accumulate usage instead of overwriting."""
    attempt_count = 0

    async def fake_retrieve(*_a, **_k):
        return [_make_source()]

    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)

    async def mock_call_llm(query, sources, history):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            # First attempt returns empty output but consumed reasoning tokens
            return "", {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "reasoning_tokens": 40,
                "visible_tokens": 10,
                "cached_tokens": 0,
            }
        # Second attempt succeeds
        return "DeployIQ provides continuous governance and auditability.", {
            "prompt_tokens": 100,
            "completion_tokens": 60,
            "total_tokens": 160,
            "reasoning_tokens": 30,
            "visible_tokens": 30,
            "cached_tokens": 0,
        }

    monkeypatch.setattr("app.harness.orchestrator.call_llm", mock_call_llm)

    stage_timings = {}
    response = await orchestrator.run_query(
        query="Tell me about DeployIQ governance features.",
        top_k=2,
        filters=None,
        vector_store=VectorStore(db_session),
        request_id="test-retry-req",
        stage_timings=stage_timings,
    )

    assert response.prompt_tokens == 200  # 100 + 100
    assert response.completion_tokens == 110  # 50 + 60
    assert response.total_tokens == 310  # 150 + 160
    assert response.reasoning_tokens == 70  # 40 + 30
    assert response.token_details["llm_attempts"] == 2


@pytest.mark.asyncio
async def test_failure_path_cost_retention(
    monkeypatch,
    db_session: AsyncSession,
    fake_embed,
):
    """Verify ungrounded or empty answers still carry accumulated tokens and outcome."""

    async def fake_retrieve(*_a, **_k):
        return [_make_source()]

    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)

    async def mock_call_llm_ungrounded(query, sources, history):
        return "Invented unsupported answer with $999999 cost.", {
            "prompt_tokens": 80,
            "completion_tokens": 40,
            "total_tokens": 120,
            "reasoning_tokens": 10,
            "visible_tokens": 30,
            "cached_tokens": 0,
        }

    monkeypatch.setattr("app.harness.orchestrator.call_llm", mock_call_llm_ungrounded)

    stage_timings = {}
    response = await orchestrator.run_query(
        query="What is the pricing tier for DeployIQ enterprise?",
        top_k=2,
        filters=None,
        vector_store=VectorStore(db_session),
        request_id="test-ungrounded-req",
        stage_timings=stage_timings,
    )

    assert response.grounded is False
    assert response.outcome in ("ungrounded", "blocked")
    assert response.prompt_tokens == 80
    assert response.completion_tokens == 40
    assert response.reasoning_tokens == 10
    assert response.token_details is not None


@pytest.mark.asyncio
async def test_admin_cost_breakdown_multi_tier(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch,
    fake_embed,
    fake_llm,
):
    """Verify /admin/usage/cost-breakdown returns all 4 levels with stage breakdowns."""
    actor = "admin_test_user"
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr("app.core.sessions.AsyncSessionLocal", TestingSessionLocal)

    async def fake_retrieve(*a, **k):
        return [_make_source()]

    async def fake_post_checks(*a, **k):
        return True, 0.95, []

    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    monkeypatch.setattr(orchestrator, "run_post_checks", fake_post_checks)

    fake_llm["set_response"](
        "DeployIQ architecture documentation answer.",
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "reasoning_tokens": 15,
            "visible_tokens": 35,
            "cached_tokens": 0,
        },
    )
    query_resp = await client.post(
        "/query",
        json={
            "query": "Explain DeployIQ platform architecture.",
            "ip_address": "203.0.113.42",
        },
        headers={"X-Client-Id": actor},
    )
    assert query_resp.status_code == 200
    q_data = query_resp.json()
    req_id = q_data["request_id"]

    # Drain background write
    await drain_pending_writes(actor)

    # Call cost breakdown endpoint
    res = await client.get("/admin/usage/cost-breakdown")
    assert res.status_code == 200
    data = res.json()

    # Check Level 1: overall
    assert "overall" in data
    overall = data["overall"]
    assert "total_queries" in overall
    assert "total_cost_usd" in overall
    assert "stages_tokens" in overall
    assert "stages_cost_usd" in overall
    assert "platform_overhead" in overall

    # Check Level 2: by_ip and by_test_ip separation
    assert "by_ip" in data
    assert "by_test_ip" in data
    assert len(data["by_ip"]) >= 1
    ip_row = next((r for r in data["by_ip"] if r["ip_address"] == "203.0.113.42"), None)
    assert ip_row is not None
    assert ip_row["ip_address"] == "203.0.113.42"
    assert "sessions" in ip_row
    assert "query_embed_tokens" in ip_row
    assert "prompt_tokens" in ip_row
    assert "reasoning_tokens" in ip_row

    # Check Level 3: sessions
    assert "sessions" in data
    assert len(data["sessions"]) >= 1

    # Check Level 4: queries
    assert "queries" in data
    assert len(data["queries"]) >= 1
    q_row = data["queries"][0]
    assert "cost_usd" in q_row
    assert "prompt_tokens" in q_row or "input_tokens" in q_row

    # Test single query inspector (Subtask 2.5.5)
    inspector_res = await client.get(f"/admin/usage/queries/{req_id}")
    assert inspector_res.status_code == 200
    detail = inspector_res.json()
    assert detail["request_id"] == req_id
    assert "tokens" in detail
    assert detail["tokens"]["prompt"] == 100
    assert detail["tokens"]["reasoning"] == 15
    assert detail["answer"] != ""

    # Test single query inspector 404
    not_found_res = await client.get("/admin/usage/queries/non-existent-id-999")
    assert not_found_res.status_code == 404
