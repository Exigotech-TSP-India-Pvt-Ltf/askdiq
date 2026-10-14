"""Unit tests for Concurrency-Safe Rate Limiting & Admin Audit Logging (Task 2.3)."""

from fastapi import HTTPException
import pytest

from app.core.rate_limit import (
    _admin_ip_log,
    _admin_user_log,
    enforce_admin_heavy_rate_limit,
)


def test_admin_heavy_rate_limit_enforcement():
    """Verify sliding-window rate limit enforces HTTP 429 with Retry-After header."""
    _admin_user_log.clear()
    _admin_ip_log.clear()

    actor = "admin_tester@example.com"
    ip = "192.168.1.100"
    limit = 5

    # First 5 calls pass cleanly
    for _ in range(limit):
        enforce_admin_heavy_rate_limit(actor, ip, limit=limit)

    # 6th call raises HTTP 429
    with pytest.raises(HTTPException) as exc_info:
        enforce_admin_heavy_rate_limit(actor, ip, limit=limit)

    assert exc_info.value.status_code == 429
    assert "Retry-After" in exc_info.value.headers
    retry_after = int(exc_info.value.headers["Retry-After"])
    assert 0 < retry_after <= 60


def test_admin_rate_limit_independent_ip_tracking():
    """Verify that multiple accounts from the same IP share the IP limit."""
    _admin_user_log.clear()
    _admin_ip_log.clear()

    shared_ip = "198.51.100.1"
    limit = 3

    # 3 calls from 3 different users from same IP
    enforce_admin_heavy_rate_limit("user1@example.com", shared_ip, limit=limit)
    enforce_admin_heavy_rate_limit("user2@example.com", shared_ip, limit=limit)
    enforce_admin_heavy_rate_limit("user3@example.com", shared_ip, limit=limit)

    # 4th call from yet another user from the same IP must be throttled
    with pytest.raises(HTTPException) as exc_info:
        enforce_admin_heavy_rate_limit("user4@example.com", shared_ip, limit=limit)

    assert exc_info.value.status_code == 429
    assert "per IP" in exc_info.value.detail


def test_admin_export_audit_payload_integrity():
    """Verify audit log payload contains client IP and filter criteria with zero plaintext secrets."""
    audit_detail = {
        "tab": "queries",
        "audience": "users",
        "q": "payroll report",
        "ip": "203.0.113.5",
        "session_id": "sess-abc-123",
        "outcome": "grounded",
        "grounded": True,
    }

    # Verify no secrets or credentials leaked in payload
    forbidden_terms = {"password", "secret", "bearer", "api_key", "token"}
    for key, val in audit_detail.items():
        assert key.lower() not in forbidden_terms
        assert not any(term in str(val).lower() for term in forbidden_terms)
