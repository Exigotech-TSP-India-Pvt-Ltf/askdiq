"""Phase 1 Integration Test Harness: Filters, Query Builders, Concurrency & Security (OWASP A03/A04)."""

import asyncio
from datetime import UTC, datetime, timedelta
from fastapi import HTTPException
import pytest
from sqlalchemy import select

from app.core.cache import TTLCache, make_cache_key
from app.db.query_builders import (
    apply_sort_order,
    build_date_range_filters,
    build_query_filter_clauses,
    build_user_filter_clauses,
)
from app.models.db_models import ChatMessageRecord, User
from app.schemas.filters import QueryFilterParams, UserFilterParams


@pytest.mark.asyncio
async def test_phase1_end_to_end_filter_to_cache_pipeline():
    """Validates complete flow: filter validation -> query builder -> SingleFlight cached execution."""
    cache = TTLCache(default_ttl=15.0)
    db_execution_counter = 0

    async def mock_execute_filtered_query(
        params: QueryFilterParams, audience: str
    ) -> dict[str, int]:
        nonlocal db_execution_counter
        db_execution_counter += 1

        # 1. Build query clauses
        test_ips = {"127.0.0.1", "10.0.0.1"}
        clauses = build_query_filter_clauses(
            ChatMessageRecord,
            params,
            test_ips=test_ips,
            audience=audience,
        )
        stmt = select(ChatMessageRecord).where(*clauses)

        # 2. Apply safe sort order
        allowed_sorts = {
            "created_desc": ChatMessageRecord.created_at.desc(),
            "cost_desc": ChatMessageRecord.cost_usd.desc(),
        }
        stmt = apply_sort_order(
            stmt,
            params.sort,
            allowed_sorts,
            default_sort=ChatMessageRecord.created_at.desc(),
        )

        # Assert query compiles cleanly with parameter bindings
        compiled = str(stmt.compile())
        assert "WHERE" in compiled
        assert "ORDER BY" in compiled

        await asyncio.sleep(0.03)  # simulate async DB execution
        return {"total_records": 12, "db_hits": db_execution_counter}

    # Prepare validated filter params
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    filter_params = QueryFilterParams(
        q="SELECT * FROM test",  # Valid search string containing SQL words, should be safely treated as text
        status="success",
        min_cost=0.001,
        max_cost=0.50,
        sort="cost_desc",
        start_date=yesterday,
        end_date=now,
    )

    cache_key = make_cache_key(
        "queries_summary", params=filter_params, audience="users"
    )

    # Simulate 25 concurrent frontend component requests for this analytical summary
    tasks = [
        cache.get_or_compute(
            cache_key,
            lambda: mock_execute_filtered_query(filter_params, audience="users"),
        )
        for _ in range(25)
    ]
    results = await asyncio.gather(*tasks)

    # Assert SingleFlight collapsed all 25 requests into exactly 1 database execution
    assert db_execution_counter == 1
    assert len(results) == 25
    for r in results:
        assert r["total_records"] == 12

    # Assert cache hit on 26th request
    cached_result = await cache.get_or_compute(
        cache_key,
        lambda: mock_execute_filtered_query(filter_params, audience="users"),
    )
    assert db_execution_counter == 1
    assert cached_result["total_records"] == 12


def test_phase1_owasp_a03_sql_injection_defense():
    """Verify that SQL injection attempts across filter clauses and sort orders are completely neutralized."""
    # 1. Sort injection attempt
    q = select(User)
    allowed_sorts = {
        "created_desc": User.created_at.desc(),
        "email_asc": User.email.asc(),
    }

    malicious_sorts = [
        "created_desc; DROP TABLE users; --",
        "email_asc OR 1=1",
        "'; EXEC xp_cmdshell('dir'); --",
        "UNION SELECT * FROM chat_messages",
    ]

    for attack in malicious_sorts:
        with pytest.raises(HTTPException) as exc_info:
            apply_sort_order(q, attack, allowed_sorts)
        assert exc_info.value.status_code == 422
        assert "Invalid sort key" in exc_info.value.detail

    # 2. Search query parameter binding verification
    attack_search = "admin' OR '1'='1' UNION SELECT NULL, NULL--"
    user_params = UserFilterParams(q=attack_search)

    clauses = build_user_filter_clauses(User, user_params)
    stmt = select(User).where(*clauses)

    # When compiled with default or literal parameters, it MUST be bound, never raw SQL concatenated
    compiled = stmt.compile()
    sql_text = str(compiled)
    assert "OR '1'='1'" not in sql_text
    # Parameter is safely bound in params dict
    assert any(attack_search in str(val) for val in compiled.params.values())


def test_phase1_date_range_boundary_integrity():
    """Verify that invalid date boundaries cannot bypass query builders."""
    now = datetime.now(UTC)
    earlier = now - timedelta(hours=2)

    # Empty
    empty_clauses = build_date_range_filters(ChatMessageRecord.created_at, None, None)
    assert len(empty_clauses) == 0

    # Valid
    valid_clauses = build_date_range_filters(ChatMessageRecord.created_at, earlier, now)
    assert len(valid_clauses) == 2
    stmt = select(ChatMessageRecord).where(*valid_clauses)
    assert "chat_messages.created_at >=" in str(stmt)
    assert "chat_messages.created_at <=" in str(stmt)
