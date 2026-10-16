"""HTTP load and concurrency test with performance budgets (newplan 13.4 / 13.5).

Virtual users hit a running API (directly, or through the nginx load balancer)
for ``--duration`` seconds and the run is graded against the newplan 13.4
budgets. Profiles:

- ``admin``: admin console mix (dashboard, usage, feedback analytics,
  governance logs and traces, paginated lists).
- ``chat``: ``POST /query``, one ``X-Client-Id`` per virtual user. Raise the
  per-user limit for those ids via ``RATE_LIMIT_EXEMPT_ACTORS`` on the server
  (they are ``<client-prefix>-<n>``); 429s are reported but never counted as
  errors.
- ``usage``: the original Task 5.2.1 mix (``/admin/usage/*`` only).

The JSON report (default ``backend/reports/admin_load_report.json``) holds
p50/p70/p95/p99, error % and 429 count per route, plus every budget check.
Exit code: 0 when every budget holds, 1 when one is breached, 2 when no
request completed.

Examples (from backend/, venv python)::

    python scripts/run_admin_load_test.py --smoke --base-url http://127.0.0.1:8000
    python scripts/run_admin_load_test.py --profile admin --concurrency 100 --duration 600
    python scripts/run_admin_load_test.py --profile chat --concurrency 50 --duration 120
    python scripts/run_admin_load_test.py --profile usage --readers 10 --duration 5

The API key is read from ``--api-key`` or the ``API_KEY_NAME`` environment
variable (sent as ``X-API-Key``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import random
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"
DEFAULT_REPORT = REPORTS_DIR / "admin_load_report.json"

SMOKE_CONCURRENCY = 50
SMOKE_DURATION_SEC = 15.0
DEFAULT_CONCURRENCY = 10
DEFAULT_DURATION_SEC = 5.0
REQUEST_TIMEOUT_SEC = 30.0
THINK_TIME_SEC = (0.01, 0.05)
RATE_LIMITED = 429
HTTP_OK_MIN = 200
HTTP_CLIENT_ERROR_MIN = 400

logger = logging.getLogger("load_test")


class Profile(StrEnum):
    ADMIN = "admin"
    CHAT = "chat"
    USAGE = "usage"


class BudgetKind(StrEnum):
    """newplan 13.4 latency budget classes (p95, milliseconds)."""

    AGGREGATION = "aggregation"
    LIST = "list"
    NONE = "none"


# A load run mixes cold and warm caches, so aggregation GETs are held to the
# uncached budget (600 ms); the cached budget (150 ms) is a per-request target.
P95_BUDGET_MS: dict[BudgetKind, float] = {
    BudgetKind.AGGREGATION: 600.0,
    BudgetKind.LIST: 250.0,
}
# "Error rate under target load < 0.5 % (excluding deliberate 429s)".
MAX_ERROR_RATE_PCT = 0.5


@dataclass(frozen=True, slots=True)
class Route:
    name: str
    method: str
    path: str
    budget: BudgetKind
    params: dict[str, str | int] = field(default_factory=dict)


def _get(path: str, budget: BudgetKind, **params: str | int) -> Route:
    return Route(f"GET {path}", "GET", path, budget, dict(params))


def routes_for(profile: Profile, audience: str = "overview") -> tuple[Route, ...]:
    """The request mix for ``profile``; each virtual user picks uniformly."""
    agg, lst = BudgetKind.AGGREGATION, BudgetKind.LIST
    usage = (
        _get("/admin/usage/audience-counts", agg),
        _get("/admin/usage/summary", agg, audience=audience),
        _get("/admin/usage/users", lst, audience=audience, limit=10),
        _get("/admin/usage/sessions", lst, audience=audience, limit=10),
        _get("/admin/usage/queries", lst, audience=audience, limit=10),
    )
    if profile is Profile.USAGE:
        return usage
    if profile is Profile.CHAT:
        return (Route("POST /query", "POST", "/query", BudgetKind.NONE),)
    return (
        *usage,
        _get("/admin/dashboard", agg, audience=audience),
        _get("/admin/feedback/analytics", agg, audience=audience),
        _get("/admin/governance/monitoring", agg, audience=audience),
        _get("/admin/governance/logs", lst, audience=audience, limit=50),
        _get("/admin/governance/traces", lst, audience=audience, limit=50),
        _get("/admin/feedback", lst, audience=audience, limit=50),
        _get("/admin/users", lst, audience=audience, limit=50),
    )


CHAT_QUESTIONS = (
    "What is the Essential Eight?",
    "Explain Zero Trust in simple terms.",
    "How does DeployIQ help with audit readiness?",
    "What are the key benefits of multi-factor authentication?",
    "Summarise what DeployIQ says about responsible AI.",
)


@dataclass(slots=True)
class Sample:
    route: str
    status: int
    latency_ms: float
    error: str | None


# --------------------------------------------------------------------------
# Pure helpers (unit tested in tests/test_load_scripts.py)
# --------------------------------------------------------------------------


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile; 0.0 for an empty sequence."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(math.ceil(pct / 100.0 * len(ordered)) - 1, len(ordered) - 1)
    return round(ordered[max(0, idx)], 2)


def calculate_percentiles(latencies: Sequence[float]) -> dict[str, float]:
    return {f"p{p}": percentile(latencies, p) for p in (50, 70, 95, 99)}


def is_success(status: int) -> bool:
    return HTTP_OK_MIN <= status < HTTP_CLIENT_ERROR_MIN


def is_error(status: int) -> bool:
    """Transport failures (status 0) and non-2xx/3xx, except deliberate 429s."""
    return status != RATE_LIMITED and not is_success(status)


def route_stats(samples: Sequence[Sample]) -> dict[str, Any]:
    errors = sum(1 for s in samples if is_error(s.status))
    return {
        "requests": len(samples),
        "errors": errors,
        "rate_limited": sum(1 for s in samples if s.status == RATE_LIMITED),
        "error_rate_pct": round(errors / len(samples) * 100, 3) if samples else 0.0,
        "latency_ms": calculate_percentiles([s.latency_ms for s in samples]),
    }


def summarize(samples: Sequence[Sample]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (overall stats, per-route stats keyed by route name)."""
    by_route: dict[str, list[Sample]] = {}
    for s in samples:
        by_route.setdefault(s.route, []).append(s)
    per_route = {name: route_stats(rs) for name, rs in sorted(by_route.items())}
    return route_stats(samples), per_route


def evaluate_budgets(
    overall: dict[str, Any],
    per_route: dict[str, Any],
    routes: Sequence[Route],
    max_error_rate_pct: float = MAX_ERROR_RATE_PCT,
) -> list[dict[str, Any]]:
    """One p95 check per budgeted route that ran, plus the overall error rate."""
    checks: list[dict[str, Any]] = []
    for route in routes:
        budget = P95_BUDGET_MS.get(route.budget)
        stats = per_route.get(route.name)
        if budget is None or not stats or not stats["requests"]:
            continue
        actual = stats["latency_ms"]["p95"]
        checks.append(
            {
                "check": f"{route.name} p95",
                "kind": route.budget.value,
                "budget": budget,
                "actual": actual,
                "unit": "ms",
                "passed": actual < budget,
            }
        )
    actual_err = overall["error_rate_pct"]
    checks.append(
        {
            "check": "error rate (excluding 429)",
            "kind": "error_rate",
            "budget": max_error_rate_pct,
            "actual": actual_err,
            "unit": "%",
            "passed": overall["requests"] > 0 and actual_err < max_error_rate_pct,
        }
    )
    return checks


def exit_code(
    checks: Sequence[dict[str, Any]], total_requests: int, report_only: bool
) -> int:
    if total_requests == 0:
        return 2
    if report_only or all(c["passed"] for c in checks):
        return 0
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="HTTP load test graded against the newplan 13.4 budgets."
    )
    parser.add_argument(
        "--profile",
        type=Profile,
        choices=list(Profile),
        default=Profile.ADMIN,
        help="Request mix (default: admin; 'usage' is the original Task 5.2.1 mix)",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help=f"Short CI run: {SMOKE_CONCURRENCY} users for "
        f"{SMOKE_DURATION_SEC:.0f}s unless --concurrency/--duration are given",
    )
    parser.add_argument(
        "--concurrency",
        "--readers",
        dest="concurrency",
        type=int,
        default=None,
        help=f"Concurrent virtual users (default {DEFAULT_CONCURRENCY}, "
        f"{SMOKE_CONCURRENCY} with --smoke)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help=f"Test duration in seconds (default {DEFAULT_DURATION_SEC:.0f}, "
        f"{SMOKE_DURATION_SEC:.0f} with --smoke)",
    )
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000", help="API base URL"
    )
    parser.add_argument("--audience", default="overview", help="Audience scope")
    parser.add_argument(
        "--api-key", default=None, help="X-API-Key value (default: $API_KEY_NAME)"
    )
    parser.add_argument(
        "--client-prefix",
        default="loadtest",
        help="X-Client-Id prefix; virtual user n sends '<prefix>-<n>'",
    )
    parser.add_argument(
        "--max-error-rate",
        type=float,
        default=MAX_ERROR_RATE_PCT,
        help="Error-rate budget in percent (default %(default)s)",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Write the report but exit 0 even when a budget is breached",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_REPORT, help="JSON report path"
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="Random seed for the request mix"
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.concurrency is None:
        args.concurrency = SMOKE_CONCURRENCY if args.smoke else DEFAULT_CONCURRENCY
    if args.duration is None:
        args.duration = SMOKE_DURATION_SEC if args.smoke else DEFAULT_DURATION_SEC
    if args.concurrency < 1:
        parser.error("--concurrency must be >= 1")
    if args.duration <= 0:
        parser.error("--duration must be > 0")
    if args.api_key is None:
        args.api_key = os.environ.get("API_KEY_NAME", "")
    return args


# --------------------------------------------------------------------------
# Network part
# --------------------------------------------------------------------------


async def _send(
    client: httpx.AsyncClient,
    route: Route,
    headers: dict[str, str],
    rng: random.Random,
) -> Sample:
    started = time.perf_counter()
    try:
        if route.method == "POST":
            resp = await client.post(
                route.path,
                json={"query": rng.choice(CHAT_QUESTIONS)},
                headers=headers,
            )
        else:
            resp = await client.get(route.path, params=route.params, headers=headers)
    except httpx.HTTPError as exc:
        latency = (time.perf_counter() - started) * 1000.0
        return Sample(route.name, 0, latency, f"{type(exc).__name__}: {exc}"[:200])
    latency = (time.perf_counter() - started) * 1000.0
    error = None if is_success(resp.status_code) else f"status_{resp.status_code}"
    return Sample(route.name, resp.status_code, latency, error)


async def virtual_user(
    user_id: int,
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    routes: Sequence[Route],
    deadline: float,
    samples: list[Sample],
) -> None:
    # Load generation only, not security-sensitive randomness.
    rng = random.Random(None if args.seed is None else args.seed + user_id)
    headers = {"X-Client-Id": f"{args.client_prefix}-{user_id}"}
    if args.api_key:
        headers["X-API-Key"] = args.api_key
    while time.perf_counter() < deadline:
        samples.append(await _send(client, rng.choice(routes), headers, rng))
        await asyncio.sleep(rng.uniform(*THINK_TIME_SEC))


async def run(
    args: argparse.Namespace, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    routes = routes_for(args.profile, args.audience)
    samples: list[Sample] = []
    # One pooled client, built before the clock starts (TLS setup is slow).
    limits = httpx.Limits(
        max_connections=args.concurrency, max_keepalive_connections=args.concurrency
    )
    async with httpx.AsyncClient(
        base_url=args.base_url,
        timeout=REQUEST_TIMEOUT_SEC,
        limits=limits,
        transport=transport,
    ) as client:
        t_start = time.perf_counter()
        deadline = t_start + args.duration
        async with asyncio.TaskGroup() as tg:
            for i in range(args.concurrency):
                tg.create_task(virtual_user(i, client, args, routes, deadline, samples))
        total_time = time.perf_counter() - t_start

    overall, per_route = summarize(samples)
    checks = evaluate_budgets(overall, per_route, routes, args.max_error_rate)
    throughput = overall["requests"] / total_time if total_time > 0 else 0.0
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "profile": args.profile.value,
            "smoke": args.smoke,
            "concurrency": args.concurrency,
            "duration_sec": args.duration,
            "audience": args.audience,
            "base_url": args.base_url,
        },
        "metrics": {
            "total_requests": overall["requests"],
            "total_time_sec": round(total_time, 2),
            "throughput_req_per_sec": round(throughput, 2),
            "error_count": overall["errors"],
            "rate_limited_count": overall["rate_limited"],
            "error_rate_pct": overall["error_rate_pct"],
            "latency_ms": overall["latency_ms"],
        },
        "routes": per_route,
        "budgets": checks,
        "passed": all(c["passed"] for c in checks),
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


async def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = parse_args(argv)
    logger.info(
        "Starting %s load test%s: %d users, %.1fs against %s",
        args.profile.value,
        " (smoke)" if args.smoke else "",
        args.concurrency,
        args.duration,
        args.base_url,
    )
    report = await run(args)
    await asyncio.to_thread(_write_report, args.output, report)

    m = report["metrics"]
    logger.info(
        "Done in %.2fs: %d requests, %.1f req/s, p95=%.1fms, errors=%d, 429s=%d",
        m["total_time_sec"],
        m["total_requests"],
        m["throughput_req_per_sec"],
        m["latency_ms"]["p95"],
        m["error_count"],
        m["rate_limited_count"],
    )
    for check in report["budgets"]:
        logger.info(
            "%s %s: actual=%s%s budget<%s%s",
            "PASS" if check["passed"] else "FAIL",
            check["check"],
            check["actual"],
            check["unit"],
            check["budget"],
            check["unit"],
        )
    logger.info("Report saved to %s", args.output)
    return exit_code(report["budgets"], m["total_requests"], args.report_only)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
