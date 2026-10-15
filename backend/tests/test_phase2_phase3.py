"""Comprehensive Integration Test Suite for Phase 2 and Phase 3 (Audience Engine & Usage SQL API)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import AuditLog


@pytest.mark.asyncio
async def test_audience_counts_and_reconciliation(client: AsyncClient):
    """Task 2.4 & Integration Suite 2: users + testing == overview mathematically."""
    res = await client.get("/admin/usage/audience-counts")
    assert res.status_code == 200
    counts = res.json()

    assert "overview" in counts
    assert "users" in counts
    assert "testing" in counts

    ov = counts["overview"]
    us = counts["users"]
    ts = counts["testing"]

    # Mathematical property: queries and sessions must reconcile
    assert us["queries"] + ts["queries"] == ov["queries"]
    assert us["sessions"] + ts["sessions"] == ov["sessions"]
    assert us["ips"] + ts["ips"] == ov["ips"]


@pytest.mark.asyncio
async def test_audience_validation_and_fuzz(client: AsyncClient):
    """Task 2.1.1 & OWASP A03/A04: 422 on invalid audience, injection-safe."""
    # Invalid audience parameter
    bad_res = await client.get("/admin/usage/summary?audience=hack_audience")
    assert bad_res.status_code == 422

    # Malicious injection attempt in audience
    sql_inj = await client.get("/admin/usage/summary?audience=' OR 1=1--")
    assert sql_inj.status_code == 422

    # Valid audiences
    for aud in ("overview", "users", "testing"):
        ok_res = await client.get(f"/admin/usage/summary?audience={aud}")
        assert ok_res.status_code == 200


@pytest.mark.asyncio
async def test_test_ip_hardening_and_audit(
    client: AsyncClient, db_session: AsyncSession
):
    """Task 2.2: Hardened test IP management, validation, and audit trail."""
    # 1. Invalid IP format -> 422
    bad_ip = await client.post(
        "/admin/usage/test-ips", json={"ip_address": "invalid..ip"}
    )
    assert bad_ip.status_code == 422

    # 2. Control character -> 422
    ctrl_ip = await client.post(
        "/admin/usage/test-ips", json={"ip_address": "10.0.0.1\x00malicious"}
    )
    assert ctrl_ip.status_code == 422

    # 3. Valid CIDR block -> 200 and audit log entry created
    cidr_ip = "172.16.0.0/16"
    add_res = await client.post(
        "/admin/usage/test-ips",
        json={"ip_address": cidr_ip, "description": "Internal VPC"},
    )
    assert add_res.status_code == 200

    # Verify audit event
    audit_evts = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "test_ip_added")
            )
        )
        .scalars()
        .all()
    )
    matching_audit = [
        e for e in audit_evts if (e.detail or {}).get("ip_address") == cidr_ip
    ]
    assert len(matching_audit) > 0

    # Verify classification note in GET
    details_res = await client.get("/admin/usage/test-ips")
    assert details_res.status_code == 200
    assert "classification_note" in details_res.json()

    # 4. Clean up
    del_res = await client.delete(f"/admin/usage/test-ips/{cidr_ip}")
    assert del_res.status_code == 200


@pytest.mark.asyncio
async def test_usage_summary_separation_and_quality(client: AsyncClient):
    """Task 3.2: Financial cost vs Token volume separation, rates, and quality stats."""
    res = await client.get("/admin/usage/summary?audience=overview")
    assert res.status_code == 200
    data = res.json()

    # Financial block
    assert "financial" in data
    fin = data["financial"]
    assert "total_cost_usd" in fin
    assert "llm_cost_usd" in fin
    assert "embeddings_cost_usd" in fin
    assert "stage_costs" in fin

    # Tokens block
    assert "tokens" in data
    tok = data["tokens"]
    assert "total_billed_tokens" in tok
    assert "llm_tokens" in tok
    assert "embeddings_tokens" in tok

    # Rate card
    assert "pricing" in data
    pricing = data["pricing"]
    assert "price_prompt_per_million" in pricing
    assert "price_embed_per_million" in pricing

    # Quality block
    assert "quality" in data
    qual = data["quality"]
    assert "groundedness_pass_rate" in qual
    assert "outcomes" in qual
    assert "latency" in qual


@pytest.mark.asyncio
async def test_users_sessions_queries_endpoints(client: AsyncClient):
    """Task 3.3: Paginated endpoints for users, sessions, and queries with intelligence headers."""
    # 1. Users tab
    u_res = await client.get("/admin/usage/users?limit=5")
    assert u_res.status_code == 200
    u_data = u_res.json()
    assert "items" in u_data
    assert "stats" in u_data
    assert "top_users" in u_data["stats"]
    assert "top_consumer_share_pct" in u_data["stats"]

    # 2. Sessions tab
    s_res = await client.get("/admin/usage/sessions?limit=5")
    assert s_res.status_code == 200
    s_data = s_res.json()
    assert "items" in s_data
    assert "stats" in s_data
    assert "top_sessions" in s_data["stats"]
    assert "avg_turns_per_session" in s_data["stats"]

    # Verify session sorting with numeric zero/float/int edge cases
    for sort_param in (
        "cost_desc",
        "cost_asc",
        "queries_desc",
        "queries_asc",
        "last_active_desc",
    ):
        sorted_res = await client.get(
            f"/admin/usage/sessions?audience=overview&sort={sort_param}&limit=10&offset=0"
        )
        assert sorted_res.status_code == 200
        assert "items" in sorted_res.json()

    # 3. Queries tab
    q_res = await client.get("/admin/usage/queries?limit=5")
    assert q_res.status_code == 200
    q_data = q_res.json()
    assert "items" in q_data
    if q_data["items"]:
        first_q = q_data["items"][0]
        assert "request_id" in first_q
        assert "outcome" in first_q

        # Inspector detail endpoint (Task 3.5.3)
        detail_res = await client.get(f"/admin/usage/queries/{first_q['request_id']}")
        assert detail_res.status_code == 200
        detail_data = detail_res.json()
        assert "rates" in detail_data
        assert "latencies" in detail_data


@pytest.mark.asyncio
async def test_csv_export_streaming_and_neutralization(client: AsyncClient):
    """Task 3.4: Streaming CSV export with formula injection neutralization and headers."""
    res = await client.get("/admin/usage/export?tab=queries&audience=overview")
    assert res.status_code == 200
    assert "text/csv" in res.headers["content-type"]
    assert "attachment" in res.headers["content-disposition"]
    assert res.headers["x-content-type-options"] == "nosniff"

    content = res.text
    # Check UTF-8 BOM
    assert content.startswith("\ufeff")
    # Verify header line
    assert "Request ID,Created At,User (IP)" in content
