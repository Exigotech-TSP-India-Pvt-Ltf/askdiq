"""Azure billed-cost alert rules (``azure_billed_budget``, ``azure_estimate_drift``).

Both read ``azure_billed_costs`` (filled daily by the ``azure.costs`` job)
and AskDIQ's own usage tables through :mod:`app.azure_insights.billed`; one
pass (registry key ``azure_costs``) evaluates both. No billed data (Azure
Insights off, unsupported subscription) means no finding.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import catalogue as c
from app.alerts.catalogue import Severity
from app.alerts.service import Finding
from app.azure_insights.billed import (
    CLOSED_AFTER_DAYS,
    USD,
    billed_info,
    build_days,
    estimate_for,
)
from app.azure_insights.costs import month_start
from app.core.config import Settings

# Closed days checked for drift (the most recent ones).
DRIFT_LOOKBACK_DAYS = 7
_COMPONENT = "Cost"


def budget_level(pct: float, cfg: Settings) -> tuple[Severity, float] | None:
    """(severity, the threshold crossed) for ``pct`` of the budget, or None."""
    for severity, threshold in (
        (Severity.CRITICAL, cfg.alert_budget_critical_pct),
        (Severity.WARNING, cfg.alert_budget_warning_pct),
    ):
        if pct >= threshold:
            return severity, threshold
    return None


def _details(
    value: float, threshold: float, window: str, counts: dict[str, object]
) -> dict[str, object]:
    return {
        "value": round(value, 1),
        "threshold": threshold,
        "unit": "%",
        "window": window,
        "counts": counts,
    }


def billed_budget(cfg: Settings, currency: str | None) -> float | None:
    """AZURE_MONTHLY_BUDGET (billing currency), else MONTHLY_COST_BUDGET_USD
    for a USD bill; None when no budget applies (no FX conversion)."""
    if cfg.azure_monthly_budget:
        return cfg.azure_monthly_budget
    if currency == USD and cfg.monthly_cost_budget_usd:
        return cfg.monthly_cost_budget_usd
    return None


async def billed_budget_findings(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    today = now.astimezone(UTC).date()
    first = month_start(today)
    info = await billed_info(db, first, today)
    budget = billed_budget(cfg, info.currency)
    if not info.by_day or not budget:
        return []
    spent = sum(info.by_day.values())
    pct = spent * 100 / budget
    crossed = budget_level(pct, cfg)
    if crossed is None:
        return []
    severity, threshold = crossed
    cur = info.currency or ""
    month = f"{today:%Y-%m}"
    return [
        Finding(
            rule=c.AZURE_BILLED_BUDGET,
            dedupe_key=f"{c.AZURE_BILLED_BUDGET}:{month}",
            severity=severity,
            title=(
                f"Azure billed {pct:.1f}% of budget "
                f"({cur} {spent:,.2f} of {budget:,.2f})"
            ),
            summary=(
                f"Azure has billed {cur} {spent:,.2f} for {month} so far "
                f"(through {info.billed_through}), {pct:.1f}% of the {cur} "
                f"{budget:,.2f} budget (alert at {threshold:.1f}%). Billed cost "
                "lags 8-72 h, so the real figure is higher."
            ),
            component=_COMPONENT,
            details=_details(
                pct,
                threshold,
                f"month to date ({month}, UTC)",
                {
                    "billed": round(spent, 2),
                    "budget": round(budget, 2),
                    "currency": cur,
                    "billed_through": str(info.billed_through),
                },
            ),
        )
    ]


async def estimate_drift_findings(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    """One finding per closed day (deduped per day) where the estimate
    drifts from the bill by more than ALERT_AZURE_DRIFT_PCT."""
    today = now.astimezone(UTC).date()
    end = today - timedelta(days=CLOSED_AFTER_DAYS)
    start = end - timedelta(days=DRIFT_LOOKBACK_DAYS - 1)
    info = await billed_info(db, start, end)
    if info.billed_through is None or not info.by_day:
        return []
    estimate = await estimate_for(db, cfg, start, end, info.currency)
    if estimate.mismatch:
        return []
    findings: list[Finding] = []
    for day in build_days(start, end, today, info, estimate, None):
        billed = day.billed or 0.0
        if (
            not day.closed
            or day.variance_pct is None
            or billed < cfg.alert_azure_drift_min
        ):
            continue
        if abs(day.variance_pct) <= cfg.alert_azure_drift_pct:
            continue
        cur = info.currency or ""
        direction = "above" if day.variance_pct > 0 else "below"
        findings.append(
            Finding(
                rule=c.AZURE_ESTIMATE_DRIFT,
                dedupe_key=f"{c.AZURE_ESTIMATE_DRIFT}:{day.date.isoformat()}",
                severity=Severity.WARNING,
                title=(
                    f"Estimate {abs(day.variance_pct):.1f}% {direction} Azure bill "
                    f"on {day.date.isoformat()}"
                ),
                summary=(
                    f"On {day.date.isoformat()} Azure billed {cur} {billed:,.2f} "
                    f"and AskDIQ estimated {estimate.currency} {day.estimate:,.2f} "
                    f"({day.variance_pct:+.1f}%, threshold "
                    f"{cfg.alert_azure_drift_pct:.1f}%)."
                ),
                component=_COMPONENT,
                details=_details(
                    abs(day.variance_pct),
                    cfg.alert_azure_drift_pct,
                    f"UTC day {day.date.isoformat()}",
                    {
                        "billed": round(billed, 2),
                        "estimate": round(day.estimate, 2),
                        "currency": cur,
                        "status": day.status,
                    },
                ),
            )
        )
    return findings


async def azure_cost_rule(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    """Both Azure cost rules in one pass (registry key ``azure_costs``)."""
    return [
        *await billed_budget_findings(db, now, cfg),
        *await estimate_drift_findings(db, now, cfg),
    ]
