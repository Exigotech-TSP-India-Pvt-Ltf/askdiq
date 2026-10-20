"""Advisor recommendations (Phase 4b, optional: ``AZURE_ADVISOR_ENABLED``).

Polled daily (``AZURE_ADVISOR_INTERVAL_SECONDS``): the existing
recommendations of our resource group (subscription
``Microsoft.Advisor/recommendations`` with ``$filter=ResourceGroup eq
'{rg}'``, api 2025-01-01). Never calls ``generateRecommendations``. Rows are
upserted by recommendation id; one Advisor stops returning is marked
``resolved_at`` (history kept). Model / version retirement notices are
flagged for the Governance list.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.azure_insights.capacity_schemas import AdvisorRecommendation, AzureAdvisorRead
from app.azure_insights.health import parse_time, plain_text
from app.azure_insights.scheduler import (
    AzureSource,
    SourceContext,
    SourceResult,
    register_source,
)
from app.core.config import Settings, get_settings
from app.dashboard.snapshots import Gather, register
from app.jobs.queue import utcnow
from app.models.db_models import AzureAdvisorRec

ADVISOR_SOURCE = "advisor"
ADVISOR_KIND = "azure.advisor"
ADVISOR_API_VERSION = "2025-01-01"
MAX_EXTENDED_KEYS = 20
MAX_EXTENDED_VALUE = 200
SHOWN = 50
_RETIREMENT_WORDS = ("retire", "deprecat", "end of life", "end-of-life")


def is_retirement(problem: str | None, solution: str | None, extended: dict) -> bool:
    text = " ".join(
        [problem or "", solution or "", *(str(k) for k in extended)]
    ).lower()
    return any(word in text for word in _RETIREMENT_WORDS)


def retirement_date(extended: dict[str, Any]) -> str | None:
    for key, value in extended.items():
        if "retire" in key.lower() and "date" in key.lower() and value:
            return str(value)[:40]
    return None


def _extended(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    items = list(value.items())[:MAX_EXTENDED_KEYS]
    return {str(k)[:80]: str(v)[:MAX_EXTENDED_VALUE] for k, v in items}


def parse_recommendation(item: dict[str, Any], now: datetime) -> dict | None:
    rec_id = item.get("name")
    if not rec_id:
        return None
    props = item.get("properties") or {}
    short = props.get("shortDescription") or {}
    return {
        "rec_id": str(rec_id)[:64],
        "category": plain_text(props.get("category"), 48),
        "impact": plain_text(props.get("impact"), 16),
        "impacted_field": plain_text(props.get("impactedField"), 200),
        "impacted_value": plain_text(props.get("impactedValue"), 200),
        "problem": plain_text(short.get("problem")),
        "solution": plain_text(short.get("solution")),
        "learn_more_link": plain_text(props.get("learnMoreLink"), 1000),
        "extended": _extended(props.get("extendedProperties")) or None,
        "last_updated": parse_time(props.get("lastUpdated")),
        "first_seen_at": now,
        "last_seen_at": now,
        "resolved_at": None,
    }


def recommendations_path(settings: Settings) -> str:
    return (
        f"/subscriptions/{settings.azure_subscription_id}"
        "/providers/Microsoft.Advisor/recommendations"
    )


async def store_recommendations(
    session: AsyncSession, rows: list[dict[str, Any]], now: datetime
) -> int:
    """Upsert ``rows``; mark the open ones not returned any more as resolved.
    Returns how many were resolved."""
    if rows:
        stmt = insert(AzureAdvisorRec).values(rows)
        cols = {
            c: stmt.excluded[c] for c in rows[0] if c not in {"rec_id", "first_seen_at"}
        }
        await session.execute(
            stmt.on_conflict_do_update(index_elements=["rec_id"], set_=cols)
        )
    ids = [r["rec_id"] for r in rows]
    resolved = await session.execute(
        update(AzureAdvisorRec)
        .where(
            AzureAdvisorRec.resolved_at.is_(None),
            AzureAdvisorRec.rec_id.notin_(ids) if ids else true(),
        )
        .values(resolved_at=now)
    )
    return int(resolved.rowcount or 0)  # type: ignore[attr-defined]


async def run_advisor(ctx: SourceContext) -> SourceResult:
    settings, now = ctx.settings, ctx.now
    params = {
        "api-version": ADVISOR_API_VERSION,
        "$filter": f"ResourceGroup eq '{settings.azure_resource_group}'",
    }
    rows: dict[str, dict[str, Any]] = {}
    async with ctx.arm_client() as api:
        async for item in api.paged(recommendations_path(settings), params):
            row = parse_recommendation(item, now)
            if row is not None:
                rows[row["rec_id"]] = row
    async with ctx.session_factory() as session:
        resolved = await store_recommendations(session, list(rows.values()), now)
        await session.commit()
    return SourceResult(
        rows=len(rows), detail={"recommendations": len(rows), "resolved": resolved}
    )


def _read(row: AzureAdvisorRec) -> AdvisorRecommendation:
    extended = row.extended or {}
    return AdvisorRecommendation(
        rec_id=row.rec_id,
        category=row.category,
        impact=row.impact,
        impacted_field=row.impacted_field,
        impacted_value=row.impacted_value,
        problem=row.problem,
        solution=row.solution,
        learn_more_link=row.learn_more_link,
        retirement=is_retirement(row.problem, row.solution, extended),
        retirement_date=retirement_date(extended),
        last_updated=row.last_updated,
        first_seen_at=row.first_seen_at,
    )


async def build_advisor(
    session: AsyncSession, settings: Settings, now: datetime | None = None
) -> AzureAdvisorRead:
    now = now or utcnow()
    rows = (
        (
            await session.execute(
                select(AzureAdvisorRec)
                .where(AzureAdvisorRec.resolved_at.is_(None))
                .order_by(AzureAdvisorRec.last_seen_at.desc(), AzureAdvisorRec.rec_id)
                .limit(SHOWN)
            )
        )
        .scalars()
        .all()
    )
    recs = sorted((_read(r) for r in rows), key=lambda r: (not r.retirement, r.rec_id))
    return AzureAdvisorRead(
        connected=settings.azure_insights_configured,
        enabled=settings.azure_advisor_enabled,
        recommendations=recs,
        generated_at=now,
    )


async def _advisor_snapshot(gather: Gather, _params: dict[str, Any]) -> dict:
    settings = get_settings()
    (payload,) = await gather(lambda db: build_advisor(db, settings))
    return payload.model_dump(mode="json")


register(ADVISOR_KIND, _advisor_snapshot, [{}])

register_source(
    AzureSource(
        name=ADVISOR_SOURCE,
        label="Advisor recommendations",
        interval=lambda s: s.azure_advisor_interval_seconds,
        run=run_advisor,
        enabled=lambda s: s.azure_advisor_enabled,
        snapshot_kinds=(ADVISOR_KIND,),
        order=70,
    )
)
