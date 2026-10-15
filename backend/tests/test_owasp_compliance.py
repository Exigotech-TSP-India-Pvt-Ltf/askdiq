"""OWASP Top 10 Security Audit & Penetration Benchmark Suite (Task 8.3)."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.serialization import sanitize_csv_cell
from app.governance.auth import DEFAULT_ACTOR, sanitize_client_id
from main import app


@pytest.mark.asyncio
async def test_owasp_a01_unauthenticated_access_blocked():
    """A01: Broken Access Control - Unauthenticated calls to admin routes return 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as unauth_client:
        admin_routes = [
            "/admin/usage/summary",
            "/admin/usage/users",
            "/admin/usage/sessions",
            "/admin/usage/queries",
            "/admin/users",
            "/admin/conversations",
        ]
        for route in admin_routes:
            # Call without X-API-Key header
            res = await unauth_client.get(route)
            assert res.status_code in (401, 403), (
                f"Route {route} was accessible unauthenticated!"
            )


@pytest.mark.asyncio
async def test_owasp_a03_sql_injection_rejection(client: AsyncClient):
    """A03: Injection - SQL injection payloads in sort, filter, and audience are rejected (HTTP 422)."""
    sql_payloads = [
        "' OR '1'='1",
        "'; DROP TABLE chat_messages; --",
        "1 UNION SELECT null, null, null--",
        "admin'--",
        "benchmark(10000000,MD5(1))",
    ]

    for payload in sql_payloads:
        # Sort parameter injection
        res_sort = await client.get(f"/admin/usage/users?sort={payload}")
        assert res_sort.status_code == 422, (
            f"SQL injection in sort not rejected: {payload}"
        )

        # Audience parameter injection
        res_aud = await client.get(f"/admin/usage/summary?audience={payload}")
        assert res_aud.status_code == 422, (
            f"SQL injection in audience not rejected: {payload}"
        )


@pytest.mark.asyncio
async def test_owasp_a04_rate_limiting_and_resource_caps(client: AsyncClient):
    """A04: Resource Abuse - Pagination limit and offset caps strictly enforced."""
    # Limit cap > 200 must fail with 422
    res_limit = await client.get("/admin/usage/queries?limit=201")
    assert res_limit.status_code == 422, "Limit > 200 was not rejected with 422"

    # Offset cap > 10,000 must fail with 422
    res_offset = await client.get("/admin/usage/queries?offset=10001")
    assert res_offset.status_code == 422, "Offset > 10,000 was not rejected with 422"


@pytest.mark.asyncio
async def test_owasp_a07_malformed_tokens_rejected():
    """A07: Identification and Authentication Failures - Malformed or tampered keys rejected."""
    transport = ASGITransport(app=app)
    tampered_headers = [
        {"X-API-Key": "invalid_random_string_12345"},
        {"X-API-Key": "Bearer null"},
        {"X-API-Key": "Bearer ../../../etc/passwd"},
        {"X-API-Key": ""},
    ]

    for headers in tampered_headers:
        async with AsyncClient(
            transport=transport, base_url="http://test", headers=headers
        ) as bad_client:
            res = await bad_client.get("/admin/usage/summary")
            assert res.status_code in (401, 403), (
                f"Tampered key {headers} was not rejected!"
            )


def test_owasp_a08_csv_formula_injection_defense():
    """A08: Software and Data Integrity Failures - CSV formula payloads prepended with '."""
    malicious_cells = [
        "=cmd|' /C calc'!A0",
        "=1+1",
        "+44123456789",
        "-SUM(A1:A10)",
        "@SUM(1, 2)",
        '\tHYPERLINK("http://evil.com")',
        '\rDDE("server","calc","test")',
    ]

    for cell in malicious_cells:
        sanitized = sanitize_csv_cell(cell)
        assert sanitized.startswith("'"), (
            f"CSV payload not neutralized: {cell} -> {sanitized}"
        )

    # Benign text and numbers untouched
    assert sanitize_csv_cell("Normal Text") == "Normal Text"
    assert sanitize_csv_cell("192.168.1.1") == "192.168.1.1"
    assert sanitize_csv_cell(12345) == "12345"


def test_owasp_xss_script_injection_defense():
    """XSS: Cross-Site Scripting - Malicious script tags in client_id and inputs sanitized."""
    xss_payloads = [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "javascript:alert(document.cookie)",
        "\"><script>alert('xss')</script>",
    ]

    for payload in xss_payloads:
        # sanitize_client_id must neutralize dangerous input to DEFAULT_ACTOR
        assert sanitize_client_id(payload) == DEFAULT_ACTOR
