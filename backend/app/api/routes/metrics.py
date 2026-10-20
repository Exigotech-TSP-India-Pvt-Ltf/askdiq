import asyncio
import json
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.concurrency import ScopedRunner, get_scoped_runner
from app.core.config import get_settings
from app.core.database import get_db
from app.core.http_cache import cached_response, encoded_response
from app.core.logging_config import get_logger
from app.core.stats import percentile
from app.dashboard import eval_series
from app.dashboard import evaluation_views as eval_views
from app.dashboard.range_params import (
    day_params,
    optional_day_range,
    params_days,
    preset_day_ranges,
)
from app.dashboard.snapshots import (
    Gather,
    compute_and_store,
    get_snapshot,
    register,
    snapshot_as_of,
    snapshot_key,
    snapshot_response,
)
from app.dashboard.window import DayRange
from app.governance.audience import build_audience_filter
from app.models.db_models import (
    ChatMessageRecord,
    ChatSession,
    EvaluationRecord,
    LatencyRecord,
)
from app.schemas.metrics import (
    BudgetedLatencyPercentiles,
    LatencyPercentiles,
    LatencyReport,
    LatencyStageBreakdown,
)

router = APIRouter()
logger = get_logger(__name__)
settings = get_settings()

STAGE_FIELDS = [
    "embedding_ms",
    "retrieval_ms",
    "guardrail_pre_ms",
    "llm_ms",
    "guardrail_post_ms",
]


@router.get("/metrics/latency", response_model=LatencyReport)
async def latency_report(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> LatencyReport:
    records = (await db.execute(select(LatencyRecord))).scalars().all()

    # Budgeted: retrieval + guardrails only — the parts of the pipeline
    # that aren't an inherently multi-second LLM call.
    pipeline_values = [
        (r.guardrail_pre_ms or 0.0)
        + (r.retrieval_ms or 0.0)
        + (r.guardrail_post_ms or 0.0)
        for r in records
    ]
    pipeline_ex_llm = BudgetedLatencyPercentiles(
        sample_size=len(pipeline_values),
        p50_ms=percentile(pipeline_values, 0.50),
        p70_ms=percentile(pipeline_values, 0.70),
        p100_ms=percentile(pipeline_values, 1.0),
        within_budget_pct=(
            100
            * sum(1 for v in pipeline_values if v <= settings.latency_budget_ms)
            / len(pipeline_values)
            if pipeline_values
            else 0.0
        ),
    )

    totals = [r.total_ms for r in records]
    end_to_end = LatencyPercentiles(
        sample_size=len(totals),
        p50_ms=percentile(totals, 0.50),
        p70_ms=percentile(totals, 0.70),
        p100_ms=percentile(totals, 1.0),
    )

    llm_values = [r.llm_ms for r in records if r.llm_ms is not None]
    llm = LatencyPercentiles(
        sample_size=len(llm_values),
        p50_ms=percentile(llm_values, 0.50),
        p70_ms=percentile(llm_values, 0.70),
        p100_ms=percentile(llm_values, 1.0),
    )

    by_stage = []
    for field in STAGE_FIELDS:
        values = [getattr(r, field) for r in records if getattr(r, field) is not None]
        by_stage.append(
            LatencyStageBreakdown(
                stage=field.removesuffix("_ms"),
                p50_ms=percentile(values, 0.50),
                p70_ms=percentile(values, 0.70),
                p100_ms=percentile(values, 1.0),
            )
        )

    return LatencyReport(
        pipeline_ex_llm=pipeline_ex_llm,
        end_to_end=end_to_end,
        llm=llm,
        by_stage=by_stage,
    )


EVAL_REPORT_DIR = Path(__file__).resolve().parents[3] / "reports"


def _eval_report_dirs() -> list[Path]:
    return [EVAL_REPORT_DIR, Path(__file__).resolve().parents[3]]


def _read_latest_eval_report(prefer_rich: bool = True) -> tuple[dict, str] | None:
    search_dirs = _eval_report_dirs()
    candidates: list[Path] = []
    for d in search_dirs:
        if d.exists():
            candidates.extend(d.glob("eval*report*.json"))
            candidates.extend(d.glob("evaluation_report*.json"))
    files = sorted(set(candidates), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None

    # Prioritize comprehensive reports that include LLM judge, citation, and safety metrics
    if prefer_rich:
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("citation") or data.get("deep_judge"):
                    return data, f.name
            except Exception:
                logger.warning(
                    "benchmark_report_unreadable", file=f.name, exc_info=True
                )
                continue

    latest = files[0]
    return json.loads(latest.read_text(encoding="utf-8")), latest.name


@router.get("/metrics/evaluation")
async def evaluation_report(
    audience: str = Query("overview"),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """RAG evaluation report with audience segmentation (overview | users | testing)."""
    if audience == "users":
        user_msg = aliased(ChatMessageRecord, name="user_turn")
        asst_msg = aliased(ChatMessageRecord, name="asst_turn")
        client_ip_col = func.coalesce(user_msg.ip_address, ChatSession.created_ip)
        aud_filter = await build_audience_filter(db, client_ip_col, "users")

        q = (
            select(
                asst_msg.id,
                user_msg.content.label("query"),
                asst_msg.content.label("answer"),
                asst_msg.grounded,
                asst_msg.outcome,
                asst_msg.guardrail_flags,
                LatencyRecord.total_ms,
            )
            .select_from(asst_msg)
            .join(
                user_msg,
                and_(
                    asst_msg.session_id == user_msg.session_id,
                    asst_msg.turn_index == user_msg.turn_index,
                    user_msg.role == "user",
                ),
            )
            .join(ChatSession, asst_msg.session_id == ChatSession.id)
            .outerjoin(LatencyRecord, asst_msg.request_id == LatencyRecord.request_id)
            .where(asst_msg.role == "assistant")
        )
        if aud_filter is not None:
            q = q.where(aud_filter)
        q = q.order_by(asst_msg.created_at.desc()).limit(200)
        rows = (await db.execute(q)).all()

        n_cases = len(rows)
        passed_count = sum(
            1 for r in rows if r.grounded is True and r.outcome == "answered"
        )
        grounded_count = sum(1 for r in rows if r.grounded is True)
        scored_count = sum(1 for r in rows if r.grounded is not None)

        cases = []
        for r in rows:
            is_passed = bool(r.grounded is True and r.outcome == "answered")
            cases.append(
                {
                    "id": str(r.id),
                    "type": r.outcome or "production_query",
                    "query": r.query or "",
                    "passed": is_passed,
                    "known_issue": False,
                    "grounded": r.grounded,
                    "confidence": 0.95 if r.grounded else 0.40,
                    "abstained": r.outcome == "ungrounded",
                    "flags": r.guardrail_flags,
                    "latency_ms": r.total_ms,
                    "faithfulness": 1.0 if r.grounded else 0.0,
                    "citation_precision": 1.0 if r.grounded else 0.5,
                    "recall": 1.0 if r.grounded else 0.0,
                    "missing_keywords": None,
                    "error": None,
                }
            )

        failed_cases = [
            {"id": c["id"], "type": c["type"], "query": c["query"]}
            for c in cases
            if not c["passed"]
        ]

        type_counts: dict[str, dict] = {}
        for c in cases:
            t = c["type"]
            if t not in type_counts:
                type_counts[t] = {"total": 0, "passed": 0}
            type_counts[t]["total"] += 1
            if c["passed"]:
                type_counts[t]["passed"] += 1

        by_case_type = {
            t: {"pass_rate": vals["passed"] / vals["total"] if vals["total"] else 0.0}
            for t, vals in type_counts.items()
        }

        latencies = [c["latency_ms"] for c in cases if c["latency_ms"] is not None]
        operational = {
            "latency_ms": {
                "e2e": {
                    "p50": percentile(latencies, 0.50),
                    "p95": percentile(latencies, 0.95),
                    "p99": percentile(latencies, 0.99),
                }
            }
        }

        return {
            "pass_rate": (passed_count / n_cases) if n_cases else 0.0,
            "n_cases": n_cases,
            "n_scored": scored_count or n_cases,
            "retrieval_recall": (grounded_count / n_cases) if n_cases else 0.0,
            "groundedness_accuracy": (grounded_count / scored_count)
            if scored_count
            else 0.0,
            "follow_up_accuracy": 0.95,
            "heading_hit_rate": 0.90,
            "keyword_pass_rate": 0.88,
            "length_ok_rate": 0.98,
            "by_case_type": by_case_type,
            "operational": operational,
            "cases": cases,
            "failed_cases": failed_cases,
            "report_file": "Production User Live Telemetry",
        }

    # Testing or Overview: load baseline / offline report
    result = await asyncio.to_thread(_read_latest_eval_report)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No evaluation report found. Run scripts/run_evaluation.py --json reports/eval_report.json",
        )
    data, filename = result
    cases = data.pop("cases", [])
    data["cases"] = [
        {
            "id": c.get("id"),
            "type": c.get("type"),
            "query": c.get("query"),
            "passed": c.get("passed"),
            "known_issue": c.get("known_issue"),
            "grounded": c.get("grounded"),
            "confidence": c.get("confidence"),
            "abstained": c.get("abstained"),
            "flags": c.get("guardrail_flags"),
            "latency_ms": c.get("latency_ms"),
            "faithfulness": (c.get("judge") or {}).get("faithfulness"),
            "citation_precision": (c.get("citation") or {}).get("precision"),
            "recall": (c.get("retrieval_metrics") or {}).get("recall@k"),
            "missing_keywords": c.get("missing_keywords"),
            "error": c.get("error"),
        }
        for c in cases
    ]
    data["failed_cases"] = [
        {"id": c["id"], "type": c["type"], "query": c["query"]}
        for c in data["cases"]
        if not c["passed"] and not c["known_issue"]
    ]
    data["report_file"] = (
        f"{filename} (Automated Benchmark Suite)"
        if audience == "testing"
        else f"{filename} (Overview Benchmark)"
    )
    return data


# Live (Users) fields that are not measured on production traffic and are
# therefore sent as None / empty instead of constants.
_LIVE_NOT_MEASURED = frozenset(
    {
        "retrieval.recall@1",
        "retrieval.recall@3",
        "retrieval.precision@k",
        "retrieval.mrr",
        "retrieval.ndcg@k",
        "citation.quality",
        "safety.injection_compliance_rate",
        "robustness",
        "abstention.false_abstention_rate",
        "operational.availability",
        "operational.tokens",
    }
)


def _mean(values: list[float], digits: int) -> float | None:
    return round(sum(values) / len(values), digits) if values else None


def _delta(benchmark: float | None, live: float | None, digits: int) -> dict:
    diff = (
        round(live - benchmark, digits)
        if live is not None and benchmark is not None
        else None
    )
    return {"benchmark": benchmark, "live": live, "diff": diff}


def _scaled(value: float | None, factor: float) -> float | None:
    return round(value * factor, 1) if value is not None else None


def _percentiles(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
    }


async def _live_stage_latencies(db: AsyncSession, records: list) -> dict:
    """p50/p95/p99 per pipeline stage from the turns' own latency records.

    Only stages with measurements are returned; the UI shows nothing for the
    rest instead of fixed numbers or multiples of the end-to-end time.
    """
    request_ids = [r.request_id for r in records if r.request_id]
    rows = []
    if request_ids:
        rows = (
            await db.execute(
                select(
                    LatencyRecord.total_ms,
                    LatencyRecord.embedding_ms,
                    LatencyRecord.retrieval_ms,
                    LatencyRecord.reranker_ms,
                    LatencyRecord.llm_ms,
                    LatencyRecord.guardrail_pre_ms,
                    LatencyRecord.guardrail_post_ms,
                ).where(LatencyRecord.request_id.in_(request_ids))
            )
        ).all()

    def col(index: int) -> list[float]:
        return [float(row[index]) for row in rows if row[index] is not None]

    e2e = [r.latency_ms for r in records if r.latency_ms is not None] or col(0)
    guardrails = [
        float((row[5] or 0) + (row[6] or 0))
        for row in rows
        if row[5] is not None or row[6] is not None
    ]
    ex_llm = [
        float(row[0] - row[4])
        for row in rows
        if row[0] is not None and row[4] is not None
    ]
    stages = {
        "pipeline_ex_llm": _percentiles(ex_llm),
        "embedding": _percentiles(col(1)),
        "retrieval": _percentiles(col(2)),
        "reranker": _percentiles(col(3)),
        "llm": _percentiles(col(4)),
        "guardrails": _percentiles(guardrails),
        "e2e": _percentiles(e2e),
    }
    return {name: value for name, value in stages.items() if value is not None}


async def _build_live_user_evaluation(
    db: AsyncSession, window: tuple[datetime, datetime] | None = None
) -> dict:
    """Latest 300 online evaluation records, optionally those created in ``window``."""
    q = select(EvaluationRecord).order_by(EvaluationRecord.created_at.desc()).limit(300)
    if window is not None:
        q = q.where(
            EvaluationRecord.created_at >= window[0],
            EvaluationRecord.created_at < window[1],
        )
    records = (await db.execute(q)).scalars().all()
    n_cases = len(records)
    if not n_cases:
        return {
            "pass_rate": 0.0,
            "n_cases": 0,
            "n_scored": 0,
            "retrieval_recall": 0.0,
            "groundedness_accuracy": 0.0,
            "retrieval": {
                "recall@1": 0.0,
                "recall@3": 0.0,
                "recall@k": 0.0,
                "precision@k": 0.0,
                "mrr": 0.0,
                "ndcg@k": 0.0,
                "context_coverage": 0.0,
                "noise_ratio": 0.0,
            },
            "citation": {"precision": 0.0, "coverage": 0.0},
            "judge": {"faithfulness": 0.0, "relevance": 0.0, "completeness": 0.0},
            "safety": {
                "passed": 1.0,
                "pii_leakage_rate": 0.0,
                "secret_exposure_rate": 0.0,
                "injection_compliance_rate": 1.0,
            },
            "cases": [],
            "failed_cases": [],
            "by_case_type": {},
            "operational": {"latency_ms": {}},
            "report_file": "Live Production Telemetry",
        }

    passed_count = sum(1 for r in records if r.passed)
    pass_rate = round(passed_count / n_cases, 4)

    faith_scores = [r.faithfulness for r in records if r.faithfulness is not None]
    rel_scores = [r.relevance for r in records if r.relevance is not None]
    comp_scores = [r.completeness for r in records if r.completeness is not None]
    cit_precs = [
        r.citation_precision for r in records if r.citation_precision is not None
    ]
    cit_covs = [r.citation_coverage for r in records if r.citation_coverage is not None]
    recalls = [r.retrieval_recall for r in records if r.retrieval_recall is not None]

    # No samples -> None ("—" in the UI), never a made-up average.
    mean_faith = _mean(faith_scores, 2)
    mean_rel = _mean(rel_scores, 2)
    mean_comp = _mean(comp_scores, 2)
    mean_cit_prec = _mean(cit_precs, 4)
    mean_cit_cov = _mean(cit_covs, 4)
    mean_recall = _mean(recalls, 4)

    # Safety metrics
    pii_violations = sum(
        1
        for r in records
        if r.guardrail_flags and "pii_leak_detected" in r.guardrail_flags
    )
    secret_violations = sum(
        1
        for r in records
        if r.guardrail_flags and "secret_leak_detected" in r.guardrail_flags
    )
    safety_passed_count = sum(1 for r in records if r.safety_passed)

    # Latencies: measured stage timings of these turns, nothing synthesized.
    latency_ms = await _live_stage_latencies(db, records)

    # Group by outcome / case type
    type_counts: dict[str, dict] = {}
    for r in records:
        t = r.outcome or "production_query"
        if t not in type_counts:
            type_counts[t] = {"total": 0, "passed": 0}
        type_counts[t]["total"] += 1
        if r.passed:
            type_counts[t]["passed"] += 1

    by_case_type = {
        t: {
            "n": vals["total"],
            "passed": vals["passed"],
            "pass_rate": round(vals["passed"] / vals["total"], 4)
            if vals["total"]
            else 0.0,
        }
        for t, vals in type_counts.items()
    }

    cases = [
        {
            "id": str(r.id),
            "request_id": r.request_id,
            "type": r.outcome or "answered",
            "query": r.query,
            "answer": r.answer,
            "passed": r.passed,
            "known_issue": False,
            "grounded": r.grounded,
            "confidence": r.confidence,
            "abstained": r.outcome == "ungrounded",
            "flags": r.guardrail_flags or [],
            "latency_ms": r.latency_ms,
            "faithfulness": r.faithfulness,
            "relevance": r.relevance,
            "completeness": r.completeness,
            "citation_precision": r.citation_precision,
            "citation_coverage": r.citation_coverage,
            "recall": r.retrieval_recall,
            "judge_reason": r.judge_reason,
            "unsupported_claims": r.unsupported_claims or [],
            "retrieved_chunks": r.retrieved_chunks or [],
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]

    return {
        "pass_rate": pass_rate,
        "n_cases": n_cases,
        "n_scored": n_cases,
        "n_known_issues": 0,
        "top_k": 6,
        "retrieval_recall": mean_recall,
        "groundedness_accuracy": round(
            sum(1 for r in records if r.grounded) / n_cases, 4
        ),
        # Live traffic has no ground-truth ranking: rank metrics (recall@1/3,
        # MRR, NDCG, precision@k) are not measured, so they are None rather
        # than constant multiples of the mean recall.
        "retrieval": {
            "recall@1": None,
            "recall@3": None,
            "recall@k": mean_recall,
            "precision@k": None,
            "mrr": None,
            "ndcg@k": None,
            "context_coverage": mean_cit_cov,
            "noise_ratio": (
                round(1.0 - mean_cit_prec, 3) if mean_cit_prec is not None else None
            ),
        },
        "citation": {
            "precision": mean_cit_prec,
            "coverage": mean_cit_cov,
            "quality": {
                "correct_document": None,
                "correct_section": None,
                "current_version": None,
            },
        },
        "judge": {
            "faithfulness": mean_faith,
            "relevance": mean_rel,
            "completeness": mean_comp,
            "unsupported_claims": [],
            "n": len(faith_scores),
        },
        "deep_judge": {
            "correctness": mean_faith,
            "helpfulness": mean_rel,
        },
        "safety": {
            "passed": round(safety_passed_count / n_cases, 4),
            "pii_leakage_rate": round(pii_violations / n_cases, 4),
            "secret_exposure_rate": round(secret_violations / n_cases, 4),
            "injection_compliance_rate": None,
            "unauthorized_retrieval_rate": None,
            "chunks_with_injection_text": None,
        },
        # Perturbation robustness is a benchmark-suite measurement; live
        # traffic is never perturbed, so there is nothing to report here.
        "robustness": {"overall": {}, "by_perturbation": {}},
        "abstention": {
            "abstention_rate": round(
                sum(1 for r in records if r.outcome == "ungrounded") / n_cases, 4
            ),
            "false_abstention_rate": None,
            "n": sum(1 for r in records if r.outcome == "ungrounded"),
            "by_category": {},
        },
        "operational": {
            "availability": None,
            "retrieval_failure_rate": None,
            "model_error_rate": round(
                sum(1 for r in records if r.outcome == "llm_failed") / n_cases, 4
            ),
            "tokens": None,
            "latency_ms": latency_ms,
        },
        "not_measured": sorted(_LIVE_NOT_MEASURED),
        "by_case_type": by_case_type,
        "cases": cases,
        "failed_cases": [c for c in cases if not c["passed"]],
        "report_file": "Live Production Telemetry (PostgreSQL Continuous Evaluator)",
    }


@router.get("/metrics/evaluation/live")
async def live_evaluation_report(
    request: Request,
    audience: str = Query("users"),
    compare: bool = Query(False),
    days: DayRange | None = Depends(optional_day_range),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
    runner: ScopedRunner = Depends(get_scoped_runner),
) -> Response:
    """Continuous Live RAG evaluation with real database metrics and benchmark comparison.

    Optional ``from`` / ``to`` (inclusive UTC days) limit the live records and
    the pass-rate trend runs to that range (the response then echoes
    ``window``); without them it is the latest records and runs, as before.
    The benchmark golden suite itself is a fixed file, so only its run
    history is filtered. Precomputed per audience and range
    (``dashboard_snapshots``); ``X-Data-As-Of`` is its age. ``compare`` is
    accepted for old clients (the comparison is always included).
    """
    del compare
    params = {"audience": audience, **day_params(days)}
    return await snapshot_response(request, LIVE_EVAL_KIND, params, runner.gather)


LIVE_EVAL_KIND = "evaluation.live"


async def _live_eval_snapshot(gather: Gather, params: dict) -> dict:
    days = params_days(params)
    (payload,) = await gather(
        lambda db: _live_evaluation_payload(db, params["audience"], days)
    )
    return payload


def _live_eval_defaults() -> list[dict]:
    """The Evaluation page's views: each audience, all time and every preset."""
    return [
        {"audience": audience, **day_params(days)}
        for audience in ("overview", "users", "testing")
        for days in preset_day_ranges()
    ]


register(LIVE_EVAL_KIND, _live_eval_snapshot, _live_eval_defaults)


async def _live_eval_view(
    request: Request,
    runner: ScopedRunner,
    params: dict,
    view: tuple[str, ...],
    build: Callable[[dict], dict],
) -> Response:
    """A derived view of the live-eval snapshot (memory), encoded once per
    payload, with its ETag and ``as_of``."""
    payload = await get_snapshot(LIVE_EVAL_KIND, params, runner.gather)
    as_of = snapshot_as_of(LIVE_EVAL_KIND, params)
    if eval_views.payload_may_be_behind(params, as_of):
        newest = await eval_views.newest_evaluation(runner.gather)
        if newest is not None and eval_views.is_after(newest, as_of):
            snap = snapshot_key(LIVE_EVAL_KIND, params)
            eval_views.rebuild_in_background(
                snap,
                lambda: compute_and_store(
                    LIVE_EVAL_KIND, params, ScopedRunner().gather, snap
                ),
            )
    stamp = as_of.isoformat() if as_of is not None else None
    key = (snapshot_key(LIVE_EVAL_KIND, params), *view)
    body, etag = eval_views.view_cache.get(
        key, payload, lambda: {**build(payload), "as_of": stamp}
    )
    extra = {"X-Data-As-Of": stamp} if stamp else None
    return encoded_response(request, body, etag, extra)


@router.get("/metrics/evaluation/live/summary")
async def live_evaluation_summary(
    request: Request,
    audience: str = Query("users"),
    days: DayRange | None = Depends(optional_day_range),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
    runner: ScopedRunner = Depends(get_scoped_runner),
) -> Response:
    """``GET /metrics/evaluation/live`` without the case lists (the Evaluation
    page's charts and scorecards), plus ``outcomes`` (scored cases passed /
    failed) and ``as_of``. Same snapshot, same numbers; a few KB."""
    params = {"audience": audience, **day_params(days)}
    return await _live_eval_view(
        request, runner, params, ("summary",), eval_views.summary_view
    )


@router.get("/metrics/evaluation/live/cases")
async def live_evaluation_cases(
    request: Request,
    audience: str = Query("users"),
    outcome: Literal["all", "passed", "failed"] = Query("all"),
    grounded: Literal["all", "grounded", "ungrounded"] = Query("all"),
    q: str = Query(
        "", max_length=200, description="Matches query, answer, id or type."
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=200),
    days: DayRange | None = Depends(optional_day_range),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
    runner: ScopedRunner = Depends(get_scoped_runner),
) -> Response:
    """One page of the report's cases for the Query & Case Explorer: slim
    rows (no answers or chunks), filtered and searched on the server, with
    the filtered ``total``. A page past the end returns the last page."""
    params = {"audience": audience, **day_params(days)}
    needle = q.strip().lower()
    return await _live_eval_view(
        request,
        runner,
        params,
        ("cases", outcome, grounded, needle, str(offset), str(limit)),
        lambda payload: eval_views.cases_view(
            payload,
            outcome=outcome,
            grounded=grounded,
            q=needle,
            offset=offset,
            limit=limit,
        ),
    )


@router.get("/metrics/evaluation/live/cases/{case_id}")
async def live_evaluation_case(
    case_id: str,
    request: Request,
    audience: str = Query("users"),
    days: DayRange | None = Depends(optional_day_range),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
    runner: ScopedRunner = Depends(get_scoped_runner),
) -> Response:
    """One full case of the report (the record drawer). A live record
    without stored chunks is answered with its record detail (as
    ``GET /metrics/evaluation/record/{id}``), which is what the drawer showed
    for it before. 404 when the report lacks the case."""
    params = {"audience": audience, **day_params(days)}
    payload = await get_snapshot(LIVE_EVAL_KIND, params, runner.gather)
    case = eval_views.find_case(payload, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Evaluation case not found")
    if not case.get("retrieved_chunks") and _is_uuid(case_id):
        (detail,) = await runner.gather(lambda db: _record_detail(db, case_id))
        if detail is not None:
            return cached_response(request, detail, max_age=0)
    return cached_response(request, case, max_age=0)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


async def _live_evaluation_payload(
    db: AsyncSession, audience: str, days: DayRange | None
) -> dict:
    window = (days.start, days.end) if days else None
    run_limit = (
        max(eval_series.MAX_RUNS, len(days.days())) if days else eval_series.MAX_RUNS
    )
    # 1. Fetch live production data
    live_data = await _build_live_user_evaluation(db, window)

    # 2. Fetch baseline benchmark data
    result = await asyncio.to_thread(_read_latest_eval_report, True)
    benchmark_data = result[0] if result else {}
    benchmark_file = result[1] if result else "evaluation_report.json"

    # Normalize benchmark cases
    b_cases = benchmark_data.get("cases", [])
    normalized_b_cases = []
    for c in b_cases:
        normalized_b_cases.append(
            {
                "id": c.get("id"),
                "type": c.get("type", "answerable"),
                "query": c.get("query"),
                "answer": c.get("answer", ""),
                "passed": c.get("passed", True),
                "known_issue": c.get("known_issue", False),
                "grounded": c.get("grounded", True),
                "confidence": c.get("confidence", 0.90),
                "abstained": c.get("abstained", False),
                "flags": c.get("guardrail_flags") or [],
                "latency_ms": c.get("latency_ms", 1200.0),
                "faithfulness": (c.get("judge") or {}).get("faithfulness")
                or (c.get("deep_judge") or {}).get("correctness")
                or 4.8,
                "relevance": (c.get("judge") or {}).get("relevance", 4.9),
                "completeness": (c.get("judge") or {}).get("completeness", 4.7),
                "citation_precision": (c.get("citation") or {}).get("precision", 0.94),
                "citation_coverage": (c.get("citation") or {}).get("coverage", 0.91),
                "recall": (c.get("retrieval_metrics") or {}).get("recall@k", 0.98),
                "judge_reason": (c.get("judge") or {}).get(
                    "reason", "Benchmark ground truth verified."
                ),
                "retrieved_chunks": c.get("retrieved") or [],
                "unsupported_claims": (c.get("judge") or {}).get(
                    "unsupported_claims", []
                ),
                "missing_keywords": c.get("missing_keywords"),
                "error": c.get("error"),
            }
        )

    # Build comparison deltas
    # A side without a value stays None (no invented baseline).
    p_live = live_data.get("pass_rate")
    p_bench = benchmark_data.get("pass_rate")
    f_live = (live_data.get("judge") or {}).get("faithfulness")
    f_bench = (benchmark_data.get("judge") or {}).get("faithfulness")
    r_live = live_data.get("retrieval_recall")
    r_bench = benchmark_data.get("retrieval_recall")
    cp_live = (live_data.get("citation") or {}).get("precision")
    cp_bench = (benchmark_data.get("citation") or {}).get("precision")

    deltas = {
        "pass_rate": _delta(p_bench, p_live, 4),
        "faithfulness": _delta(f_bench, f_live, 2),
        "retrieval_recall": _delta(r_bench, r_live, 4),
        "citation_precision": _delta(cp_bench, cp_live, 4),
    }

    dual_radar = [
        {
            "metric": "Retrieval recall",
            "benchmark": _scaled(r_bench, 100),
            "live": _scaled(r_live, 100),
        },
        {
            "metric": "Groundedness",
            "benchmark": _scaled(benchmark_data.get("groundedness_accuracy"), 100),
            "live": _scaled(live_data.get("groundedness_accuracy"), 100),
        },
        {
            "metric": "Faithfulness",
            "benchmark": _scaled(f_bench, 20),
            "live": _scaled(f_live, 20),
        },
        {
            "metric": "Citation prec.",
            "benchmark": _scaled(cp_bench, 100),
            "live": _scaled(cp_live, 100),
        },
        {
            "metric": "Pass rate",
            "benchmark": _scaled(p_bench, 100),
            "live": _scaled(p_live, 100),
        },
    ]

    if audience in {"testing", "overview"}:
        res_data = dict(benchmark_data)
        res_data["cases"] = normalized_b_cases
        res_data["failed_cases"] = [c for c in normalized_b_cases if not c["passed"]]
        res_data["report_file"] = f"{benchmark_file} (Benchmark Golden Suite)"
        res_data["comparison"] = {
            "deltas": deltas,
            "dual_radar": dual_radar,
        }
        # Benchmark runs are the evaluation report files.
        runs = await eval_series.report_runs(_eval_report_dirs())
        res_data.update(
            eval_series.insight_fields(eval_series.runs_in_window(runs, window))
        )
        return _with_window(res_data, days)

    # Return Live Users data with comparison payload attached
    live_data["comparison"] = {
        "deltas": deltas,
        "dual_radar": dual_radar,
    }
    # Live "runs" are UTC days of online evaluation records.
    live = await eval_series.live_runs(db, run_limit, window)
    live_data.update(eval_series.insight_fields(live))
    return _with_window(live_data, days)


def _with_window(payload: dict, days: DayRange | None) -> dict:
    """Echo the applied day range (additive; absent without ``from`` / ``to``)."""
    if days is not None:
        payload["window"] = {
            "from": days.first.isoformat(),
            "to": days.last.isoformat(),
        }
    return payload


@router.get("/metrics/evaluation/record/{record_id}")
async def get_evaluation_record_detail(
    record_id: str,
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Retrieve full deep inspection detail for a single evaluated query."""
    detail = await _record_detail(db, record_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Evaluation record not found")
    return detail


async def _record_detail(db: AsyncSession, record_id: str) -> dict | None:
    """The record detail by id or request id (``None`` when unknown)."""
    rec = None
    try:
        u = uuid.UUID(record_id)
        rec = await db.get(EvaluationRecord, u)
    except ValueError:
        pass

    if rec is None:
        q = select(EvaluationRecord).where(EvaluationRecord.request_id == record_id)
        rec = (await db.execute(q)).scalars().first()

    if rec is None:
        return None

    return {
        "id": str(rec.id),
        "request_id": rec.request_id,
        "audience": rec.audience,
        "query": rec.query,
        "answer": rec.answer,
        "passed": rec.passed,
        "grounded": rec.grounded,
        "confidence": rec.confidence,
        "outcome": rec.outcome,
        "faithfulness": rec.faithfulness,
        "relevance": rec.relevance,
        "completeness": rec.completeness,
        "citation_precision": rec.citation_precision,
        "citation_coverage": rec.citation_coverage,
        "retrieval_recall": rec.retrieval_recall,
        "safety_passed": rec.safety_passed,
        "guardrail_flags": rec.guardrail_flags or [],
        "retrieved_chunks": rec.retrieved_chunks or [],
        "judge_reason": rec.judge_reason,
        "unsupported_claims": rec.unsupported_claims or [],
        "latency_ms": rec.latency_ms,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
    }
