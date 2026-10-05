"""Runs the EVAL_CASES dataset through the real pipeline (real embedding +
real LLM calls) and scores:

- retrieval   : doc hit, Recall@k, Precision@k, MRR, nDCG@k, heading hit
- answer      : groundedness, expected/forbidden keywords, length, and
                (opt-in) LLM-as-judge faithfulness / relevance / completeness
- guardrails  : expected flags
- multi-turn  : history is passed through; the follow-up heuristic is checked
- latency     : P50/P70/P100 total and per stage

What gates ``EvalResult.passed``
--------------------------------
Gated: groundedness, guardrail flags, doc-level retrieval hit, expected /
forbidden keywords, and (only when --judge is used) judge faithfulness.
Reported but NOT gated: heading hit, answer length, follow-up detection.
To gate one of those, add it to ``EvalResult.passed``.
"""

import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.stats import percentile
from app.evaluation import metrics as m
from app.evaluation.dataset import EvalCase
from app.evaluation.judge import JudgeScores, judge_answer
from app.harness.orchestrator import run_query
from app.retrieval.vector_store import VectorStore
from app.schemas.query import ChatTurn

STAGES = ("guardrail_pre", "retrieval", "llm", "guardrail_post")


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
    # --- added for the extended harness ---------------------------------
    retrieved: list[dict] = field(default_factory=list)
    retrieval_metrics: dict[str, float] = field(default_factory=dict)
    heading_ok: bool | None = None
    keywords_checked: bool = False
    keywords_ok: bool = True
    missing_keywords: list[str] = field(default_factory=list)
    forbidden_found: list[str] = field(default_factory=list)
    length_ok: bool | None = None  # None when there is no real LLM answer
    follow_up_detected: bool = False
    follow_up_ok: bool | None = None
    judge: JudgeScores | None = None
    judge_ok: bool | None = None

    @property
    def known_issue(self) -> str | None:
        return self.case.known_issue

    @property
    def passed(self) -> bool:
        return (
            self.grounded_ok
            and self.flags_ok
            and self.retrieval_ok is not False
            and self.keywords_ok
            and self.judge_ok is not False
        )

    def to_dict(self) -> dict:
        return {
            "id": self.case.id,
            "type": self.case.case_type,
            "query": self.case.query,
            "passed": self.passed,
            "known_issue": self.known_issue,
            "grounded": self.grounded,
            "confidence": self.confidence,
            "guardrail_flags": self.guardrail_flags,
            "matched_docs": sorted(self.matched_docs),
            "retrieved": self.retrieved,
            "retrieval_metrics": self.retrieval_metrics,
            "heading_ok": self.heading_ok,
            "missing_keywords": self.missing_keywords,
            "forbidden_found": self.forbidden_found,
            "length_ok": self.length_ok,
            "follow_up_detected": self.follow_up_detected,
            "follow_up_ok": self.follow_up_ok,
            "judge": self.judge.as_dict() if self.judge else None,
            "latency_ms": self.latency_ms,
            "answer_word_count": self.answer_word_count,
        }


def _rate(values: list[bool]) -> float | None:
    return (sum(1 for v in values if v) / len(values)) if values else None


@dataclass
class EvaluationReport:
    results: list[EvalResult] = field(default_factory=list)
    top_k: int = 6

    # Cases carrying a ``known_issue`` marker still run and are listed, but are
    # kept out of every quality aggregate below so a known bug can't hide (or
    # fake) a regression.
    @property
    def scored(self) -> list[EvalResult]:
        return [r for r in self.results if not r.known_issue]

    @property
    def known_issue_results(self) -> list[EvalResult]:
        return [r for r in self.results if r.known_issue]

    @property
    def pass_rate(self) -> float:
        scored = self.scored
        if not scored:
            return 0.0
        return sum(1 for r in scored if r.passed) / len(scored)

    @property
    def retrieval_recall(self) -> float:
        checked = [r for r in self.scored if r.retrieval_ok is not None]
        if not checked:
            return 1.0
        return sum(1 for r in checked if r.retrieval_ok) / len(checked)

    @property
    def groundedness_accuracy(self) -> float:
        scored = self.scored
        if not scored:
            return 0.0
        return sum(1 for r in scored if r.grounded_ok) / len(scored)

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

    def retrieval_means(self) -> dict[str, float]:
        """Mean of each retrieval metric over scored cases that have expected docs."""

        collected: dict[str, list[float]] = defaultdict(list)
        for r in self.scored:
            for name, value in r.retrieval_metrics.items():
                collected[name].append(value)
        return {name: sum(vs) / len(vs) for name, vs in collected.items()}

    def judge_means(self) -> dict[str, float]:
        judged = [r.judge for r in self.scored if r.judge is not None]
        if not judged:
            return {}
        n = len(judged)
        return {
            "n": n,
            "faithfulness": sum(j.faithfulness for j in judged) / n,
            "relevance": sum(j.relevance for j in judged) / n,
            "completeness": sum(j.completeness for j in judged) / n,
        }

    def by_case_type(self) -> dict[str, dict]:
        grouped: dict[str, list[EvalResult]] = defaultdict(list)
        for r in self.scored:
            grouped[r.case.case_type].append(r)
        return {
            case_type: {
                "n": len(rs),
                "passed": sum(1 for r in rs if r.passed),
                "pass_rate": sum(1 for r in rs if r.passed) / len(rs),
            }
            for case_type, rs in sorted(grouped.items())
        }

    def known_issue_status(self) -> list[dict]:
        return [
            {
                "id": r.case.id,
                "issue": r.known_issue,
                "passing_now": r.passed,  # True => bug fixed, remove the marker
            }
            for r in self.known_issue_results
        ]

    def summary(self) -> dict:
        scored = self.scored
        return {
            "n_cases": len(self.results),
            "n_scored": len(scored),
            "n_known_issues": len(self.known_issue_results),
            "top_k": self.top_k,
            "pass_rate": self.pass_rate,
            "retrieval_recall": self.retrieval_recall,
            "groundedness_accuracy": self.groundedness_accuracy,
            "retrieval": self.retrieval_means(),
            "heading_hit_rate": _rate(
                [r.heading_ok for r in scored if r.heading_ok is not None]
            ),
            "keyword_pass_rate": _rate(
                [r.keywords_ok for r in scored if r.keywords_checked]
            ),
            "length_ok_rate": _rate(
                [r.length_ok for r in scored if r.length_ok is not None]
            ),
            "follow_up_accuracy": _rate(
                [r.follow_up_ok for r in scored if r.follow_up_ok is not None]
            ),
            "judge": self.judge_means(),
            "by_case_type": self.by_case_type(),
            "known_issues": self.known_issue_status(),
            "latency": {
                "total": self.latency_percentiles(),
                **{stage: self.latency_percentiles(stage) for stage in STAGES},
            },
        }


def _retrieval_metrics(
    case: EvalCase,
    ranked_docs: list[str | None],
    top_k: int,
) -> dict[str, float]:
    flags = m.chunk_relevance(ranked_docs, [None] * len(ranked_docs), case.expected_docs)
    return {
        "recall@1": m.doc_recall_at_k(ranked_docs, case.expected_docs, 1, case.min_expected_docs_hit),
        "recall@3": m.doc_recall_at_k(ranked_docs, case.expected_docs, 3, case.min_expected_docs_hit),
        "recall@k": m.doc_recall_at_k(ranked_docs, case.expected_docs, top_k, case.min_expected_docs_hit),
        "precision@k": m.precision_at_k(flags, top_k),
        "mrr": m.reciprocal_rank(flags),
        "ndcg@k": m.ndcg_at_k(flags, top_k),
    }


async def evaluate_case(
    case: EvalCase,
    vector_store: VectorStore,
    top_k: int = 6,
    use_judge: bool = False,
    judge_min_faithfulness: int = 4,
) -> EvalResult:
    stage_timings: dict = {}
    settings = get_settings()

    history = [ChatTurn(role=role, content=content) for role, content in case.history]

    response = await run_query(
        query=case.query,
        top_k=top_k,
        filters=None,
        vector_store=vector_store,
        request_id=f"eval-{case.id}-{uuid.uuid4().hex[:8]}",
        stage_timings=stage_timings,
        history=history or None,
    )

    ranked_docs = [(s.metadata or {}).get("source_name") for s in response.sources]
    ranked_headings = [(s.metadata or {}).get("section_heading") for s in response.sources]

    matched_docs = set(ranked_docs) & set(case.expected_docs)

    retrieval_ok = (
        len(matched_docs) >= case.min_expected_docs_hit if case.expected_docs else None
    )
    grounded_ok = response.grounded == case.expect_grounded
    flags_ok = (
        any(flag in response.guardrail_flags for flag in case.expect_flags_any)
        if case.expect_flags_any
        else True
    )

    # --- retrieval ranking metrics (only when ground truth exists) ---------
    retrieval_metrics: dict[str, float] = {}
    heading_ok: bool | None = None
    if case.expected_docs:
        retrieval_metrics = _retrieval_metrics(case, ranked_docs, top_k)
        if case.expected_headings:
            heading_flags = m.chunk_relevance(
                ranked_docs, ranked_headings, case.expected_docs, case.expected_headings
            )
            heading_ok = any(heading_flags)

    # --- answer text checks: only on a real, grounded answer ---------------
    has_real_answer = bool(response.grounded and response.answer.strip())
    missing: list[str] = []
    forbidden: list[str] = []
    keywords_checked = False
    if has_real_answer and (case.expected_keywords or case.forbidden_keywords):
        keywords_checked = True
        missing = m.missing_keywords(response.answer, case.expected_keywords)
        forbidden = m.forbidden_found(response.answer, case.forbidden_keywords)
    keywords_ok = not missing and not forbidden

    word_count = m.word_count(response.answer)
    length_ok = (
        word_count <= settings.answer_max_words
        if has_real_answer and response.sources
        else None
    )

    follow_up_ok = (
        response.follow_up_detected == case.expect_follow_up_detected
        if case.expect_follow_up_detected is not None
        else None
    )

    # --- optional LLM-as-judge (skips canned/fallback replies) -------------
    judge: JudgeScores | None = None
    judge_ok: bool | None = None
    if use_judge and has_real_answer and response.sources and case.expect_grounded:
        judge = await judge_answer(
            case.query,
            [s.content for s in response.sources],
            response.answer,
            key_facts=[k.replace("|", " / ") for k in case.expected_keywords] or None,
        )
        if judge is not None:
            judge_ok = judge.faithfulness >= judge_min_faithfulness

    return EvalResult(
        case=case,
        grounded=response.grounded,
        confidence=response.confidence,
        guardrail_flags=response.guardrail_flags,
        matched_docs=matched_docs,
        latency_ms=response.latency_ms,
        stage_timings=stage_timings,
        answer_word_count=word_count,
        answer=response.answer,
        retrieval_ok=retrieval_ok,
        grounded_ok=grounded_ok,
        flags_ok=flags_ok,
        retrieved=[
            {
                "source_name": doc,
                "section_heading": heading,
                "score": round(source.score, 4),
            }
            for doc, heading, source in zip(ranked_docs, ranked_headings, response.sources)
        ],
        retrieval_metrics=retrieval_metrics,
        heading_ok=heading_ok,
        keywords_checked=keywords_checked,
        keywords_ok=keywords_ok,
        missing_keywords=missing,
        forbidden_found=forbidden,
        length_ok=length_ok,
        follow_up_detected=response.follow_up_detected,
        follow_up_ok=follow_up_ok,
        judge=judge,
        judge_ok=judge_ok,
    )


async def run_evaluation(
    cases: list[EvalCase],
    vector_store: VectorStore,
    top_k: int = 6,
    use_judge: bool = False,
    judge_min_faithfulness: int = 4,
    on_result: Callable[[EvalResult], None] | None = None,
) -> EvaluationReport:
    report = EvaluationReport(top_k=top_k)

    for case in cases:
        result = await evaluate_case(
            case,
            vector_store,
            top_k=top_k,
            use_judge=use_judge,
            judge_min_faithfulness=judge_min_faithfulness,
        )
        report.results.append(result)
        if on_result is not None:
            on_result(result)

    return report