from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.stats import percentile
from app.governance.auth import get_current_actor
from app.models.db_models import LatencyRecord
from app.schemas.metrics import (
    BudgetedLatencyPercentiles,
    LatencyPercentiles,
    LatencyReport,
    LatencyStageBreakdown,
)

router = APIRouter()
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
    actor: str = Depends(get_current_actor),
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
