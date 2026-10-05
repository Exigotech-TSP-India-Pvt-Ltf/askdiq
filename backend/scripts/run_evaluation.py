"""Run the RAG evaluation dataset against the live pipeline (real embedding
+ real LLM calls) and print a pass/fail + latency report.

Usage (from backend/, with the venv active and Postgres/pgvector up):
    python scripts/run_evaluation.py [--json report.json]
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
from app.evaluation.evaluator import run_evaluation
from app.retrieval.vector_store import VectorStore

configure_logging()
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)


def _print_report(report) -> None:
    print("\n" + "=" * 88)
    print("RAG EVALUATION REPORT")
    print("=" * 88)

    for result in report.results:
        status = "PASS" if result.passed else "FAIL"
        print(f"\n[{status}] {result.case.id} ({result.case.case_type})")
        print(f"  query: {result.case.query}")
        print(
            f"  grounded={result.grounded} (expected {result.case.expect_grounded}) "
            f"confidence={result.confidence:.3f} flags={result.guardrail_flags}"
        )
        if result.case.expected_docs:
            print(
                f"  retrieval_hit={result.retrieval_ok} "
                f"matched={sorted(result.matched_docs)} expected_any_of={result.case.expected_docs}"
            )
        print(
            f"  latency_ms={result.latency_ms:.0f} answer_words={result.answer_word_count}"
        )

    print("\n" + "-" * 88)
    print(
        f"pass_rate={report.pass_rate * 100:.1f}%  ({sum(r.passed for r in report.results)}/{len(report.results)})"
    )
    print(f"retrieval_recall={report.retrieval_recall * 100:.1f}%")
    print(f"groundedness_accuracy={report.groundedness_accuracy * 100:.1f}%")

    overall = report.latency_percentiles()
    print(
        f"latency total_ms: n={overall['n']} p50={overall['p50']:.0f} "
        f"p70={overall['p70']:.0f} p100={overall['p100']:.0f}"
    )

    for stage in ("guardrail_pre", "retrieval", "llm", "guardrail_post"):
        stats = report.latency_percentiles(stage)
        if stats["n"]:
            print(
                f"latency {stage}_ms: n={stats['n']} p50={stats['p50']:.0f} "
                f"p70={stats['p70']:.0f} p100={stats['p100']:.0f}"
            )
    print("=" * 88)


def _to_json(report) -> dict:
    return {
        "pass_rate": report.pass_rate,
        "retrieval_recall": report.retrieval_recall,
        "groundedness_accuracy": report.groundedness_accuracy,
        "latency": {
            "total": report.latency_percentiles(),
            **{
                stage: report.latency_percentiles(stage)
                for stage in ("guardrail_pre", "retrieval", "llm", "guardrail_post")
            },
        },
        "cases": [
            {
                "id": r.case.id,
                "type": r.case.case_type,
                "query": r.case.query,
                "passed": r.passed,
                "grounded": r.grounded,
                "confidence": r.confidence,
                "guardrail_flags": r.guardrail_flags,
                "matched_docs": sorted(r.matched_docs),
                "latency_ms": r.latency_ms,
                "answer_word_count": r.answer_word_count,
            }
            for r in report.results
        ],
    }


async def main(json_path: str | None) -> None:
    async with AsyncSessionLocal() as db:
        vector_store = VectorStore(db)
        report = await run_evaluation(EVAL_CASES, vector_store)

    _print_report(report)

    if json_path:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(_to_json(report), f, indent=2)
        print(f"\nWrote JSON report to {json_path}")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args()

    asyncio.run(main(args.json_path))
