"""Run the RAG evaluation dataset against the live pipeline (real embedding
+ real LLM calls) and print a pass/fail + retrieval + latency report.

Usage (from backend/, with the venv active and Postgres/pgvector up):

    python scripts/run_evaluation.py                      # all cases
    python scripts/run_evaluation.py --judge              # + LLM-as-judge (extra LLM call per case)
    python scripts/run_evaluation.py --types answerable followup
    python scripts/run_evaluation.py --ids zt_pillars e8_control_list
    python scripts/run_evaluation.py --json report.json
    python scripts/run_evaluation.py --min-pass-rate 0.85   # exit 1 below threshold (default 0.9)
    python scripts/run_evaluation.py --type-min safety=1.0 --type-min unsafe=1.0   # per-type floors
    python scripts/run_evaluation.py --fail-on-known-issue  # exit 1 while known_issue cases exist
    python scripts/run_evaluation.py --kb                  # + knowledge-base / ingestion evaluation
    python scripts/run_evaluation.py --kb-only --kb-db     # ingestion checks only, incl. stored DB state
    python scripts/run_evaluation.py --repeat 3 --concurrency 4 --price-in 0.25 --price-out 2.0
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

from datetime import UTC

from app.core.config import configure_logging
from app.core.database import AsyncSessionLocal, engine
from app.evaluation.dataset import ALL_DOCS, EvalCase
from app.evaluation.dataset_extra import load_promoted_cases_with_source
from app.evaluation.evaluator import STAGES, EvalResult, run_evaluation
from app.evaluation.runs import report_payload, select_cases
from app.retrieval.vector_store import VectorStore

_REAL_DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "real_docs"

# CI gates: overall floor plus per-type floors (safety and unsafe must be perfect).
DEFAULT_MIN_PASS_RATE = 0.9
DEFAULT_TYPE_MINIMUMS: dict[str, float] = {"safety": 1.0, "unsafe": 1.0}

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
        f"[{index}/{total}] {tag:<11} {result.case.id} ({result.latency_ms:.0f} ms)",
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
        print(
            f"  history_turns={len(case.history)} follow_up_detected={result.follow_up_detected}"
        )
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
        print(
            f"  heading_hit={result.heading_ok} expected_any_of={case.expected_headings}"
        )
    if result.missing_keywords:
        print(f"  MISSING keywords: {result.missing_keywords}")
    if result.forbidden_found:
        print(f"  FORBIDDEN keywords present: {result.forbidden_found}")
    if result.internal_wording:
        print(f"  BANNED internal wording: {result.internal_wording}")
    if result.contact_step_missing:
        print("  MISSING contact step (reply must point to a specialist)")
    if result.false_abstention:
        print("  FALSE ABSTENTION on an answerable case")
    if result.clarification_ok is False:
        print(f"  CLARIFICATION mismatch (clarified={result.clarified})")
    if result.sources_ok is False:
        print("  SOURCES present but the case expects none")
    if result.judge_error:
        print("  JUDGE failed (counts as a failed case)")
    if result.follow_up_ok is False:
        print(
            f"  follow_up_detected={result.follow_up_detected} "
            f"(expected {case.expect_follow_up_detected})"
        )
    if result.judge:
        j = result.judge
        print(
            f"  judge: faithfulness={j.faithfulness} relevance={j.relevance} "
            f"completeness={j.completeness} ok={result.judge_ok}"
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
        print(
            f"  {case_type:<15} {stats['passed']}/{stats['n']}  ({_pct(stats['pass_rate'])})"
        )

    # Always printed, even when empty, so a known issue can never hide.
    print(f"\nknown issues (not counted): {len(s['known_issues'])}")
    for item in s["known_issues"]:
        state = (
            "FIXED - remove known_issue marker"
            if item["passing_now"]
            else "still failing"
        )
        print(f"  {item['id']}: {state}")
    print(f"known_issue_ids={s['known_issue_ids']}")

    gates = s["quality_gates"]
    print(
        "quality gates: "
        f"internal_wording={gates['n_internal_wording']} "
        f"contact_step_missing={gates['n_contact_step_missing']} "
        f"false_abstention={gates['n_false_abstention']} "
        f"judge_errors={gates['n_judge_errors']} "
        f"clarification_mismatch={gates['n_clarification_mismatch']}"
    )
    if gates["internal_wording"]:
        print(f"  banned internal wording seen: {gates['internal_wording']}")

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


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _print_section(title: str, data) -> None:
    """Print a nested dict of metrics, skipping empty/None leaves."""

    def walk(node, indent):
        for key, val in node.items():
            if isinstance(val, dict):
                if val:
                    print(f"{' ' * indent}{key}:")
                    walk(val, indent + 2)
            elif val not in (None, [], ()):
                print(f"{' ' * indent}{key}: {_fmt(val)}")

    print(f"\n[{title}]")
    walk(data, 2)


def _print_extended(s: dict) -> None:
    print("\n" + "=" * 88)
    print(
        "KNOWLEDGE-BASE RAG EVALUATION (retrieval / answer / citation / safety / robustness / ops)"
    )
    print("=" * 88)
    r = s["retrieval"]
    if r:
        keys = ("hit_rate@k", "context_coverage", "noise_ratio")
        _print_section("retrieval (extra)", {k: r[k] for k in keys if k in r})
    _print_section("noise sensitivity", s["noise_sensitivity"])
    _print_section("answer quality (judge)", s["deep_judge"])
    _print_section("citation & attribution", s["citation"])
    _print_section("abstention", s["abstention"])
    _print_section("safety / privacy / security", s["safety"])
    _print_section("robustness", s["robustness"])
    _print_section("operational", s["operational"])


async def _run_kb(args: argparse.Namespace) -> dict:
    from datetime import datetime

    from app.chunking.metadata_aware import MetadataAwareChunker
    from app.chunking.structure_aware import StructureAwareChunker
    from app.evaluation import kb_eval

    source_dir = _REAL_DOCS_DIR
    corpus = await kb_eval.load_corpus(source_dir)
    chunks, timings = await kb_eval.chunk_corpus(
        corpus, lambda: MetadataAwareChunker(base_strategy=StructureAwareChunker())
    )
    mtimes = await asyncio.to_thread(
        lambda: {
            n: datetime.fromtimestamp((source_dir / n).stat().st_mtime, tz=UTC)
            for n in corpus
        }
    )
    result = kb_eval.evaluate_kb(
        corpus, chunks, ALL_DOCS, mtimes=mtimes, timings_ms=timings
    )
    if args.kb_db:
        from ingest_real_docs import _sha256

        async with AsyncSessionLocal() as db:
            result["ingested"] = await kb_eval.evaluate_ingested(
                db, corpus, mtimes, _sha256
            )

    print("\n" + "=" * 88)
    print("KNOWLEDGE BASE & INGESTION EVALUATION")
    print("=" * 88)
    summary = {k: v for k, v in result["summary"].items() if k != "duplicates"}
    summary["duplicates"] = {
        "exact": len(result["summary"]["duplicates"]["exact"]),
        "near": len(result["summary"]["duplicates"]["near"]),
    }
    summary["metadata_problems"] = len(summary["metadata_problems"])
    _print_section("summary", summary)
    if "ingested" in result:
        _print_section("stored in database", result["ingested"])
    print("\nfindings:")
    for finding in result["findings"] or ["none"]:
        print(f"  - {finding}")
    return result


def _to_json(report) -> dict:
    # Top-level keys (pass_rate, retrieval_recall, groundedness_accuracy,
    # latency, ...) match the original report; new metrics sit alongside them.
    # Shared with the "Run evaluation" job (app.evaluation.run_job).
    return report_payload(report)


def _select_cases(
    ids: list[str] | None, types: list[str] | None, promoted: list | None = None
):
    cases = select_cases(promoted or [])
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


def _parse_type_minimums(pairs: list[str] | None) -> dict[str, float]:
    """``["safety=1.0", ...]`` over the defaults (safety and unsafe at 100%)."""

    minimums = dict(DEFAULT_TYPE_MINIMUMS)
    for pair in pairs or []:
        name, _, value = pair.partition("=")
        try:
            minimums[name.strip()] = float(value)
        except ValueError:
            raise SystemExit(f"--type-min expects TYPE=RATE, got {pair!r}") from None
    return minimums


async def main(args: argparse.Namespace) -> int:
    kb_result = await _run_kb(args) if (args.kb or args.kb_only) else None
    if args.kb_only:
        if args.json_path:
            await asyncio.to_thread(
                Path(args.json_path).write_text,
                json.dumps({"kb": kb_result}, indent=2, default=str),
                encoding="utf-8",
            )
        await engine.dispose()
        return 0

    # Golden cases promoted from feedback (DB, JSON fallback).
    promoted, promoted_source = await load_promoted_cases_with_source(EvalCase)
    cases = _select_cases(args.ids, args.types, promoted)
    total = len(cases) * max(1, args.repeat)
    counter = {"i": 0}

    def on_result(result: EvalResult) -> None:
        counter["i"] += 1
        _print_progress(counter["i"], total, result)

    known = sorted(c.id for c in cases if c.known_issue)
    print(
        f"Cases: {len(cases)} selected (benchmark + {len(promoted)} promoted from "
        f"{promoted_source.upper()}), repeat={max(1, args.repeat)}"
    )
    print(f"Known-issue cases ({len(known)}): {known if known else 'none'}")
    print(
        f"Running {total} case(s) at top_k={args.top_k} judge={'on' if args.judge else 'off'}"
    )

    async with AsyncSessionLocal() as db:
        vector_store = VectorStore(db)
        report = await run_evaluation(
            cases,
            vector_store,
            top_k=args.top_k,
            use_judge=args.judge,
            judge_min_faithfulness=args.judge_min_faithfulness,
            judge_min_relevance=args.judge_min_relevance,
            judge_min_completeness=args.judge_min_completeness,
            judge_failure_fails=not args.allow_judge_failure,
            on_result=on_result,
            repeat=args.repeat,
            concurrency=args.concurrency,
            price_in_per_1m=args.price_in,
            price_out_per_1m=args.price_out,
            session_factory=AsyncSessionLocal,
        )

    _print_report(report, show_all=args.verbose)
    _print_extended(report.summary())

    if args.json_path:
        await asyncio.to_thread(
            Path(args.json_path).write_text,
            json.dumps(
                {**_to_json(report), **({"kb": kb_result} if kb_result else {})},
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        print(f"\nWrote JSON report to {args.json_path}")

    await engine.dispose()

    failures = report.gate_failures(
        args.min_pass_rate,
        _parse_type_minimums(args.type_min),
        fail_on_known_issue=args.fail_on_known_issue,
    )
    for failure in failures:
        print(f"\nFAILED: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--json", dest="json_path", default=None, help="write the full report as JSON"
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="enable LLM-as-judge scoring (1 extra LLM call per case)",
    )
    parser.add_argument(
        "--judge-min-faithfulness",
        type=int,
        default=4,
        help="judge faithfulness (1-5) needed to pass; default 4",
    )
    parser.add_argument(
        "--judge-min-relevance",
        type=int,
        default=4,
        help="judge relevance (1-5) needed to pass; default 4",
    )
    parser.add_argument(
        "--judge-min-completeness",
        type=int,
        default=4,
        help="judge completeness (1-5) needed to pass; default 4",
    )
    parser.add_argument(
        "--allow-judge-failure",
        action="store_true",
        help="do not fail a case when the judge itself errors (default: fail)",
    )
    parser.add_argument(
        "--top-k", type=int, default=6, help="chunks retrieved per query; default 6"
    )
    parser.add_argument("--ids", nargs="+", help="run only these case ids")
    parser.add_argument("--types", nargs="+", help="run only these case types")
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=DEFAULT_MIN_PASS_RATE,
        help=f"exit 1 if pass rate (0-1) is below this; default {DEFAULT_MIN_PASS_RATE}",
    )
    parser.add_argument(
        "--type-min",
        action="append",
        metavar="TYPE=RATE",
        help=(
            "per case-type minimum pass rate, repeatable; defaults: "
            + ", ".join(f"{k}={v}" for k, v in DEFAULT_TYPE_MINIMUMS.items())
        ),
    )
    parser.add_argument(
        "--fail-on-known-issue",
        action="store_true",
        help="exit 1 while any case still carries a known_issue marker",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="run each case N times (repeated-question consistency)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="cases in flight at once (throughput/concurrency)",
    )
    parser.add_argument(
        "--price-in",
        type=float,
        default=0.0,
        help="USD per 1M input tokens (cost metrics)",
    )
    parser.add_argument(
        "--price-out",
        type=float,
        default=0.0,
        help="USD per 1M output tokens (cost metrics)",
    )
    parser.add_argument(
        "--kb",
        action="store_true",
        help="also run the knowledge-base / ingestion evaluation",
    )
    parser.add_argument(
        "--kb-only",
        action="store_true",
        help="run only the knowledge-base evaluation (no LLM calls)",
    )
    parser.add_argument(
        "--kb-db",
        action="store_true",
        help="with --kb: also compare stored DB documents to source files",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="print detail for passing cases too"
    )
    parsed = parser.parse_args()

    sys.exit(asyncio.run(main(parsed)))
