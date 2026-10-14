"""Unit tests and benchmark for Dynamic Audience Scope Engine (Task 2.1)."""

import time
from sqlalchemy import select

from app.governance.test_ips import (
    get_audience_filter,
    invalidate_test_ips_cache,
    is_test_ip,
)
from app.models.db_models import ChatMessageRecord


def test_get_audience_filter_clauses():
    """Verify audience filter generation for overview, users, and testing."""
    effective_ips = {"127.0.0.1", "192.168.1.100"}

    # Overview: no filter (returns None)
    f_overview = get_audience_filter(
        "overview", ChatMessageRecord.ip_address, effective_ips
    )
    assert f_overview is None

    # Users: excludes test IPs and is not None
    f_users = get_audience_filter("users", ChatMessageRecord.ip_address, effective_ips)
    assert f_users is not None
    stmt_users = select(ChatMessageRecord).where(f_users)
    sql_users = str(stmt_users)
    assert "NOT IN" in sql_users
    assert "IS NOT NULL" in sql_users

    # Testing: matches test IPs or is None
    f_testing = get_audience_filter(
        "testing", ChatMessageRecord.ip_address, effective_ips
    )
    assert f_testing is not None
    stmt_testing = select(ChatMessageRecord).where(f_testing)
    sql_testing = str(stmt_testing)
    assert "IN" in sql_testing or "IS NULL" in sql_testing


def test_is_test_ip_fast_path_and_latency_benchmark():
    """Benchmark IP classification latency to verify <= 0.05ms per check requirement."""
    effective_ips = {
        "127.0.0.1",
        "::1",
        "localhost",
        "10.0.0.0/24",
        "192.168.1.0/24",
        "198.51.100.99",
    }

    test_samples = [
        "127.0.0.1",
        "127.0.0.5",
        "10.0.0.45",
        "192.168.1.10",
        "203.0.113.1",  # production
        "8.8.8.8",  # production
        "198.51.100.99",  # test
        None,
    ]

    # Warmup
    for ip in test_samples:
        is_test_ip(ip, effective_ips)

    # 10,000 iterations
    iterations = 10000
    t0 = time.perf_counter()
    for _ in range(iterations):
        for ip in test_samples:
            is_test_ip(ip, effective_ips)
    elapsed = time.perf_counter() - t0

    total_checks = iterations * len(test_samples)
    avg_latency_ms = (elapsed / total_checks) * 1000.0

    # Subtask 2.1.2 DoD: <= 0.05ms per classification check
    assert avg_latency_ms <= 0.05, (
        f"Average latency {avg_latency_ms:.5f}ms exceeded 0.05ms budget"
    )


def test_invalidate_test_ips_cache():
    """Verify in-memory cache invalidation."""
    invalidate_test_ips_cache()
    # Cache is empty and clean
    assert is_test_ip("127.0.0.1") is True
    assert is_test_ip("1.2.3.4") is False
