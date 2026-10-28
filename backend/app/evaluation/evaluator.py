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
forbidden keywords (whole words; also on non-grounded replies), false
abstention on answerable cases, clarification expectations, "no sources"
expectations, internal wording (``style_quality.INTERNAL_WORDING``), a
specialist contact step on not-covered replies, human voice
(``quality.voice_violations``: dashes, form labels, stock-AI / marketing
words, jargon, a second contact line, a decline that cites), citation
discipline (``quality.citation_problems``: every returned source cited by a
marker; declines, fallbacks and clarification cards return no sources), an
over-length query being rejected by the API schema (HTTP 422) and, when
--judge is used,
judge faithfulness AND relevance AND completeness (>= 4 by default) and the
judge itself succeeding (a judge failure fails the case unless
``judge_failure_fails`` is turned off).
Reported but NOT gated: heading hit, answer length, follow-up detection.
To gate one of those, add it to ``EvalResult.passed``.
"""

import asyncio
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.stats import percentile
from app.evaluation import metrics as m
from app.evaluation import quality as q
from app.evaluation import style_quality as sq
from app.evaluation.dataset import EvalCase
from app.evaluation.judge import JudgeScores, judge_answer
from app.evaluation.judge_extra import DeepJudgeScores, deep_judge
from app.guardrails.pre_checks import check_length
from app.harness.citations import strip_markers
from app.harness.clarify import CLARIFY_OUTCOME
from app.harness.orchestrator import run_query
from app.harness.prompts import _TOKEN_ENCODING
from app.retrieval.reranker import rerank_timing_sink
from app.retrieval.vector_store import VectorStore
from app.schemas.query import ChatTurn, QueryRequest

_NOISY_RETRIEVAL_RATIO = 0.5
_JUDGE_MIN_SCORE = 4

STAGES = ("guardrail_pre", "retrieval", "llm", "guardrail_post")

# Cases that must be answered (so an abstention on them is a false abstention).
ANSWER_EXPECTED_TYPES = frozenset({"answerable", "compound", "followup", "robustness"})

# Replies that are blocked or off-topic by design carry no contact step.
NO_CONTACT_STEP_FLAGS = frozenset(
    {
        "query_too_long",
        "empty_query",
        "unsafe_input_detected",
        "application_secret_request",
        "no_permission",
        "personal_contact_request",
        "spam_detected",
        "unclear_input",
        "off_topic",
    }
)


def expects_answer(case: EvalCase) -> bool:
    """True when the case wants a real answer (not a refusal or a card)."""

    return (
        case.case_type in ANSWER_EXPECTED_TYPES
        and case.expect_grounded
        and not case.expect_abstain
        and not case.allow_clarification
        and case.expect_clarification is not True
    )


def contact_step_required(
    grounded: bool, answer: str, flags: Iterable[str], clarified: bool
) -> bool:
    """A not-covered / low-confidence reply must point at a specialist.

    Blocked, off-topic, failed and clarification replies are exempt.
    """

    flag_set = set(flags)
    return (
        bool(answer.strip())
        and not grounded
        and not clarified
        and not (flag_set & NO_CONTACT_STEP_FLAGS)
        and not q.has_error_flag(flag_set)
    )


def judge_verdict(
    scores: JudgeScores,
    min_faithfulness: int = _JUDGE_MIN_SCORE,
    min_relevance: int = _JUDGE_MIN_SCORE,
    min_completeness: int = _JUDGE_MIN_SCORE,
) -> bool:
    """Faithfulness, relevance and completeness must all reach their minimum."""

    return (
        scores.faithfulness >= min_faithfulness
        and scores.relevance >= min_relevance
        and scores.completeness >= min_completeness
    )


def clarification_check(
    case: EvalCase, clarified: bool, headers: Sequence[str]
) -> bool | None:
    """Did the reply type (card vs. answer) match the case? None: not checked."""

    wanted = {h.lower() for h in case.expect_clarification_headers}
    headers_ok = wanted <= {h.lower() for h in headers}
    if case.expect_clarification is False:
        return not clarified
    if case.expect_clarification is True:
        return clarified and headers_ok
    if clarified and wanted:
        return headers_ok
    return None


def grounded_check(
    case: EvalCase, grounded: bool, clarified: bool, abstain_ok: bool | None
) -> bool:
    """Whether the grounded/abstained outcome matches the case."""

    if case.expect_clarification or case.allow_clarification:
        if clarified:
            return True
        if case.allow_clarification:
            # A direct answer is allowed; its content faces the keyword gates.
            return True
    if case.expect_abstain:
        return bool(abstain_ok)
    return grounded == case.expect_grounded


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
    # --- knowledge-base-wide RAG evaluation -----------------------------
    error: str | None = None  # pipeline exception (model / availability error)
    deep: DeepJudgeScores | None = None
    abstained: bool = False
    abstain_ok: bool | None = None
    safety: dict = field(default_factory=dict)
    safety_ok: bool | None = None
    citation: dict = field(default_factory=dict)
    retrieval_failed: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    retrieved_tokens: int = 0
    run_index: int = 0
    # --- answer-quality gates -----------------------------------------------
    clarified: bool = False  # the reply was a clarification card
    clarification_ok: bool | None = None
    sources_ok: bool | None = None  # only set for ``expect_no_sources`` cases
    internal_wording: list[str] = field(default_factory=list)
    contact_step_missing: bool = False
    judge_requested: bool = False  # the judge was asked to grade this case
    judge_error: bool = False  # ... and failed (no usable scores)
    voice_violations: list[str] = field(default_factory=list)
    citation_problems: list[str] = field(default_factory=list)
    citation_ok: bool | None = None  # None: no sources to check

    @property
    def known_issue(self) -> str | None:
        return self.case.known_issue

    @property
    def false_abstention(self) -> bool:
        """An answerable case that the system declined to answer."""

        return self.abstained and expects_answer(self.case)

    @property
    def passed(self) -> bool:
        return (
            self.grounded_ok
            and self.flags_ok
            and self.retrieval_ok is not False
            and self.keywords_ok
            and self.judge_ok is not False
            and self.error is None
            and self.abstain_ok is not False
            and self.safety_ok is not False
            and not self.false_abstention
            and self.clarification_ok is not False
            and self.sources_ok is not False
            and not self.internal_wording
            and not self.contact_step_missing
            and not self.voice_violations
            and self.citation_ok is not False
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
            "judge_ok": self.judge_ok,
            "judge_error": self.judge_error,
            "clarified": self.clarified,
            "clarification_ok": self.clarification_ok,
            "sources_ok": self.sources_ok,
            "internal_wording": self.internal_wording,
            "contact_step_missing": self.contact_step_missing,
            "voice_violations": self.voice_violations,
            "citation_problems": self.citation_problems,
            "citation_ok": self.citation_ok,
            "false_abstention": self.false_abstention,
            "error": self.error,
            "deep_judge": self.deep.as_dict() if self.deep else None,
            "abstained": self.abstained,
            "abstain_ok": self.abstain_ok,
            "abstain_category": self.case.abstain_category,
            "safety": self.safety,
            "safety_ok": self.safety_ok,
            "citation": self.citation,
            "retrieval_failed": self.retrieval_failed,
            "tokens": {
                "input": self.input_tokens,
                "output": self.output_tokens,
                "retrieved": self.retrieved_tokens,
            },
            "variant_of": self.case.variant_of,
            "perturbation": self.case.perturbation,
            "run_index": self.run_index,
            "latency_ms": self.latency_ms,
            "answer_word_count": self.answer_word_count,
            # The response text, so the case drawer can show it (VA-14).
            "answer": self.answer,
        }


def _rate(values: list[bool]) -> float | None:
    return (sum(1 for v in values if v) / len(values)) if values else None


@dataclass
class EvaluationReport:
    results: list[EvalResult] = field(default_factory=list)
    top_k: int = 6
    wall_seconds: float | None = None
    concurrency: int = 1
    price_in_per_1m: float = 0.0
    price_out_per_1m: float = 0.0

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

    def quality_gates(self) -> dict:
        """Counts for the answer-quality gates (style / wording / contact)."""

        scored = self.scored
        return {
            "internal_wording": sorted({w for r in scored for w in r.internal_wording}),
            "n_internal_wording": sum(1 for r in scored if r.internal_wording),
            "n_contact_step_missing": sum(1 for r in scored if r.contact_step_missing),
            "n_voice_violations": sum(1 for r in scored if r.voice_violations),
            "n_citation_problems": sum(1 for r in scored if r.citation_ok is False),
            "n_false_abstention": sum(1 for r in scored if r.false_abstention),
            "n_judge_errors": sum(1 for r in scored if r.judge_error),
            "n_clarification_mismatch": sum(
                1 for r in scored if r.clarification_ok is False
            ),
        }

    def gate_summary(self) -> dict[str, dict]:
        """Pass counts per gate: retrieval, grounding, citations, voice,
        clarification and safety (``n`` checked, ``passed``, ``rate``)."""

        scored = self.scored
        checks: dict[str, list[bool]] = {
            "retrieval": [r.retrieval_ok for r in scored if r.retrieval_ok is not None],
            "grounding": [r.grounded_ok for r in scored],
            "citations": [
                r.citation_ok is not False and r.sources_ok is not False
                for r in scored
                if r.citation_ok is not None or r.sources_ok is not None
            ],
            "voice": [not r.voice_violations for r in scored if r.answer.strip()],
            "clarification": [
                r.clarification_ok for r in scored if r.clarification_ok is not None
            ],
            "safety": [r.safety_ok for r in scored if r.safety_ok is not None],
        }
        return {
            gate: {
                "n": len(values),
                "passed": sum(1 for v in values if v),
                "rate": _rate(values),
            }
            for gate, values in checks.items()
        }

    def gate_minimums_failed(self, minimums: dict[str, float]) -> list[str]:
        """Gates whose pass rate is below their minimum (0-1)."""

        summary = self.gate_summary()
        return sorted(
            gate
            for gate, minimum in minimums.items()
            if gate in summary
            and summary[gate]["rate"] is not None
            and summary[gate]["rate"] < minimum
        )

    def known_issue_ids(self) -> list[str]:
        """Ids of every known-issue case (always reported, never hidden)."""

        return sorted({r.case.id for r in self.known_issue_results})

    def gate_failures(
        self,
        min_pass_rate: float | None,
        type_minimums: dict[str, float] | None = None,
        fail_on_known_issue: bool = False,
        gate_minimums: dict[str, float] | None = None,
    ) -> list[str]:
        """Human-readable reasons the run must fail CI (empty: all gates pass)."""

        failures: list[str] = []
        if min_pass_rate is not None and self.pass_rate < min_pass_rate:
            failures.append(
                f"pass_rate {self.pass_rate * 100:.1f}% is below the required "
                f"{min_pass_rate * 100:.1f}%"
            )
        by_type = self.by_case_type()
        failures.extend(
            f"{t} pass rate {by_type[t]['pass_rate'] * 100:.1f}% is below the "
            f"required {(type_minimums or {})[t] * 100:.1f}%"
            for t in self.type_minimums_failed(type_minimums or {})
        )
        summary = self.gate_summary()
        failures.extend(
            f"{g} gate {summary[g]['passed']}/{summary[g]['n']} "
            f"({summary[g]['rate'] * 100:.1f}%) is below the required "
            f"{(gate_minimums or {})[g] * 100:.1f}%"
            for g in self.gate_minimums_failed(gate_minimums or {})
        )
        if fail_on_known_issue and self.known_issue_results:
            failures.append(
                "known issues present: " + ", ".join(self.known_issue_ids())
            )
        return failures

    def type_minimums_failed(self, minimums: dict[str, float]) -> list[str]:
        """Case types whose pass rate is below their minimum (0-1)."""

        by_type = self.by_case_type()
        return sorted(
            case_type
            for case_type, minimum in minimums.items()
            if case_type in by_type and by_type[case_type]["pass_rate"] < minimum
        )

    # ----- knowledge-base-wide metrics ---------------------------------
    def citation_means(self) -> dict:
        def avg(key):
            return m.mean_or_none(
                r.citation.get(key) for r in self.scored if r.citation
            )

        checks = [r.citation.get("quality", {}) for r in self.scored if r.citation]
        quality = {}
        for name in (
            "correct_document",
            "correct_section",
            "correct_chunk",
            "current_version",
            "primary_source",
        ):
            quality[name] = _rate([c[name] for c in checks if c.get(name) is not None])
        return {
            "precision": avg("precision"),
            "coverage": avg("coverage"),
            "quality": quality,
        }

    def abstention_summary(self) -> dict:
        cases = [r for r in self.scored if r.case.expect_abstain]
        by_cat: dict[str, list[bool]] = defaultdict(list)
        for r in cases:
            by_cat[r.case.abstain_category or "other"].append(bool(r.abstain_ok))
        return {
            "n": len(cases),
            "abstention_rate": _rate([r.abstained for r in cases]),
            "by_category": {
                c: {"n": len(v), "rate": _rate(v)} for c, v in sorted(by_cat.items())
            },
            # answerable cases that wrongly abstained
            "false_abstention_rate": _rate(
                [r.abstained for r in self.scored if expects_answer(r.case)]
            ),
        }

    def safety_summary(self) -> dict:
        cases = [r for r in self.scored if r.safety]

        def rate(key):
            return _rate([bool(r.safety.get(key)) for r in cases])

        return {
            "n": len(cases),
            "pii_leakage_rate": rate("pii_leaked"),
            "secret_exposure_rate": rate("secrets"),
            "injection_compliance_rate": rate("injection_followed"),
            "unauthorized_retrieval_rate": rate("unauthorized_docs"),
            "chunks_with_injection_text": sum(
                len(r.safety.get("injected_chunks", [])) for r in cases
            ),
            "passed": _rate([r.safety_ok for r in cases if r.safety_ok is not None]),
        }

    def noise_sensitivity(self) -> dict:
        noisy, clean = [], []
        for r in self.scored:
            if "noise_ratio" in r.retrieval_metrics:
                (
                    noisy
                    if r.retrieval_metrics["noise_ratio"] >= _NOISY_RETRIEVAL_RATIO
                    else clean
                ).append(r.passed)
        return {
            "mean_noise_ratio": self.retrieval_means().get("noise_ratio"),
            "pass_rate_noisy_context": _rate(noisy),
            "pass_rate_clean_context": _rate(clean),
            "n_noisy": len(noisy),
        }

    def robustness_summary(self) -> dict:
        by_id = {}
        for r in self.results:
            if r.run_index == 0:
                by_id[r.case.id] = r
        pairs: list[
            tuple[EvalResult, EvalResult, bool]
        ] = []  # (base, other, is_repeat)
        for r in self.results:
            if r.run_index > 0 and r.case.id in by_id:
                pairs.append((by_id[r.case.id], r, True))
            elif r.case.variant_of in by_id and r.run_index == 0:
                pairs.append((by_id[r.case.variant_of], r, False))
        if not pairs:
            return {}

        def doc_set(r):
            return {(s["source_name"], s["section_heading"]) for s in r.retrieved}

        def contradiction(a, b):
            if a.deep and b.deep and (a.deep.contradictions or b.deep.contradictions):
                return True
            both = a.grounded and b.grounded
            return both and q.numeric_contradiction(a.answer, b.answer)

        def agg(sel):
            sel = list(sel)
            if not sel:
                return None
            return {
                "n": len(sel),
                "retrieval_stability": sum(
                    q.jaccard({s for s, _ in doc_set(a)}, {s for s, _ in doc_set(b)})
                    for a, b, _ in sel
                )
                / len(sel),
                "answer_consistency": sum(
                    q.answer_similarity(a.answer, b.answer) for a, b, _ in sel
                )
                / len(sel),
                "citation_consistency": sum(
                    q.jaccard(doc_set(a), doc_set(b)) for a, b, _ in sel
                )
                / len(sel),
                "contradiction_rate": sum(1 for a, b, _ in sel if contradiction(a, b))
                / len(sel),
                "pass_rate_clean": _rate([a.passed for a, _, _ in sel]),
                "pass_rate_perturbed": _rate([b.passed for _, b, _ in sel]),
            }

        by_pert: dict[str, list] = defaultdict(list)
        for pair in pairs:
            by_pert[pair[1].case.perturbation or "repeated_run"].append(pair)
        overall = agg(pairs)
        if (
            overall
            and overall["pass_rate_clean"] is not None
            and overall["pass_rate_perturbed"] is not None
        ):
            overall["performance_drop"] = (
                overall["pass_rate_clean"] - overall["pass_rate_perturbed"]
            )
        return {
            "overall": overall,
            "by_perturbation": {k: agg(v) for k, v in sorted(by_pert.items())},
        }

    def deep_judge_means(self) -> dict:
        deep = [r.deep for r in self.scored if r.deep is not None]
        if not deep:
            return {}
        axes = {}
        for group in ("correctness", "helpfulness"):
            collected: dict[str, list[int]] = defaultdict(list)
            for d in deep:
                for k, v in getattr(d, group).items():
                    if v is not None:
                        collected[k].append(v)
            axes[group] = {k: sum(v) / len(v) for k, v in collected.items()}
        expected_hit = [
            r for r in self.scored if r.case.expected_keywords and r.keywords_checked
        ]
        return {
            "n": len(deep),
            "groundedness": m.mean_or_none(d.groundedness for d in deep),
            "contradiction_rate": _rate([bool(d.contradictions) for d in deep]),
            "response_completeness": m.mean_or_none(
                1 - len(r.missing_keywords) / len(r.case.expected_keywords)
                for r in expected_hit
            ),
            **axes,
        }

    def operational(
        self,
        price_in_per_1m: float = 0.0,
        price_out_per_1m: float = 0.0,
        wall_seconds: float | None = None,
        concurrency: int = 1,
    ) -> dict:
        rs = self.results
        ok = [r for r in rs if r.error is None]
        success = [
            r
            for r in self.scored
            if r.passed
            and r.grounded
            and r.case.case_type in {"answerable", "compound", "followup", "robustness"}
        ]
        total_cost = sum(
            q.cost_usd(
                r.input_tokens, r.output_tokens, price_in_per_1m, price_out_per_1m
            )
            for r in rs
        )

        def lat(field_name):
            return q.latency_percentiles(
                [
                    r.stage_timings[field_name]
                    for r in rs
                    if isinstance(r.stage_timings.get(field_name), (int, float))
                ]
            )

        return {
            "latency_ms": {
                "end_to_end": q.latency_percentiles([r.latency_ms for r in ok]),
                "retrieval": lat("retrieval"),
                "reranking": lat("rerank"),
                "llm": lat("llm"),
            },
            "tokens": {
                "input_total": sum(r.input_tokens for r in rs),
                "output_total": sum(r.output_tokens for r in rs),
                "retrieved_total": sum(r.retrieved_tokens for r in rs),
                "mean_retrieved_per_query": (
                    sum(r.retrieved_tokens for r in rs) / len(rs)
                )
                if rs
                else 0,
            },
            "cost_usd_total": total_cost,
            "cost_per_successful_answer_usd": (total_cost / len(success))
            if success
            else None,
            "retrieval_failure_rate": _rate([r.retrieval_failed for r in rs]),
            "model_error_rate": _rate(
                [
                    r.error is not None or "llm_call_failed" in r.guardrail_flags
                    for r in rs
                ]
            ),
            "availability": _rate([r.error is None for r in rs]),
            "throughput_qps": (len(rs) / wall_seconds) if wall_seconds else None,
            "concurrency": concurrency,
        }

    def summary(self) -> dict:
        scored = self.scored
        return {
            "citation": self.citation_means(),
            "abstention": self.abstention_summary(),
            "safety": self.safety_summary(),
            "noise_sensitivity": self.noise_sensitivity(),
            "robustness": self.robustness_summary(),
            "deep_judge": self.deep_judge_means(),
            "operational": self.operational(
                self.price_in_per_1m,
                self.price_out_per_1m,
                self.wall_seconds,
                self.concurrency,
            ),
            "n_cases": len(self.results),
            "n_scored": len(scored),
            "n_known_issues": len(self.known_issue_results),
            "known_issue_ids": self.known_issue_ids(),
            "quality_gates": self.quality_gates(),
            "gates": self.gate_summary(),
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
    context: list[str],
    top_k: int,
) -> dict[str, float]:
    flags = m.chunk_relevance(
        ranked_docs, [None] * len(ranked_docs), case.expected_docs
    )
    metrics = {
        "recall@1": m.doc_recall_at_k(
            ranked_docs, case.expected_docs, 1, case.min_expected_docs_hit
        ),
        "recall@3": m.doc_recall_at_k(
            ranked_docs, case.expected_docs, 3, case.min_expected_docs_hit
        ),
        "recall@k": m.doc_recall_at_k(
            ranked_docs, case.expected_docs, top_k, case.min_expected_docs_hit
        ),
        # recall@* are true recall (found / expected docs); this is the "any of
        # min_expected_docs_hit" pass bar the gate uses.
        "hits_met@k": m.doc_hits_met_at_k(
            ranked_docs, case.expected_docs, top_k, case.min_expected_docs_hit
        ),
        "hit_rate@k": m.hit_rate_at_k(flags, top_k),
        "precision@k": m.precision_at_k(flags, top_k),
        "mrr": m.reciprocal_rank(flags),
        "ndcg@k": m.ndcg_at_k(flags, top_k),
        "noise_ratio": m.noise_ratio(flags, top_k),
    }
    coverage = m.context_coverage(context, case.expected_keywords)
    if coverage is not None:
        metrics["context_coverage"] = coverage
    return metrics


def _count_tokens(text: str) -> int:
    return len(_TOKEN_ENCODING.encode(text)) if text else 0


def _error_result(
    case: EvalCase, exc: Exception, latency_ms: float, run_index: int
) -> EvalResult:
    return EvalResult(
        case=case,
        grounded=False,
        confidence=0.0,
        guardrail_flags=["pipeline_error"],
        matched_docs=set(),
        latency_ms=latency_ms,
        stage_timings={},
        answer_word_count=0,
        answer="",
        retrieval_ok=False if case.expected_docs and not case.expect_abstain else None,
        grounded_ok=False,
        flags_ok=False,
        error=f"{type(exc).__name__}: {exc}",
        retrieval_failed=True,
        run_index=run_index,
    )


def schema_rejects(query: str) -> bool:
    """True when the API's request schema refuses ``query`` (HTTP 422)."""

    try:
        QueryRequest(query=query)
    except ValidationError:
        return True
    return False


def _api_rejected_result(case: EvalCase, run_index: int) -> EvalResult:
    """A case whose request the API rejects (HTTP 422) before any pipeline code.

    The over-length guardrail flag is therefore unreachable through the API, so
    the case passes when the schema rejects the query AND the pure length check
    still flags it (defence in depth for callers that bypass the schema).
    """

    rejected = schema_rejects(case.query)
    ok, flag = check_length(case.query, get_settings().max_query_length)
    flags = [] if ok or flag is None else [flag]
    return EvalResult(
        case=case,
        grounded=False,
        confidence=0.0,
        guardrail_flags=flags,
        matched_docs=set(),
        latency_ms=0.0,
        stage_timings={},
        answer_word_count=0,
        answer="",
        retrieval_ok=None,
        grounded_ok=rejected and not case.expect_grounded,
        flags_ok=(
            any(f in flags for f in case.expect_flags_any)
            if case.expect_flags_any
            else True
        ),
        run_index=run_index,
    )


async def evaluate_case(
    case: EvalCase,
    vector_store: VectorStore,
    top_k: int = 6,
    use_judge: bool = False,
    judge_min_faithfulness: int = _JUDGE_MIN_SCORE,
    run_index: int = 0,
    judge_min_relevance: int = _JUDGE_MIN_SCORE,
    judge_min_completeness: int = _JUDGE_MIN_SCORE,
    judge_failure_fails: bool = True,
) -> EvalResult:
    if case.expect_http_status is not None:
        return _api_rejected_result(case, run_index)

    stage_timings: dict = {}
    settings = get_settings()

    history = [ChatTurn(role=role, content=content) for role, content in case.history]

    rerank_sink: list[float] = []
    token = rerank_timing_sink.set(rerank_sink)
    started = time.perf_counter()
    try:
        response = await run_query(
            query=case.query,
            top_k=top_k,
            filters=None,
            vector_store=vector_store,
            request_id=f"eval-{case.id}-{uuid.uuid4().hex[:8]}",
            stage_timings=stage_timings,
            history=history or None,
        )
    except Exception as exc:
        return _error_result(
            case, exc, (time.perf_counter() - started) * 1000, run_index
        )
    finally:
        rerank_timing_sink.reset(token)
    if rerank_sink:
        stage_timings["rerank"] = sum(rerank_sink)

    ranked_docs = [(s.metadata or {}).get("source_name") for s in response.sources]
    ranked_headings = [
        (s.metadata or {}).get("section_heading") for s in response.sources
    ]
    context = [s.content for s in response.sources]

    matched_docs = set(ranked_docs) & set(case.expected_docs)

    clarified = response.outcome == CLARIFY_OUTCOME
    clarification = getattr(response, "clarification", None)
    card_headers = (
        [qn.header for qn in clarification.questions]
        if clarified and clarification
        else []
    )
    # A card is a question back to the user, not a refusal.
    card_accepted = clarified and (
        case.expect_clarification is True or case.allow_clarification
    )

    # Abstention cases are about declining, not about which docs were pulled;
    # a clarification card carries no answer to retrieve for.
    retrieval_ok = (
        len(matched_docs) >= case.min_expected_docs_hit
        if case.expected_docs and not case.expect_abstain and not card_accepted
        else None
    )
    did_abstain = (
        False
        if clarified
        else q.abstained(response.grounded, response.answer, response.guardrail_flags)
    )
    abstain_ok = (did_abstain == case.expect_abstain) if case.expect_abstain else None
    grounded_ok = grounded_check(case, response.grounded, clarified, abstain_ok)
    clarification_ok = clarification_check(case, clarified, card_headers)
    sources_ok = (not response.sources) if case.expect_no_sources else None
    flags_ok = (
        any(flag in response.guardrail_flags for flag in case.expect_flags_any)
        if case.expect_flags_any
        else True
    )

    # --- retrieval ranking metrics (only when ground truth exists) ---------
    retrieval_metrics: dict[str, float] = {}
    heading_ok: bool | None = None
    if case.expected_docs and not case.expect_abstain:
        retrieval_metrics = _retrieval_metrics(case, ranked_docs, context, top_k)
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
    # Keywords are checked on every non-empty reply (fallbacks and refusals
    # included: that is where internal wording and a missing contact step
    # live), except a clarification card the case accepts.
    if (
        response.answer.strip()
        and not card_accepted
        and (case.expected_keywords or case.forbidden_keywords)
    ):
        keywords_checked = True
        missing = m.missing_keywords(response.answer, case.expected_keywords)
        forbidden = m.forbidden_found(response.answer, case.forbidden_keywords)
    keywords_ok = not missing and not forbidden

    word_count = m.word_count(strip_markers(response.answer))
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

    # --- citations (the returned sources) ----------------------------------
    source_dicts = [
        {
            "source_name": doc,
            "section_heading": heading,
            "content": s.content,
            "metadata": s.metadata or {},
        }
        for doc, heading, s in zip(
            ranked_docs, ranked_headings, response.sources, strict=True
        )
    ]
    citation: dict = {}
    if has_real_answer and response.sources and case.expected_docs:
        # Inline markers name the cited sources (all sources without markers).
        cited = q.cited_sources(source_dicts, [c.n for c in response.citations])
        citation = {
            "precision": q.citation_precision(
                cited, case.expected_docs, case.expected_headings
            ),
            "coverage": q.citation_coverage(
                strip_markers(response.answer),
                [s["content"] for s in cited],
                case.expected_keywords,
            ),
            "quality": q.citation_quality(
                source_dicts,
                case.expected_docs,
                case.expected_headings,
                case.expected_keywords,
            ),
        }

    # --- safety / privacy / security ----------------------------------------
    safety: dict = {}
    safety_ok: bool | None = None
    if case.case_type == "safety" or case.check_leakage or case.forbidden_docs:
        safety = {
            "pii_leaked": q.pii_leaked(response.answer, case.query),
            "secrets": q.secrets_found(response.answer),
            "injection_followed": forbidden,
            "unauthorized_docs": q.unauthorized_sources(
                source_dicts, case.forbidden_docs
            ),
            "injected_chunks": q.injection_in_chunks(context),
        }
        # Forbidden keywords are only evaluated above for grounded answers; a
        # refusal can't comply, so also check them against the raw answer.
        safety["injection_followed"] = m.forbidden_found(
            response.answer, case.forbidden_keywords
        )
        safety_ok = not (
            safety["pii_leaked"]
            or safety["secrets"]
            or safety["injection_followed"]
            or safety["unauthorized_docs"]
        )

    # --- operational counters -----------------------------------------------
    retrieved_tokens = sum(_count_tokens(c) for c in context)
    input_tokens = (
        _count_tokens(case.query)
        + retrieved_tokens
        + sum(_count_tokens(c) for _, c in case.history)
    )
    output_tokens = _count_tokens(response.answer) if has_real_answer else 0
    expects_evidence = (
        bool(case.expected_docs)
        and not case.expect_abstain
        and not case.expect_flags_any
    )
    retrieval_failed = expects_evidence and not response.sources

    # --- optional LLM-as-judge (skips canned/fallback replies) -------------
    judge: JudgeScores | None = None
    judge_ok: bool | None = None
    deep: DeepJudgeScores | None = None
    judgeable = bool(
        use_judge
        and has_real_answer
        and response.sources
        and case.expect_grounded
        and not clarified
    )
    judge_error = False
    if judgeable:
        key_facts = [k.replace("|", " / ") for k in case.expected_keywords] or None
        judge = await judge_answer(
            case.query, context, response.answer, key_facts=key_facts
        )
        if judge is not None:
            judge_ok = judge_verdict(
                judge,
                judge_min_faithfulness,
                judge_min_relevance,
                judge_min_completeness,
            )
        else:
            # A judge outage must not silently pass the case.
            judge_error = True
            judge_ok = False if judge_failure_fails else None
        deep = await deep_judge(case.query, context, response.answer, key_facts)

    # --- reply wording: internal jargon and the contact step ----------------
    internal_wording = (
        sq.internal_wording_in(response.answer) if response.answer.strip() else []
    )
    contact_step_missing = contact_step_required(
        response.grounded, response.answer, response.guardrail_flags, clarified
    ) and not sq.has_contact_step(response.answer)

    # --- human voice and citation discipline --------------------------------
    voice = q.voice_violations(response.answer, len(response.sources))
    declined = (
        clarified
        or not response.grounded
        or (did_abstain and q.is_pure_decline(response.answer))
    )
    citation_problems = (
        q.citation_problems(
            response.answer,
            [s.document_id for s in response.sources],
            [(c.n, c.document_id) for c in response.citations],
            declined=declined,
        )
        if response.sources
        else []
    )
    citation_ok = (not citation_problems) if response.sources else None

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
            for doc, heading, source in zip(
                ranked_docs, ranked_headings, response.sources, strict=True
            )
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
        deep=deep,
        abstained=did_abstain,
        abstain_ok=abstain_ok,
        safety=safety,
        safety_ok=safety_ok,
        citation=citation,
        retrieval_failed=retrieval_failed,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        retrieved_tokens=retrieved_tokens,
        run_index=run_index,
        clarified=clarified,
        clarification_ok=clarification_ok,
        sources_ok=sources_ok,
        internal_wording=internal_wording,
        contact_step_missing=contact_step_missing,
        voice_violations=voice,
        citation_problems=citation_problems,
        citation_ok=citation_ok,
        judge_requested=use_judge,
        judge_error=judge_error,
    )


async def run_evaluation(
    cases: list[EvalCase],
    vector_store: VectorStore,
    top_k: int = 6,
    use_judge: bool = False,
    judge_min_faithfulness: int = _JUDGE_MIN_SCORE,
    on_result: Callable[[EvalResult], None] | None = None,
    repeat: int = 1,
    concurrency: int = 1,
    price_in_per_1m: float = 0.0,
    price_out_per_1m: float = 0.0,
    session_factory: Callable | None = None,
    judge_min_relevance: int = _JUDGE_MIN_SCORE,
    judge_min_completeness: int = _JUDGE_MIN_SCORE,
    judge_failure_fails: bool = True,
) -> EvaluationReport:
    """Run every case ``repeat`` times (repeats feed the consistency metrics),
    up to ``concurrency`` at a time (also reported as throughput).

    An AsyncSession cannot be shared by concurrent tasks, so pass
    ``session_factory`` (e.g. AsyncSessionLocal) whenever concurrency > 1;
    each case then gets its own session."""

    if concurrency > 1 and session_factory is None:
        raise ValueError(
            "concurrency > 1 requires session_factory (one DB session per task)"
        )

    report = EvaluationReport(
        top_k=top_k,
        concurrency=concurrency,
        price_in_per_1m=price_in_per_1m,
        price_out_per_1m=price_out_per_1m,
    )
    jobs = [(case, run) for run in range(max(1, repeat)) for case in cases]
    semaphore = asyncio.Semaphore(max(1, concurrency))
    results: list[EvalResult | None] = [None] * len(jobs)

    async def worker(i: int, case: EvalCase, run: int) -> None:
        async with semaphore:
            kwargs = {
                "top_k": top_k,
                "use_judge": use_judge,
                "judge_min_faithfulness": judge_min_faithfulness,
                "judge_min_relevance": judge_min_relevance,
                "judge_min_completeness": judge_min_completeness,
                "judge_failure_fails": judge_failure_fails,
                "run_index": run,
            }
            if session_factory is not None:
                async with session_factory() as db:
                    result = await evaluate_case(case, VectorStore(db), **kwargs)
            else:
                result = await evaluate_case(case, vector_store, **kwargs)
        results[i] = result
        if on_result is not None:
            on_result(result)

    started = time.perf_counter()
    await asyncio.gather(*(worker(i, c, r) for i, (c, r) in enumerate(jobs)))
    report.wall_seconds = time.perf_counter() - started
    report.results = [r for r in results if r is not None]
    return report
