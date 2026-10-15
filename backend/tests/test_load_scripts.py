"""Unit tests for the pure parts of scripts/run_admin_load_test.py (no network)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from scripts import run_admin_load_test as lt


def _sample(route: str, status: int = 200, latency: float = 10.0) -> lt.Sample:
    error = None if lt.is_success(status) else f"status_{status}"
    return lt.Sample(route, status, latency, error)


# --- percentiles ---------------------------------------------------------


def test_percentile_empty_is_zero() -> None:
    assert lt.percentile([], 95) == 0.0
    assert lt.calculate_percentiles([]) == {
        "p50": 0.0,
        "p70": 0.0,
        "p95": 0.0,
        "p99": 0.0,
    }


def test_percentile_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]  # 1..100
    assert lt.percentile(values, 50) == 50.0
    assert lt.percentile(values, 95) == 95.0
    assert lt.percentile(values, 99) == 99.0
    assert lt.percentile(values, 100) == 100.0


def test_percentile_is_order_independent_and_single_value() -> None:
    assert lt.percentile([30.0, 10.0, 20.0], 50) == 20.0
    assert lt.percentile([7.123], 99) == 7.12


# --- error classification and summaries ----------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [(200, False), (304, False), (429, False), (0, True), (401, True), (503, True)],
)
def test_is_error_excludes_deliberate_429(status: int, expected: bool) -> None:
    assert lt.is_error(status) is expected


def test_summarize_groups_per_route() -> None:
    samples = [
        _sample("GET /a", 200, 10),
        _sample("GET /a", 500, 30),
        _sample("GET /b", 429, 5),
        _sample("GET /b", 200, 15),
    ]
    overall, per_route = lt.summarize(samples)
    assert overall["requests"] == 4
    assert overall["errors"] == 1
    assert overall["rate_limited"] == 1
    assert overall["error_rate_pct"] == 25.0
    assert set(per_route) == {"GET /a", "GET /b"}
    assert per_route["GET /a"]["error_rate_pct"] == 50.0
    assert per_route["GET /b"]["errors"] == 0
    assert per_route["GET /b"]["rate_limited"] == 1
    assert per_route["GET /a"]["latency_ms"]["p99"] == 30.0


# --- budget evaluation ---------------------------------------------------

AGG = lt.Route("GET /agg", "GET", "/agg", lt.BudgetKind.AGGREGATION)
LST = lt.Route("GET /list", "GET", "/list", lt.BudgetKind.LIST)
CHAT = lt.Route("POST /query", "POST", "/query", lt.BudgetKind.NONE)


def _checks(samples: list[lt.Sample]) -> list[dict]:
    overall, per_route = lt.summarize(samples)
    return lt.evaluate_budgets(overall, per_route, [AGG, LST, CHAT])


def test_budgets_pass_within_limits() -> None:
    checks = _checks(
        [_sample("GET /agg", latency=100)] * 20
        + [_sample("GET /list", latency=100)] * 20
        + [_sample("POST /query", latency=5000)] * 5
    )
    assert [c["check"] for c in checks] == [
        "GET /agg p95",
        "GET /list p95",
        "error rate (excluding 429)",
    ]
    assert all(c["passed"] for c in checks)
    assert lt.exit_code(checks, 45, report_only=False) == 0


def test_list_p95_over_budget_fails() -> None:
    checks = _checks(
        [_sample("GET /list", latency=100)] * 18
        + [_sample("GET /list", latency=300)] * 2
    )
    by_name = {c["check"]: c for c in checks}
    assert by_name["GET /list p95"]["actual"] == 300.0
    assert by_name["GET /list p95"]["budget"] == 250.0
    assert not by_name["GET /list p95"]["passed"]
    assert "GET /agg p95" not in by_name  # route never ran: no check
    assert lt.exit_code(checks, 20, report_only=False) == 1
    assert lt.exit_code(checks, 20, report_only=True) == 0


def test_aggregation_budget_is_600ms_and_strict() -> None:
    at_budget = _checks([_sample("GET /agg", latency=600)] * 10)
    assert not at_budget[0]["passed"]
    under = _checks([_sample("GET /agg", latency=599)] * 10)
    assert under[0]["passed"]


def test_error_rate_budget_excludes_429() -> None:
    ok = [_sample("GET /agg", 200, 10)] * 199
    throttled_only = _checks([*ok, *[_sample("GET /agg", 429, 10)] * 50])
    assert throttled_only[-1]["passed"]

    one_error = _checks([*ok, _sample("GET /agg", 500, 10)])  # 0.5 %
    assert one_error[-1]["actual"] == 0.5
    assert not one_error[-1]["passed"]


def test_custom_error_budget_and_no_requests() -> None:
    overall, per_route = lt.summarize(
        [_sample("GET /agg", 500)] + [_sample("GET /agg")] * 9
    )
    checks = lt.evaluate_budgets(overall, per_route, [AGG], max_error_rate_pct=20.0)
    assert checks[-1]["passed"]

    empty_overall, empty_routes = lt.summarize([])
    empty = lt.evaluate_budgets(empty_overall, empty_routes, [AGG])
    assert len(empty) == 1
    assert not empty[0]["passed"]
    assert lt.exit_code(empty, 0, report_only=True) == 2


# --- argument parsing ----------------------------------------------------


def test_parse_defaults_keep_legacy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY_NAME", "env-key")
    args = lt.parse_args([])
    assert args.profile is lt.Profile.ADMIN
    assert args.concurrency == lt.DEFAULT_CONCURRENCY
    assert args.duration == lt.DEFAULT_DURATION_SEC
    assert args.base_url == "http://127.0.0.1:8000"
    assert args.api_key == "env-key"
    assert args.output == lt.DEFAULT_REPORT
    assert not args.smoke
    assert not args.report_only


def test_parse_smoke_defaults_to_50_users() -> None:
    args = lt.parse_args(["--smoke"])
    assert args.concurrency == lt.SMOKE_CONCURRENCY == 50
    assert args.duration == lt.SMOKE_DURATION_SEC


def test_parse_explicit_values_override_smoke(tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    args = lt.parse_args(
        [
            "--smoke",
            "--profile",
            "chat",
            "--concurrency",
            "100",
            "--duration",
            "2.5",
            "--base-url",
            "http://lb:8080",
            "--api-key",
            "k",
            "--output",
            str(out),
        ]
    )
    assert args.profile is lt.Profile.CHAT
    assert args.concurrency == 100
    assert args.duration == 2.5
    assert args.base_url == "http://lb:8080"
    assert args.api_key == "k"
    assert args.output == out


def test_parse_readers_alias_still_works() -> None:
    args = lt.parse_args(["--readers", "7", "--profile", "usage"])
    assert args.concurrency == 7
    assert args.profile is lt.Profile.USAGE


@pytest.mark.parametrize(
    "argv",
    [
        ["--profile", "bogus"],
        ["--concurrency", "0"],
        ["--duration", "0"],
        ["--concurrency", "x"],
    ],
)
def test_parse_rejects_invalid(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        lt.parse_args(argv)


# --- profiles ------------------------------------------------------------


def test_profiles_route_mix() -> None:
    usage = lt.routes_for(lt.Profile.USAGE, "external")
    assert [r.path for r in usage] == [
        "/admin/usage/audience-counts",
        "/admin/usage/summary",
        "/admin/usage/users",
        "/admin/usage/sessions",
        "/admin/usage/queries",
    ]
    assert usage[1].params == {"audience": "external"}

    admin = lt.routes_for(lt.Profile.ADMIN)
    assert {r.path for r in usage} <= {r.path for r in admin}
    assert all(r.budget is not lt.BudgetKind.NONE for r in admin)
    assert len({r.name for r in admin}) == len(admin)

    chat = lt.routes_for(lt.Profile.CHAT)
    assert [(r.method, r.path, r.budget) for r in chat] == [
        ("POST", "/query", lt.BudgetKind.NONE)
    ]


# --- end-to-end over an in-memory transport (no sockets) -----------------


async def test_run_report_over_mock_transport() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/admin/feedback":
            return httpx.Response(500)
        return httpx.Response(200, json={})

    args = lt.parse_args(
        ["--concurrency", "4", "--duration", "0.2", "--api-key", "k", "--seed", "1"]
    )
    report = await lt.run(args, transport=httpx.MockTransport(handler))

    assert report["metrics"]["total_requests"] == len(seen) > 0
    assert {r.headers["X-API-Key"] for r in seen} == {"k"}
    assert {r.headers["X-Client-Id"] for r in seen} <= {
        f"loadtest-{i}" for i in range(4)
    }
    assert report["config"]["concurrency"] == 4
    for stats in report["routes"].values():
        assert set(stats["latency_ms"]) == {"p50", "p70", "p95", "p99"}
    failed = sum(1 for r in seen if r.url.path == "/admin/feedback")
    assert report["metrics"]["error_count"] == failed
    assert report["passed"] is (failed == 0)
    expected_exit = 0 if failed == 0 else 1
    assert (
        lt.exit_code(report["budgets"], len(seen), report_only=False) == expected_exit
    )
