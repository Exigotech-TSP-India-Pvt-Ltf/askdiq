"""Runs the EVAL_CASES dataset through the real pipeline (real embedding +
real LLM calls) and scores retrieval hit-rate, groundedness accuracy, and
guardrail behavior, plus reports P50/P70/P100 latency across the run.
"""

import uuid
from dataclasses import dataclass, field

from app.core.stats import percentile
from app.evaluation.dataset import EvalCase
from app.harness.orchestrator import run_query
from app.retrieval.vector_store import VectorStore


@dataclass
class EvalResult:
    case: EvalCase
    grounded: bool
    confidence: float
    guardrail_flags: list[str]
    matched_docs: set[str]
    latency_ms: float
    stage_timings: dict
    answer_word_count: int
    answer: str
    retrieval_ok: bool | None  # None when the case has no expected_docs to check
    grounded_ok: bool
    flags_ok: bool

    @property
    def passed(self) -> bool:
        return self.grounded_ok and self.flags_ok and self.retrieval_ok is not False


@dataclass
class EvaluationReport:
    results: list[EvalResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    @property
    def retrieval_recall(self) -> float:
        checked = [r for r in self.results if r.retrieval_ok is not None]
        if not checked:
            return 1.0
        return sum(1 for r in checked if r.retrieval_ok) / len(checked)

    @property
    def groundedness_accuracy(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.grounded_ok) / len(self.results)

    def latency_percentiles(self, field_name: str | None = None) -> dict:
        if field_name:
            values = [
                r.stage_timings[field_name]
                for r in self.results
                if isinstance(r.stage_timings.get(field_name), (int, float))
            ]
        else:
            values = [r.latency_ms for r in self.results]

        return {
            "n": len(values),
            "p50": percentile(values, 0.50),
            "p70": percentile(values, 0.70),
            "p100": percentile(values, 1.0),
        }


async def evaluate_case(case: EvalCase, vector_store: VectorStore) -> EvalResult:
    stage_timings: dict = {}

    response = await run_query(
        query=case.query,
        top_k=6,
        filters=None,
        vector_store=vector_store,
        request_id=f"eval-{case.id}-{uuid.uuid4().hex[:8]}",
        stage_timings=stage_timings,
    )

    matched_docs = {
        (source.metadata or {}).get("source_name") for source in response.sources
    } & set(case.expected_docs)

    retrieval_ok = (
        len(matched_docs) >= case.min_expected_docs_hit if case.expected_docs else None
    )
    grounded_ok = response.grounded == case.expect_grounded
    flags_ok = (
        any(flag in response.guardrail_flags for flag in case.expect_flags_any)
        if case.expect_flags_any
        else True
    )

    return EvalResult(
        case=case,
        grounded=response.grounded,
        confidence=response.confidence,
        guardrail_flags=response.guardrail_flags,
        matched_docs=matched_docs,
        latency_ms=response.latency_ms,
        stage_timings=stage_timings,
        answer_word_count=len(response.answer.split()),
        answer=response.answer,
        retrieval_ok=retrieval_ok,
        grounded_ok=grounded_ok,
        flags_ok=flags_ok,
    )


async def run_evaluation(
    cases: list[EvalCase],
    vector_store: VectorStore,
) -> EvaluationReport:
    report = EvaluationReport()

    for case in cases:
        report.results.append(await evaluate_case(case, vector_store))

    return report
