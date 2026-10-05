"""Run the RAG evaluation dataset against the live pipeline (real embedding
+ real LLM calls) and print a pass/fail + retrieval + latency report.

Usage (from backend/, with the venv active and Postgres/pgvector up):

    python scripts/run_evaluation.py                      # all cases
    python scripts/run_evaluation.py --judge              # + LLM-as-judge (extra LLM call per case)
    python scripts/run_evaluation.py --types answerable followup
    python scripts/run_evaluation.py --ids zt_pillars e8_control_list
    python scripts/run_evaluation.py --json report.json
    python scripts/run_evaluation.py --min-pass-rate 0.85   # exit 1 below threshold (for CI)
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Running `python scripts/run_evaluation.py` puts scripts/ (not backend/) on
# sys.path[0], so `app` isn't importable without this — add backend/ too.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import configure_logging
from app.core.database import AsyncSessionLocal, engine
from app.evaluation.dataset import EVAL_CASES
from app.evaluation.evaluator import STAGES, EvalResult, run_evaluation
from app.retrieval.vector_store import VectorStore

configure_logging()
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _print_progress(index: int, total: int, result: EvalResult) -> None:
    tag = "PASS" if result.passed else "FAIL"
    if result.known_issue:
        tag = "KNOWN-FIXED" if result.passed else "KNOWN"
    print(
        f"[{index}/{total}] {tag:<11} {result.case.id} "
        f"({result.latency_ms:.0f} ms)",
        flush=True,
    )


def _print_case_detail(result: EvalResult) -> None:
    case = result.case
    status = "PASS" if result.passed else "FAIL"
    if case.known_issue:
        status = "KNOWN ISSUE (now passing!)" if result.passed else "KNOWN ISSUE"

    print(f"\n[{status}] {case.id} ({case.case_type})")
    print(f"  query: {case.query[:120]}{'...' if len(case.query) > 120 else ''}")
    if case.history:
        print(f"  history_turns={len(case.history)} follow_up_detected={result.follow_up_detected}")
    print(
        f"  grounded={result.grounded} (expected {case.expect_grounded}) "
        f"confidence={result.confidence:.3f} flags={result.guardrail_flags}"
    )
    if case.expected_docs:
        print(
            f"  retrieval_hit={result.retrieval_ok} matched={sorted(result.matched_docs)} "
            f"expected_any_of={case.expected_docs}"
        )
        if result.retrieval_metrics:
            rm = result.retrieval_metrics
            print(
                f"  recall@1={rm['recall@1']:.2f} recall@3={rm['recall@3']:.2f} "
                f"recall@k={rm['recall@k']:.2f} precision@k={rm['precision@k']:.2f} "
                f"mrr={rm['mrr']:.2f} ndcg@k={rm['ndcg@k']:.2f}"
            )
        print("  retrieved:")
        for rank, item in enumerate(result.retrieved, start=1):
            print(
                f"    {rank}. {item['source_name']} | "
                f"{item['section_heading']} | score={item['score']}"
            )
    if result.heading_ok is not None:
        print(f"  heading_hit={result.heading_ok} expected_any_of={case.expected_headings}")
    if result.missing_keywords:
        print(f"  MISSING keywords: {result.missing_keywords}")
    if result.forbidden_found:
        print(f"  FORBIDDEN keywords present: {result.forbidden_found}")
    if result.follow_up_ok is False:
        print(
            f"  follow_up_detected={result.follow_up_detected} "
            f"(expected {case.expect_follow_up_detected})"
        )
    if result.judge:
        j = result.judge
        print(
            f"  judge: faithfulness={j.faithfulness} relevance={j.relevance} "
            f"completeness={j.completeness}"
        )
        if j.unsupported_claims:
            print(f"  judge unsupported_claims: {j.unsupported_claims}")
    if case.known_issue:
        print(f"  known_issue: {case.known_issue}")
    print(
        f"  latency_ms={result.latency_ms:.0f} answer_words={result.answer_word_count} "
        f"length_ok={result.length_ok}"
    )
    if not result.passed and result.answer:
        print(f"  answer: {result.answer[:300]}")


def _print_report(report, show_all: bool) -> None:
    print("\n" + "=" * 88)
    print("RAG EVALUATION REPORT")
    print("=" * 88)

    for result in report.results:
        # Passing cases are one line in the progress log; show detail only for
        # problems (or everything with --verbose).
        if show_all or not result.passed or result.known_issue:
            _print_case_detail(result)

    s = report.summary()
    print("\n" + "-" * 88)
    print(
        f"cases={s['n_cases']} scored={s['n_scored']} known_issues={s['n_known_issues']} "
        f"(known issues are excluded from the numbers below)"
    )
    passed = sum(1 for r in report.scored if r.passed)
    print(f"pass_rate={_pct(s['pass_rate'])}  ({passed}/{s['n_scored']})")
    print(f"retrieval_recall (doc hit)={_pct(s['retrieval_recall'])}")
    print(f"groundedness_accuracy={_pct(s['groundedness_accuracy'])}")

    r = s["retrieval"]
    if r:
        print(
            f"retrieval @k={s['top_k']}: recall@1={r['recall@1']:.3f} "
            f"recall@3={r['recall@3']:.3f} recall@k={r['recall@k']:.3f} "
            f"precision@k={r['precision@k']:.3f} MRR={r['mrr']:.3f} nDCG@k={r['ndcg@k']:.3f}"
        )
    print(f"heading_hit_rate={_pct(s['heading_hit_rate'])}")
    print(f"keyword_pass_rate={_pct(s['keyword_pass_rate'])}")
    print(f"length_ok_rate={_pct(s['length_ok_rate'])}")
    print(f"follow_up_accuracy={_pct(s['follow_up_accuracy'])}")

    j = s["judge"]
    if j:
        print(
            f"judge (n={j['n']}): faithfulness={j['faithfulness']:.2f}/5 "
            f"relevance={j['relevance']:.2f}/5 completeness={j['completeness']:.2f}/5"
        )

    print("\npass rate by case type:")
    for case_type, stats in s["by_case_type"].items():
        print(f"  {case_type:<15} {stats['passed']}/{stats['n']}  ({_pct(stats['pass_rate'])})")

    if s["known_issues"]:
        print("\nknown issues (not counted):")
        for item in s["known_issues"]:
            state = "FIXED - remove known_issue marker" if item["passing_now"] else "still failing"
            print(f"  {item['id']}: {state}")

    overall = s["latency"]["total"]
    print(
        f"\nlatency total_ms: n={overall['n']} p50={overall['p50']:.0f} "
        f"p70={overall['p70']:.0f} p100={overall['p100']:.0f}"
    )
    for stage in STAGES:
        stats = s["latency"][stage]
        if stats["n"]:
            print(
                f"latency {stage}_ms: n={stats['n']} p50={stats['p50']:.0f} "
                f"p70={stats['p70']:.0f} p100={stats['p100']:.0f}"
            )
    print("=" * 88)


def _to_json(report) -> dict:
    # Top-level keys (pass_rate, retrieval_recall, groundedness_accuracy,
    # latency, ...) match the original report; new metrics sit alongside them.
    return {**report.summary(), "cases": [r.to_dict() for r in report.results]}


def _select_cases(ids: list[str] | None, types: list[str] | None):
    cases = EVAL_CASES
    if ids:
        unknown = set(ids) - {c.id for c in cases}
        if unknown:
            raise SystemExit(f"Unknown case id(s): {sorted(unknown)}")
        cases = [c for c in cases if c.id in set(ids)]
    if types:
        cases = [c for c in cases if c.case_type in set(types)]
    if not cases:
        raise SystemExit("No cases selected.")
    return cases


async def main(args: argparse.Namespace) -> int:
    cases = _select_cases(args.ids, args.types)
    total = len(cases)
    counter = {"i": 0}

    def on_result(result: EvalResult) -> None:
        counter["i"] += 1
        _print_progress(counter["i"], total, result)

    print(f"Running {total} case(s) at top_k={args.top_k} judge={'on' if args.judge else 'off'}")

    async with AsyncSessionLocal() as db:
        vector_store = VectorStore(db)
        report = await run_evaluation(
            cases,
            vector_store,
            top_k=args.top_k,
            use_judge=args.judge,
            judge_min_faithfulness=args.judge_min_faithfulness,
            on_result=on_result,
        )

    _print_report(report, show_all=args.verbose)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(_to_json(report), f, indent=2)
        print(f"\nWrote JSON report to {args.json_path}")

    await engine.dispose()

    if args.min_pass_rate is not None and report.pass_rate < args.min_pass_rate:
        print(
            f"\nFAILED: pass_rate {report.pass_rate * 100:.1f}% is below the required "
            f"{args.min_pass_rate * 100:.1f}%"
        )
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", dest="json_path", default=None, help="write the full report as JSON")
    parser.add_argument("--judge", action="store_true", help="enable LLM-as-judge scoring (1 extra LLM call per case)")
    parser.add_argument("--judge-min-faithfulness", type=int, default=4, help="judge faithfulness (1-5) needed to pass; default 4")
    parser.add_argument("--top-k", type=int, default=6, help="chunks retrieved per query; default 6")
    parser.add_argument("--ids", nargs="+", help="run only these case ids")
    parser.add_argument("--types", nargs="+", help="run only these case types")
    parser.add_argument("--min-pass-rate", type=float, default=None, help="exit 1 if pass rate (0-1) is below this")
    parser.add_argument("--verbose", action="store_true", help="print detail for passing cases too")
    parsed = parser.parse_args()

    sys.exit(asyncio.run(main(parsed)))