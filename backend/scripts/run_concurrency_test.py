"""Concurrency test: N virtual users fire distinct queries at the real RAG
pipeline (real embedding + LLM calls) in parallel.

Calls `run_query` in-process, so nothing is written to the database: no chat
sessions, messages or audit rows (the pipeline itself only reads). Each
virtual user gets its own read-only DB session and a disjoint slice of a
de-duplicated query pool, so no query is ever sent twice.

Usage (from backend/, venv active, Postgres/pgvector up):

    python scripts/run_concurrency_test.py --users 10 --queries-per-user 3
    python scripts/run_concurrency_test.py --users 20 --queries-per-user 2 --think-time 0.5
    python scripts/run_concurrency_test.py --users 5 --json concurrency_report.json
"""

import argparse
import asyncio
import json
import logging
import os
import random
import statistics
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.concurrency import configure_thread_pool
from app.core.config import configure_logging, get_settings
from app.core.database import AsyncSessionLocal, engine
from app.evaluation.dataset import EVAL_CASES
from app.harness.orchestrator import run_query
from app.harness.prompts import call_llm
from app.retrieval.embeddings import embed_text, embedding_timing_sink
from app.retrieval.reranker import preload_reranker, rerank_timing_sink
from app.retrieval.vector_store import VectorStore

configure_logging()
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

_TEMPLATES = [
    "What is {t}?",
    "Explain {t} in simple terms.",
    "Why does {t} matter for an organisation?",
    "How does DeployIQ help with {t}?",
    "Who benefits most from {t}?",
    "What are the key benefits of {t}?",
    "Does DeployIQ support {t}, and how?",
    "Summarise what DeployIQ says about {t}.",
]
_TOPICS = [
    "the Essential Eight",
    "Zero Trust",
    "multi-factor authentication",
    "application control",
    "patching applications",
    "restricting administrative privileges",
    "regular backups",
    "responsible AI",
    "human-in-the-loop oversight",
    "audit readiness",
    "data privacy",
    "the Trust Center",
    "the Zero Trust Starter plan",
    "the Zero Trust Professional plan",
    "the cyber risk snapshot",
    "the DeployIQ platform",
    "least-privilege access",
    "continuous compliance monitoring",
    "identity verification",
    "network segmentation",
]


def build_query_pool(seed: int) -> list[str]:
    """Distinct, history-free queries: dataset questions + template combos."""
    seen: set[str] = set()
    pool: list[str] = []

    def add(q: str) -> None:
        key = " ".join(q.lower().split())
        if key not in seen:
            seen.add(key)
            pool.append(q)

    for case in EVAL_CASES:
        if not case.history:
            add(case.query)
    for topic in _TOPICS:
        for tmpl in _TEMPLATES:
            add(tmpl.format(t=topic))

    random.Random(seed).shuffle(pool)
    return pool


@dataclass
class Sample:
    user: int
    query: str
    ok: bool
    latency_ms: float
    started_at: float  # seconds since test start
    grounded: bool | None = None
    error: str | None = None
    # Stage name -> ms: guardrail_pre, retrieval, embedding, rerank, llm, guardrail_post.
    stages: dict[str, float] = field(default_factory=dict)


@dataclass
class State:
    in_flight: int = 0
    peak_in_flight: int = 0
    samples: list[Sample] = field(default_factory=list)
    loop_lag_ms: list[float] = field(default_factory=list)
    pool_checked_out: list[int] = field(default_factory=list)


async def virtual_user(
    user_id: int,
    queries: list[str],
    state: State,
    barrier: asyncio.Event,
    t0_holder: list[float],
    top_k: int,
    think_time: float,
    jitter: random.Random,
) -> None:
    await barrier.wait()
    # Own read-only session per user, like a separate request's db dependency.
    async with AsyncSessionLocal() as db:
        store = VectorStore(db)
        for q in queries:
            state.in_flight += 1
            state.peak_in_flight = max(state.peak_in_flight, state.in_flight)
            started = time.perf_counter()
            stage_timings: dict = {}
            rerank_sink: list[float] = []
            embed_sink: list[float] = []
            rtok = rerank_timing_sink.set(rerank_sink)
            etok = embedding_timing_sink.set(embed_sink)
            try:
                resp = await run_query(
                    query=q,
                    top_k=top_k,
                    filters=None,
                    vector_store=store,
                    request_id=f"conc-u{user_id}-{uuid.uuid4().hex[:8]}",
                    stage_timings=stage_timings,
                    history=None,
                )
                stages = {
                    k: v
                    for k, v in stage_timings.items()
                    if k != "retrieval_relevance_score" and isinstance(v, (int, float))
                }
                if embed_sink:
                    stages["embedding"] = sum(embed_sink)
                if rerank_sink:
                    stages["rerank"] = sum(rerank_sink)
                sample = Sample(
                    user_id,
                    q,
                    True,
                    (time.perf_counter() - started) * 1000,
                    started - t0_holder[0],
                    grounded=resp.grounded,
                    stages=stages,
                )
            except Exception as exc:
                await db.rollback()
                sample = Sample(
                    user_id,
                    q,
                    False,
                    (time.perf_counter() - started) * 1000,
                    started - t0_holder[0],
                    error=f"{type(exc).__name__}: {exc}"[:200],
                )
            finally:
                state.in_flight -= 1
                rerank_timing_sink.reset(rtok)
                embedding_timing_sink.reset(etok)
            state.samples.append(sample)
            print(
                f"[user {user_id:>3}] {'OK ' if sample.ok else 'ERR'} "
                f"{sample.latency_ms:7.0f} ms  {q[:70]}",
                flush=True,
            )
            if think_time > 0:
                await asyncio.sleep(think_time * (0.5 + jitter.random()))


async def loop_lag_monitor(state: State, stop: asyncio.Event) -> None:
    """Samples how late a 50 ms sleep wakes up; large lag = event loop blocked."""
    while not stop.is_set():
        t = time.perf_counter()
        await asyncio.sleep(0.05)
        state.loop_lag_ms.append((time.perf_counter() - t - 0.05) * 1000)
        state.pool_checked_out.append(engine.pool.checkedout())


def stage_table(samples: list[Sample]) -> dict[str, dict[str, float]]:
    """Per-stage stats over requests that reached that stage."""
    by_stage: dict[str, list[float]] = {}
    for s in samples:
        if not s.ok:
            continue
        st = dict(s.stages)
        # Retrieval minus embedding and rerank = DB search + thread hops + queuing.
        if "retrieval" in st:
            st["retrieval_other"] = max(
                0.0, st["retrieval"] - st.get("embedding", 0) - st.get("rerank", 0)
            )
        for k, v in st.items():
            by_stage.setdefault(k, []).append(v)
    order = [
        "guardrail_pre",
        "retrieval",
        "embedding",
        "rerank",
        "retrieval_other",
        "llm",
        "guardrail_post",
    ]
    table = {}
    for k in order + [k for k in by_stage if k not in order]:
        v = by_stage.get(k)
        if v:
            table[k] = {
                "n": len(v),
                "mean": statistics.mean(v),
                "p50": _pctl(v, 50),
                "p95": _pctl(v, 95),
                "max": max(v),
            }
    return table


def _pctl(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, round(p / 100 * (len(s) - 1)))]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--users", type=int, default=10, help="concurrent virtual users")
    ap.add_argument("--queries-per-user", type=int, default=3)
    ap.add_argument(
        "--think-time",
        type=float,
        default=0.0,
        help="mean seconds a user waits between their own queries",
    )
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--json", type=str, default=None, help="write report to this path")
    ap.add_argument(
        "--max-error-rate",
        type=float,
        default=None,
        help="exit 1 if error rate exceeds this fraction (e.g. 0.05)",
    )
    args = ap.parse_args()

    total = args.users * args.queries_per_user
    pool = build_query_pool(args.seed)
    if total > len(pool):
        print(
            f"Need {total} distinct queries but pool has {len(pool)}. "
            "Reduce --users/--queries-per-user.",
            file=sys.stderr,
        )
        return 2
    if len(set(pool[:total])) != total:  # all distinct
        raise RuntimeError("query pool contains duplicates")

    # Same warm-up the server does at startup, so cold imports/model loads
    # aren't raced by N users at once (and don't pollute latency numbers).
    configure_thread_pool()
    preload_reranker()
    await embed_text("warm-up")
    await call_llm("Reply with the single word: ok.", ["warm-up context"])

    top_k = get_settings().default_top_k
    state = State()
    barrier = asyncio.Event()
    t0_holder = [0.0]
    jitter = random.Random(args.seed)

    tasks = [
        asyncio.create_task(
            virtual_user(
                u,
                pool[u * args.queries_per_user : (u + 1) * args.queries_per_user],
                state,
                barrier,
                t0_holder,
                top_k,
                args.think_time,
                jitter,
            )
        )
        for u in range(args.users)
    ]

    print(
        f"Running {args.users} users x {args.queries_per_user} queries "
        f"= {total} distinct queries (no DB writes)\n",
        flush=True,
    )
    stop = asyncio.Event()
    monitor = asyncio.create_task(loop_lag_monitor(state, stop))
    cpu0 = os.times()
    t0_holder[0] = time.perf_counter()
    barrier.set()  # release every user at once
    await asyncio.gather(*tasks)
    wall = time.perf_counter() - t0_holder[0]
    cpu1 = os.times()
    stop.set()
    await monitor
    cpu_s = (cpu1.user - cpu0.user) + (cpu1.system - cpu0.system)
    logical = os.cpu_count() or 1
    # Client-process CPU only (embedding/rerank/tokenizing run in-process).
    cpu_util = cpu_s / wall / logical

    ok = [s for s in state.samples if s.ok]
    bad = [s for s in state.samples if not s.ok]
    lats = [s.latency_ms for s in ok]
    sum_latency_s = sum(s.latency_ms for s in state.samples) / 1000
    err_rate = len(bad) / len(state.samples) if state.samples else 0.0

    print("\n=== Concurrency report ===")
    print(
        f"users={args.users} requests={len(state.samples)} ok={len(ok)} "
        f"errors={len(bad)} ({err_rate * 100:.1f}%)"
    )
    print(f"wall time        : {wall:.2f}s")
    print(f"throughput       : {len(state.samples) / wall:.2f} req/s")
    print(f"peak in-flight   : {state.peak_in_flight}  (parallelism proof)")
    print(
        f"sum of latencies : {sum_latency_s:.1f}s -> speedup vs serial {sum_latency_s / wall:.1f}x"
    )
    if lats:
        print(
            f"latency ms       : mean={statistics.mean(lats):.0f} p50={_pctl(lats, 50):.0f} "
            f"p95={_pctl(lats, 95):.0f} max={max(lats):.0f}"
        )
    table = stage_table(state.samples)
    answered = sum(1 for s in ok if "retrieval" in s.stages)
    print(
        f"\nper-stage ms (under load; {answered} requests reached retrieval, "
        f"{len(ok) - answered} rejected early)"
    )
    print(f"  {'stage':<16}{'n':>4}{'mean':>9}{'p50':>9}{'p95':>9}{'max':>9}")
    for name, st in table.items():
        print(
            f"  {name:<16}{st['n']:>4}{st['mean']:>9.0f}{st['p50']:>9.0f}"
            f"{st['p95']:>9.0f}{st['max']:>9.0f}"
        )
    lag = state.loop_lag_ms
    print(
        f"\nevent-loop lag ms: p50={_pctl(lag, 50):.1f} p95={_pctl(lag, 95):.1f} "
        f"max={max(lag, default=0):.1f}"
    )
    print(
        f"process CPU      : {cpu_s:.1f}s over {wall:.1f}s wall = "
        f"{cpu_util * 100:.0f}% of {logical} logical cores"
    )
    settings = get_settings()
    held = state.pool_checked_out
    print(
        f"db pool          : peak checked-out={max(held, default=0)} "
        f"p50={_pctl([float(h) for h in held], 50):.0f} "
        f"(pool_size={settings.db_pool_size} + overflow={settings.db_max_overflow})"
    )
    print(
        f"config           : rerank_max_concurrency={settings.rerank_max_concurrency} "
        f"rerank_batch_size={settings.rerank_batch_size} "
        f"candidates_min={settings.retrieval_candidate_min} "
        f"thread_pool_workers={settings.thread_pool_workers}"
    )
    for s in bad[:10]:
        print(f"  ERR user {s.user}: {s.error}")

    if args.json:
        report = json.dumps(
            {
                "users": args.users,
                "requests": len(state.samples),
                "ok": len(ok),
                "errors": len(bad),
                "wall_s": wall,
                "peak_in_flight": state.peak_in_flight,
                "stage_ms": table,
                "db_pool_peak_checked_out": max(held, default=0),
                "loop_lag_ms": {
                    "p50": _pctl(lag, 50),
                    "p95": _pctl(lag, 95),
                    "max": max(lag, default=0),
                },
                "cpu": {
                    "process_cpu_s": cpu_s,
                    "logical_cores": logical,
                    "utilization": cpu_util,
                },
                "config": {
                    "rerank_max_concurrency": settings.rerank_max_concurrency,
                    "rerank_batch_size": settings.rerank_batch_size,
                    "retrieval_candidate_min": settings.retrieval_candidate_min,
                },
                "samples": [asdict(s) for s in state.samples],
            },
            indent=2,
        )
        await asyncio.to_thread(Path(args.json).write_text, report, encoding="utf-8")
        print(f"report written to {args.json}")

    await engine.dispose()
    if args.max_error_rate is not None and err_rate > args.max_error_rate:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
