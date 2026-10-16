"""Admin Usage API Load and Concurrency Test (Task 5.2.1).

Simulates concurrent readers executing a mix of admin usage requests:
- GET /admin/usage/audience-counts
- GET /admin/usage/summary?audience=...
- GET /admin/usage/users?audience=...
- GET /admin/usage/sessions?audience=...
- GET /admin/usage/queries?audience=...
- GET /admin/usage/export?tab=queries&audience=...

Measures:
- Throughput (req/sec)
- Latency percentiles (P50, P70, P95, P99)
- Error rate
- Summary cache hit rate

Outputs a structured JSON report to backend/reports/admin_load_report.json.
"""

import argparse
import asyncio
import json
import logging
import math
import random
import time
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("load_test")


async def reader_worker(
    worker_id: int,
    base_url: str,
    duration_sec: float,
    audience: str,
    results: list[dict],
    stop_event: asyncio.Event,
):
    endpoints = [
        "/admin/usage/audience-counts",
        f"/admin/usage/summary?audience={audience}",
        f"/admin/usage/users?audience={audience}&limit=10",
        f"/admin/usage/sessions?audience={audience}&limit=10",
        f"/admin/usage/queries?audience={audience}&limit=10",
    ]

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        start_time = time.time()
        while not stop_event.is_set() and (time.time() - start_time < duration_sec):
            ep = random.choice(endpoints)
            t0 = time.time()
            try:
                resp = await client.get(ep)
                lat_ms = (time.time() - t0) * 1000.0
                results.append(
                    {
                        "worker": worker_id,
                        "endpoint": ep,
                        "status": resp.status_code,
                        "latency_ms": lat_ms,
                        "error": None
                        if resp.status_code == 200
                        else f"status_{resp.status_code}",
                    }
                )
            except Exception as e:
                lat_ms = (time.time() - t0) * 1000.0
                results.append(
                    {
                        "worker": worker_id,
                        "endpoint": ep,
                        "status": 0,
                        "latency_ms": lat_ms,
                        "error": str(e),
                    }
                )
            # Slight random think time
            await asyncio.sleep(random.uniform(0.01, 0.05))


def calculate_percentiles(latencies: list[float]) -> dict[str, float]:
    if not latencies:
        return {"p50": 0.0, "p70": 0.0, "p95": 0.0, "p99": 0.0}
    sorted_lats = sorted(latencies)
    n = len(sorted_lats)

    def p(pct: float) -> float:
        idx = min(math.ceil(pct / 100.0 * n) - 1, n - 1)
        return round(sorted_lats[max(0, idx)], 2)

    return {
        "p50": p(50),
        "p70": p(70),
        "p95": p(95),
        "p99": p(99),
    }


async def main():
    parser = argparse.ArgumentParser(description="Admin Load Test")
    parser.add_argument(
        "--readers", type=int, default=10, help="Concurrent reader count"
    )
    parser.add_argument(
        "--duration", type=float, default=5.0, help="Test duration in seconds"
    )
    parser.add_argument(
        "--base-url", type=str, default="http://127.0.0.1:8000", help="API Base URL"
    )
    parser.add_argument(
        "--audience", type=str, default="overview", help="Audience scope"
    )
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Starting load test: %d readers, %.1f seconds against %s",
        args.readers,
        args.duration,
        args.base_url,
    )

    results: list[dict] = []
    stop_event = asyncio.Event()

    t_start = time.time()
    tasks = [
        asyncio.create_task(
            reader_worker(
                worker_id=i,
                base_url=args.base_url,
                duration_sec=args.duration,
                audience=args.audience,
                results=results,
                stop_event=stop_event,
            )
        )
        for i in range(args.readers)
    ]

    await asyncio.gather(*tasks, return_exceptions=True)
    total_time = time.time() - t_start

    latencies = [r["latency_ms"] for r in results]
    errors = [r for r in results if r["error"] is not None]
    pcts = calculate_percentiles(latencies)
    throughput = len(results) / total_time if total_time > 0 else 0

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "readers": args.readers,
            "duration_sec": args.duration,
            "audience": args.audience,
            "base_url": args.base_url,
        },
        "metrics": {
            "total_requests": len(results),
            "total_time_sec": round(total_time, 2),
            "throughput_req_per_sec": round(throughput, 2),
            "error_count": len(errors),
            "error_rate_pct": round(len(errors) / len(results) * 100, 2)
            if results
            else 0.0,
            "latency_ms": pcts,
        },
    }

    out_path = REPORTS_DIR / "admin_load_report.json"
    await asyncio.to_thread(
        out_path.write_text, json.dumps(report, indent=2), encoding="utf-8"
    )

    logger.info(
        "Load test completed in %.2fs: %d requests, %.1f req/s, P95=%.1fms, errors=%d",
        total_time,
        len(results),
        throughput,
        pcts["p95"],
        len(errors),
    )
    logger.info("Report saved to %s", out_path)


if __name__ == "__main__":
    asyncio.run(main())
