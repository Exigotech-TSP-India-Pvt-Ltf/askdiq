"""Full-stack smoke test (no browser): Next.js frontend -> proxy -> FastAPI.

Run after the backend (uvicorn) and the frontend (`next start`) are up:

    python scripts/smoke_e2e.py --frontend-url http://localhost:3000 \
        --backend-url http://127.0.0.1:8000

Base checks always run. Later PRs register sign-in and role journeys in
``JOURNEYS`` and select them with ``--journey <name>`` (repeatable). One
cookie-carrying ``httpx.AsyncClient`` is shared by every step so a journey
can sign in once and reuse the session cookie.

Exits 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

DEFAULT_FRONTEND_URL = "http://localhost:3000"
DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 15.0
HTTP_OK = 200
HTTP_SERVER_ERROR = 500
HTTP_TEMPORARY_REDIRECT = 307
HTTP_PERMANENT_REDIRECT = 308
EVALUATION_LIVE_PATH = "/admin/evaluation-live"
# Retired routes that must redirect to Evaluation (Live) (newplan §2.3).
LEGACY_EVALUATION_PATHS = ("/admin/evaluation", "/evaluation")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("smoke_e2e")


@dataclass(frozen=True)
class SmokeContext:
    client: httpx.AsyncClient
    frontend_url: str
    backend_url: str


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


Step = Callable[[SmokeContext], Awaitable[CheckResult]]


async def _expect_status(
    ctx: SmokeContext,
    name: str,
    url: str,
    accept: Callable[[int], bool],
    expectation: str,
) -> CheckResult:
    try:
        response = await ctx.client.get(url)
    except httpx.HTTPError as exc:
        return CheckResult(name, False, f"GET {url} failed: {exc!r}")
    ok = accept(response.status_code)
    detail = f"GET {url} -> {response.status_code} (expected {expectation})"
    return CheckResult(name, ok, detail)


async def check_frontend_home(ctx: SmokeContext) -> CheckResult:
    return await _expect_status(
        ctx,
        "frontend home",
        f"{ctx.frontend_url}/",
        lambda code: code < HTTP_SERVER_ERROR,
        f"< {HTTP_SERVER_ERROR}",
    )


async def check_proxy_health(ctx: SmokeContext) -> CheckResult:
    return await _expect_status(
        ctx,
        "frontend proxy -> backend /health",
        f"{ctx.frontend_url}/api/proxy/health",
        lambda code: code == HTTP_OK,
        str(HTTP_OK),
    )


async def check_backend_health(ctx: SmokeContext) -> CheckResult:
    return await _expect_status(
        ctx,
        "backend /health",
        f"{ctx.backend_url}/health",
        lambda code: code == HTTP_OK,
        str(HTTP_OK),
    )


async def _expect_redirect(ctx: SmokeContext, path: str, target: str) -> CheckResult:
    """GET ``path`` without following redirects; expect 307/308 to ``target``."""
    name = f"legacy redirect {path} -> {target}"
    url = f"{ctx.frontend_url}{path}"
    try:
        response = await ctx.client.get(url, follow_redirects=False)
    except httpx.HTTPError as exc:
        return CheckResult(name, False, f"GET {url} failed: {exc!r}")
    location = response.headers.get("location", "")
    ok = (
        response.status_code in {HTTP_TEMPORARY_REDIRECT, HTTP_PERMANENT_REDIRECT}
        and httpx.URL(location).path == target
    )
    detail = (
        f"GET {url} -> {response.status_code} location={location!r} "
        f"(expected {HTTP_TEMPORARY_REDIRECT}/{HTTP_PERMANENT_REDIRECT} to {target})"
    )
    return CheckResult(name, ok, detail)


async def check_legacy_evaluation_redirects(ctx: SmokeContext) -> CheckResult:
    results = [
        await _expect_redirect(ctx, path, EVALUATION_LIVE_PATH)
        for path in LEGACY_EVALUATION_PATHS
    ]
    failed = [r for r in results if not r.ok]
    return CheckResult(
        "legacy evaluation redirects",
        not failed,
        "; ".join(r.detail for r in (failed or results)),
    )


BASE_CHECKS: tuple[Step, ...] = (
    check_backend_health,
    check_frontend_home,
    check_proxy_health,
    check_legacy_evaluation_redirects,
)

# Named journeys added by later PRs, e.g. {"sign-in": (step1, step2, ...)}.
JOURNEYS: dict[str, tuple[Step, ...]] = {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--frontend-url",
        default=os.environ.get("SMOKE_FRONTEND_URL", DEFAULT_FRONTEND_URL),
        help="Next.js base URL (env SMOKE_FRONTEND_URL)",
    )
    parser.add_argument(
        "--backend-url",
        default=os.environ.get("SMOKE_BACKEND_URL", DEFAULT_BACKEND_URL),
        help="FastAPI base URL (env SMOKE_BACKEND_URL)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Per-request timeout in seconds",
    )
    parser.add_argument(
        "--journey",
        action="append",
        default=[],
        choices=sorted(JOURNEYS),
        help="Extra journey to run after the base checks (repeatable)",
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> list[CheckResult]:
    steps: list[Step] = list(BASE_CHECKS)
    for journey in args.journey:
        steps.extend(JOURNEYS[journey])

    results: list[CheckResult] = []
    async with httpx.AsyncClient(
        timeout=args.timeout,
        follow_redirects=True,
        cookies=httpx.Cookies(),
    ) as client:
        ctx = SmokeContext(
            client=client,
            frontend_url=args.frontend_url.rstrip("/"),
            backend_url=args.backend_url.rstrip("/"),
        )
        for step in steps:
            result = await step(ctx)
            log = logger.info if result.ok else logger.error
            log(
                "[%s] %s: %s",
                "PASS" if result.ok else "FAIL",
                result.name,
                result.detail,
            )
            results.append(result)
    return results


def main(argv: list[str] | None = None) -> int:
    results = asyncio.run(run(parse_args(argv)))
    failed = [r for r in results if not r.ok]
    logger.info("%d/%d checks passed", len(results) - len(failed), len(results))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
