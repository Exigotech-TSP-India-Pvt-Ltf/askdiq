"""Comprehensive Security Review and Caching/Concurrency Test Suite (Phase 5)."""

import asyncio

import pytest
from httpx import AsyncClient

from app.api.routes.admin_usage import clear_usage_caches


@pytest.mark.asyncio
async def test_summary_cache_and_single_flight(client: AsyncClient):
    """Task 5.1.1: Short-TTL cache returns cached result and as_of timestamp."""
    clear_usage_caches()

    res1 = await client.get("/admin/usage/summary?audience=overview")
    assert res1.status_code == 200
    data1 = res1.json()
    assert "as_of" in data1

    # Second immediate call should hit cache and return identical as_of
    res2 = await client.get("/admin/usage/summary?audience=overview")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data1["as_of"] == data2["as_of"]

    # Concurrent identical requests test single-flight lock
    tasks = [client.get("/admin/usage/summary?audience=overview") for _ in range(5)]
    results = await asyncio.gather(*tasks)
    for r in results:
        assert r.status_code == 200
        assert r.json()["as_of"] == data1["as_of"]


@pytest.mark.asyncio
async def test_owasp_injection_fuzzing(client: AsyncClient):
    """Task 5.3.1: Fuzzing suite for q, sort, ip, audience, tab, session_id (OWASP A03)."""
    malicious_payloads = [
        "'; DROP TABLE chat_messages; --",
        "' OR 1=1 --",
        "<script>alert(1)</script>",
        "../../etc/passwd",
        "%00",
        "%27%20OR%201=1",
        "\\x00\\x1f\\x7f",
        "A" * 1000,
    ]

    for payload in malicious_payloads:
        # Fuzz q parameter across lists: must return 200 (safe empty) or 422, never 500
        for ep in (
            "/admin/usage/queries",
            "/admin/usage/sessions",
            "/admin/usage/users",
        ):
            res = await client.get(f"{ep}?q={payload}")
            assert res.status_code in (200, 422), (
                f"Endpoint {ep} vulnerable to payload: {payload}"
            )

        # Fuzz sort parameter: must return 422 if invalid, never 500
        sort_res = await client.get(f"/admin/usage/users?sort={payload}")
        assert sort_res.status_code == 422

        # Fuzz audience parameter: must return 422 if invalid, never 500
        aud_res = await client.get(f"/admin/usage/summary?audience={payload}")
        assert aud_res.status_code == 422


@pytest.mark.asyncio
async def test_resource_abuse_caps(client: AsyncClient):
    """Task 5.3.2: Enforce limit, offset, and date range limits (OWASP A04)."""
    # 1. Limit > 200 -> 422
    over_limit = await client.get("/admin/usage/queries?limit=999999")
    assert over_limit.status_code == 422

    # 2. Offset > 10,000 -> 422
    over_offset = await client.get("/admin/usage/queries?offset=50000")
    assert over_offset.status_code == 422

    # 3. Date range > 400 days -> 422
    wide_date = await client.get(
        "/admin/usage/summary?from_date=2020-01-01T00:00:00Z&to_date=2025-01-01T00:00:00Z"
    )
    assert wide_date.status_code == 422


@pytest.mark.asyncio
async def test_data_exposure_prevention(client: AsyncClient):
    """Task 5.3.3: Verify sensitive data like user_email is never exposed."""
    endpoints = [
        "/admin/usage/summary?audience=overview",
        "/admin/usage/users?audience=overview",
        "/admin/usage/sessions?audience=overview",
        "/admin/usage/queries?audience=overview",
        "/admin/users?audience=overview",
        "/admin/conversations?audience=overview",
    ]

    for ep in endpoints:
        res = await client.get(ep)
        assert res.status_code == 200
        body_text = res.text.lower()
        assert "user_email" not in body_text, f"user_email exposed in {ep}"
        assert "password" not in body_text, f"password exposed in {ep}"
        assert "secret_key" not in body_text, f"secret exposed in {ep}"
