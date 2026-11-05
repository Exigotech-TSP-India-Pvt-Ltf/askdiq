"""Unit tests and benchmark for Dynamic Audience Scope Engine (Task 2.1)."""

import ipaddress
import threading
import time
from types import SimpleNamespace

from sqlalchemy import select

from app.governance import audience
from app.governance.test_ips import (
    BUILTIN_LOOPBACKS,
    _parse_networks,
    classify_ips,
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


def _reference_is_test(ip: str, rules: set[str]) -> bool:
    """The pre-optimisation algorithm: every CIDR rule re-parsed per IP."""
    val = ip.strip().lower()
    if val.startswith("client-"):
        val = val[len("client-") :]
    if val in BUILTIN_LOOPBACKS or val.startswith("127.") or val in rules:
        return True
    for rule in rules:
        if "/" in rule:
            try:
                if ipaddress.ip_address(val) in ipaddress.ip_network(
                    rule, strict=False
                ):
                    return True
            except ValueError:
                pass
    return False


def test_classify_ips_matches_reference_and_parses_rules_once():
    """Batch classification is identical to the per-IP reference and parses
    each rule set's CIDRs once (not once per IP)."""
    rules = frozenset(
        {"10.0.0.0/24", "2001:db8::/32", "bad/cidr", "198.51.100.99", "client-x"}
    )
    raw = [
        "10.0.0.7",
        "10.0.1.7",
        " 198.51.100.99 ",
        "2001:db8::1",
        "2001:db9::1",
        "client-127.0.0.9",
        "client-x",
        "LOCALHOST",
        "not-an-ip",
        "8.8.8.8",
        "",
        None,
        *(f"203.0.{i // 256}.{i % 256}" for i in range(2000)),
    ]
    _parse_networks.cache_clear()
    test_ips, user_ips = classify_ips(raw, rules)
    assert _parse_networks.cache_info().misses == 1

    expected_test = {
        str(r).strip()
        for r in raw
        if r and _reference_is_test(str(r).strip(), set(rules))
    }
    expected_user = {str(r).strip() for r in raw if r} - expected_test
    assert test_ips == expected_test
    assert user_ips == expected_user
    assert "10.0.1.7" in user_ips and "2001:db9::1" in user_ips
    assert {"10.0.0.7", "2001:db8::1", "client-127.0.0.9", "LOCALHOST"} <= test_ips
    assert {"client-x", "not-an-ip", "8.8.8.8"} <= user_ips


async def test_get_classified_ips_classifies_off_the_event_loop(monkeypatch):
    """The per-IP loop runs in a worker thread, never on the event loop."""
    loop_thread = threading.get_ident()
    seen: list[int] = []
    real = audience.classify_ips

    def spy(raw_ips, rules):
        seen.append(threading.get_ident())
        return real(raw_ips, rules)

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return ["10.0.0.5", "8.8.8.8"]

    async def _execute(_stmt):
        return _Result()

    async def _rules(_db):
        return {"10.0.0.0/24"}

    monkeypatch.setattr(audience, "classify_ips", spy)
    monkeypatch.setattr(audience, "get_effective_test_ips", _rules)
    monkeypatch.setattr(
        audience,
        "_cached_classification",
        {"test_ips": set(), "user_ips": set(), "as_of": None},
    )
    test_ips, user_ips, _ = await audience.get_classified_ips(
        SimpleNamespace(execute=_execute)
    )
    assert test_ips == {"10.0.0.5"} and user_ips == {"8.8.8.8"}
    assert seen and seen[0] != loop_thread
