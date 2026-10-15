"""Tests for test IP separation and zero-cost conversational/guardrail fixes."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sessions import drain_pending_writes
from app.governance.test_ips import (
    is_test_ip,
)
from app.schemas.query import SourceChunk


def _make_source(content: str = "Security doc", score: float = 0.9):
    return SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        content=content,
        score=score,
        metadata={"source_name": "security.md"},
        embedding=[0.05] * 1536,
    )


@pytest.mark.asyncio
async def test_ip_detection_logic():
    """Verify built-in loopbacks and custom CIDR matching."""
    effective = {"127.0.0.1", "::1", "localhost", "10.0.0.0/24", "192.168.1.50"}

    # Builtins and loopbacks
    assert is_test_ip("127.0.0.1", effective) is True
    assert is_test_ip("127.0.0.99", effective) is True
    assert is_test_ip("localhost", effective) is True
    assert is_test_ip("::1", effective) is True
    assert is_test_ip("client-127.0.0.1", effective) is True

    # Custom additions
    assert is_test_ip("192.168.1.50", effective) is True
    assert is_test_ip("10.0.0.42", effective) is True

    # Real external user IPs
    assert is_test_ip("203.0.113.195", effective) is False
    assert is_test_ip("8.8.8.8", effective) is False


@pytest.mark.asyncio
async def test_test_ip_crud_endpoints(client: AsyncClient, db_session: AsyncSession):
    """Verify GET, POST, DELETE /admin/usage/test-ips API."""
    # 1. GET initial
    res = await client.get("/admin/usage/test-ips")
    assert res.status_code == 200
    data = res.json()
    assert "default_ips" in data
    assert "custom_ips" in data
    assert "effective_ips" in data
    assert "127.0.0.1" in data["default_ips"]

    # 2. POST add new test IP
    add_res = await client.post(
        "/admin/usage/test-ips",
        json={"ip_address": "198.51.100.99", "description": "QA Runner IP"},
    )
    assert add_res.status_code == 200
    added = add_res.json()
    assert added["ip_address"] == "198.51.100.99"
    assert added["description"] == "QA Runner IP"

    # 3. GET verify added
    res2 = await client.get("/admin/usage/test-ips")
    assert res2.status_code == 200
    effective2 = res2.json()["effective_ips"]
    assert "198.51.100.99" in effective2

    # 4. DELETE test IP
    del_res = await client.delete("/admin/usage/test-ips/198.51.100.99")
    assert del_res.status_code == 200
    assert del_res.json()["deleted"] is True

    # 5. GET verify removed
    res3 = await client.get("/admin/usage/test-ips")
    assert "198.51.100.99" not in res3.json()["effective_ips"]


@pytest.mark.asyncio
async def test_conversational_and_guardrail_replies_zero_llm_cost(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch,
    fake_embed,
):
    """Verify prefixed canned small-talk and guardrail blocked queries incur $0.00 LLM cost."""
    actor = "zero_cost_tester"
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr("app.core.sessions.AsyncSessionLocal", TestingSessionLocal)

    # 1. Conversational greeting ("hello")
    hello_resp = await client.post(
        "/query",
        json={"query": "hello", "ip_address": "203.0.113.88"},
        headers={"X-Client-Id": actor},
    )
    assert hello_resp.status_code == 200
    hello_data = hello_resp.json()
    assert hello_data["outcome"] == "conversational"
    assert hello_data["cost_usd"] == 0.0
    assert hello_data["prompt_tokens"] is None or hello_data["prompt_tokens"] == 0
    assert (
        hello_data["completion_tokens"] is None or hello_data["completion_tokens"] == 0
    )

    # 2. Guardrail unclear input ("a")
    guard_resp = await client.post(
        "/query",
        json={"query": "a", "ip_address": "203.0.113.88"},
        headers={"X-Client-Id": actor},
    )
    assert guard_resp.status_code == 200
    guard_data = guard_resp.json()
    assert guard_data["grounded"] is False
    assert guard_data["cost_usd"] == 0.0
    assert guard_data["prompt_tokens"] is None or guard_data["prompt_tokens"] == 0

    await drain_pending_writes(actor)

    # 3. Check cost breakdown
    breakdown_res = await client.get("/admin/usage/cost-breakdown")
    assert breakdown_res.status_code == 200
    b_data = breakdown_res.json()

    # Find the queries in the report
    hello_q = next((q for q in b_data["queries"] if q["query"] == "hello"), None)
    assert hello_q is not None
    assert hello_q["cost_usd"] == 0.0
    assert hello_q["input_tokens"] == 0
    assert hello_q["output_tokens"] == 0
    assert hello_q["outcome"] == "conversational"

    unclear_q = next((q for q in b_data["queries"] if q["query"] == "a"), None)
    assert unclear_q is not None
    assert unclear_q["cost_usd"] == 0.0
    assert unclear_q["input_tokens"] == 0
    assert unclear_q["output_tokens"] == 0
    assert unclear_q["outcome"] == "blocked"
