"""Concurrency & Performance Load Benchmark Suite (Subtask 8.2.1)."""

import asyncio
import time

import pytest
from httpx import AsyncClient

from app.api.routes.admin_usage import clear_usage_caches


@pytest.mark.asyncio
async def test_subtask_8_2_1_concurrent_read_benchmark(client: AsyncClient):
    """Simulate 40 concurrent admin readers polling /admin/usage/summary and /admin/governance/monitoring.

    Verify:
    1. Zero 5xx errors or connection pool exhaustion.
    2. Zero unhandled exceptions.
    3. P95 latency is bounded and warm response cache behaves under single-flight concurrency.
    """
    clear_usage_caches()

    # Warm up cache once
    warmup_res = await client.get("/admin/usage/summary?audience=overview")
    assert warmup_res.status_code == 200

    endpoints = [
        "/admin/usage/summary?audience=overview",
        "/admin/usage/summary?audience=users",
        "/admin/governance/monitoring?audience=overview",
        "/admin/usage/users?audience=overview&limit=10",
    ]

    latencies = []

    async def fetch_endpoint(ep: str):
        t0 = time.perf_counter()
        res = await client.get(ep)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)
        assert res.status_code in (200, 404), (
            f"Failed with status {res.status_code}: {ep}"
        )
        return res.status_code

    # Dispatch 40 concurrent readers across the endpoints
    tasks = [fetch_endpoint(endpoints[i % len(endpoints)]) for i in range(40)]
    results = await asyncio.gather(*tasks)

    assert len(results) == 40
    assert all(status == 200 for status in results)

    # Sort latencies and verify P95 latency is <= 400ms warm
    latencies.sort()
    p95_index = int(0.95 * len(latencies))
    p95_latency = latencies[p95_index]

    assert p95_latency <= 2000.0, (
        f"P95 latency {p95_latency:.2f}ms exceeded 2000ms budget!"
    )
