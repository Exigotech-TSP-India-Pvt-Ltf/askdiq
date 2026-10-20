"""Database-backed alert rules (see ``app.alerts.catalogue``).

Each rule is ``async (db, now, settings) -> list[Finding]`` over persisted
rows, so every worker computes the same answer. Every query is bounded by a
time window, an aggregate or a ``LIMIT``. Tables with naive UTC timestamps
(``audit_log``, ``chat_messages``, ``latency_metrics``, ``message_feedback``)
are compared with a naive UTC ``since``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import catalogue as c
from app.alerts.azure_rules import (
    azure_quota_rule,
    azure_sync_failing_rule,
    azure_unhealthy_rule,
)
from app.alerts.catalogue import Severity
from app.alerts.resource_rules import resource_rule
from app.alerts.service import NOTIFY_JOB, Finding
from app.auth.permissions import AccessRequestStatus
from app.core.config import Settings
from app.governance.monitoring import persisted_error_counts
from app.governance.pii import redact_pii
from app.models.db_models import (
    AccessRequest,
    AuditLog,
    ChatMessageRecord,
    LatencyRecord,
    MessageFeedback,
)

Rule = Callable[[AsyncSession, datetime, Settings], Awaitable[list[Finding]]]
RESOURCES_GROUP = "resources"

_SAMPLE_IDS = 5
_MAX_ACCESS_REQUEST_ALERTS = 20
_MAX_ERROR_CHARS = 300
_NEGATIVE_RATING_MAX = 2
INGEST_PATHS = ("/ingest", "/ingest/file")
_LEAK_FLAGS = frozenset({"secret_leak_detected"})


def naive_utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def fmt_ms(value: float) -> str:
    """Latency for titles: '850 ms' below one second, else '24 s' / '8.5 s'."""
    if value < 1000:  # noqa: PLR2004 - one second
        return f"{value:.0f} ms"
    seconds = value / 1000
    return f"{seconds:.0f} s" if seconds >= 10 else f"{seconds:.1f} s"  # noqa: PLR2004


def fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


def level(value: float, warning: float, critical: float) -> Severity | None:
    if value >= critical:
        return Severity.CRITICAL
    if value >= warning:
        return Severity.WARNING
    return None


def safe_text(value: Any) -> str:
    """PII-masked, length-capped free text from the database (errors etc.)."""
    return redact_pii(str(value or "")[:_MAX_ERROR_CHARS])[0]


async def _recent_request_ids(
    db: AsyncSession, action: str, since: datetime, extra: Any = None
) -> list[str]:
    stmt = (
        select(AuditLog.request_id)
        .where(AuditLog.action == action, AuditLog.created_at >= since)
        .order_by(AuditLog.created_at.desc())
        .limit(_SAMPLE_IDS)
    )
    if extra is not None:
        stmt = stmt.where(extra)
    return [str(r) for r in (await db.scalars(stmt)).all()]


async def error_rate_rule(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    window = cfg.governance_health_window_minutes
    since = naive_utc(now) - timedelta(minutes=window)
    counts = await persisted_error_counts(db, "overview", since)
    requests = int(counts["requests"])
    rate = float(counts["error_rate"])
    if requests < cfg.alert_min_requests:
        return []
    severity = level(
        rate,
        cfg.governance_health_error_rate_degraded_pct,
        cfg.governance_health_error_rate_incident_pct,
    )
    if severity is None:
        return []
    threshold = (
        cfg.governance_health_error_rate_incident_pct
        if severity is Severity.CRITICAL
        else cfg.governance_health_error_rate_degraded_pct
    )
    return [
        Finding(
            rule=c.ERROR_RATE,
            dedupe_key=c.ERROR_RATE,
            severity=severity,
            title=f"Error rate {fmt_pct(rate)} above {fmt_pct(threshold)}",
            summary=(
                f"{counts['errors']} of {requests} requests failed in the last "
                f"{window} minutes ({counts['llm_failed']} failed assistant turns, "
                f"{counts['server_errors']} server errors)."
            ),
            component="API",
            details={
                "value": round(rate, 2),
                "threshold": threshold,
                "unit": "%",
                "window_minutes": window,
                "counts": {
                    "requests": requests,
                    "errors": counts["errors"],
                    "llm_failed": counts["llm_failed"],
                    "server_errors": counts["server_errors"],
                },
                "request_ids": await _recent_request_ids(db, "server_error", since),
            },
        )
    ]


async def p95_latency_rule(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    window = cfg.governance_health_window_minutes
    since = naive_utc(now) - timedelta(minutes=window)
    p95, samples = (
        await db.execute(
            select(
                func.percentile_cont(0.95).within_group(LatencyRecord.total_ms),
                func.count(LatencyRecord.id),
            ).where(LatencyRecord.created_at >= since)
        )
    ).one()
    if p95 is None or int(samples) < cfg.alert_latency_min_samples:
        return []
    value = float(p95)
    severity = level(
        value,
        cfg.governance_health_p95_degraded_ms,
        cfg.governance_health_p95_incident_ms,
    )
    if severity is None:
        return []
    threshold = (
        cfg.governance_health_p95_incident_ms
        if severity is Severity.CRITICAL
        else cfg.governance_health_p95_degraded_ms
    )
    slowest = (
        await db.scalars(
            select(LatencyRecord.request_id)
            .where(LatencyRecord.created_at >= since)
            .order_by(LatencyRecord.total_ms.desc())
            .limit(_SAMPLE_IDS)
        )
    ).all()
    return [
        Finding(
            rule=c.P95_LATENCY,
            dedupe_key=c.P95_LATENCY,
            severity=severity,
            title=f"p95 latency {fmt_ms(value)} above {fmt_ms(threshold)}",
            summary=(
                f"95% of the last {samples} chat requests ({window} minutes) "
                f"finished within {fmt_ms(value)}; the threshold is {fmt_ms(threshold)}."
            ),
            component="Query pipeline",
            details={
                "value": round(value),
                "threshold": threshold,
                "unit": "ms",
                "window_minutes": window,
                "counts": {"requests": int(samples)},
                "request_ids": [str(r) for r in slowest],
            },
        )
    ]


async def llm_failures_rule(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    window = cfg.alert_llm_failure_window_minutes
    since = naive_utc(now) - timedelta(minutes=window)
    error_type = AuditLog.detail["error_type"].as_string()
    rows = (
        await db.execute(
            select(error_type, func.count(AuditLog.id))
            .where(AuditLog.action == "llm_call_failed", AuditLog.created_at >= since)
            .group_by(error_type)
        )
    ).all()
    total = sum(int(n) for _t, n in rows)
    if total < cfg.alert_llm_failures_threshold:
        return []
    by_type = {safe_text(t or "unknown"): int(n) for t, n in rows}
    return [
        Finding(
            rule=c.LLM_FAILURES,
            dedupe_key=c.LLM_FAILURES,
            severity=Severity.CRITICAL,
            title=f"{total} LLM call failures in {window} min",
            summary=(
                f"Azure OpenAI chat completions failed {total} times in the last "
                f"{window} minutes (threshold {cfg.alert_llm_failures_threshold})."
            ),
            component="Azure OpenAI",
            details={
                "value": total,
                "threshold": cfg.alert_llm_failures_threshold,
                "unit": "failures",
                "window_minutes": window,
                "counts": by_type,
                "request_ids": await _recent_request_ids(db, "llm_call_failed", since),
            },
        )
    ]


async def monthly_budget_rule(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    budget = cfg.monthly_cost_budget_usd
    if not budget:
        return []
    utc_now = now.astimezone(UTC)
    month_start = utc_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = float(
        await db.scalar(
            select(func.coalesce(func.sum(ChatMessageRecord.cost_usd), 0.0)).where(
                ChatMessageRecord.created_at >= naive_utc(month_start)
            )
        )
        or 0.0
    )
    pct = spent * 100 / budget
    severity = level(pct, cfg.alert_budget_warning_pct, cfg.alert_budget_critical_pct)
    if severity is None:
        return []
    threshold = (
        cfg.alert_budget_critical_pct
        if severity is Severity.CRITICAL
        else cfg.alert_budget_warning_pct
    )
    elapsed_days = max((utc_now - month_start).total_seconds() / 86400, 1 / 24)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    month_days = (next_month - month_start).days
    month = month_start.strftime("%Y-%m")
    return [
        Finding(
            rule=c.MONTHLY_BUDGET,
            dedupe_key=f"{c.MONTHLY_BUDGET}:{month}",
            severity=severity,
            title=(
                f"Monthly spend {fmt_pct(pct)} of budget "
                f"(${spent:,.2f} of ${budget:,.2f})"
            ),
            summary=(
                f"Spend for {month} has reached ${spent:,.2f}, {fmt_pct(pct)} of the "
                f"${budget:,.2f} budget (alert at {fmt_pct(threshold)})."
            ),
            component="Cost",
            details={
                "value": round(pct, 1),
                "threshold": threshold,
                "unit": "%",
                "window": f"month to date ({month}, UTC)",
                "counts": {
                    "spent_usd": round(spent, 2),
                    "budget_usd": round(budget, 2),
                    "projected_month_usd": round(spent / elapsed_days * month_days, 2),
                },
            },
        )
    ]


async def feedback_spike_rule(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    window = cfg.alert_feedback_window_minutes
    since = naive_utc(now) - timedelta(minutes=window)
    total, negative = (
        await db.execute(
            select(
                func.count(MessageFeedback.id),
                func.count(MessageFeedback.id).filter(
                    MessageFeedback.rating <= _NEGATIVE_RATING_MAX
                ),
            ).where(MessageFeedback.created_at >= since)
        )
    ).one()
    total, negative = int(total), int(negative)
    if total < cfg.alert_feedback_min_ratings:
        return []
    pct = negative * 100 / total
    if pct <= cfg.alert_feedback_negative_pct:
        return []
    return [
        Finding(
            rule=c.FEEDBACK_SPIKE,
            dedupe_key=c.FEEDBACK_SPIKE,
            severity=Severity.WARNING,
            title=f"Negative feedback {fmt_pct(pct)} of {total} ratings",
            summary=(
                f"{negative} of {total} ratings in the last {window} minutes were "
                f"1-2 stars (alert above {fmt_pct(cfg.alert_feedback_negative_pct)})."
            ),
            component="Answer quality",
            details={
                "value": round(pct, 1),
                "threshold": cfg.alert_feedback_negative_pct,
                "unit": "%",
                "window_minutes": window,
                "counts": {"ratings": total, "negative": negative},
            },
        )
    ]


async def access_request_rule(
    db: AsyncSession, _now: datetime, _cfg: Settings
) -> list[Finding]:
    rows = (
        await db.execute(
            select(
                AccessRequest.id, AccessRequest.requested_role, AccessRequest.created_at
            )
            .where(AccessRequest.status == AccessRequestStatus.PENDING.value)
            .order_by(AccessRequest.created_at.desc())
            .limit(_MAX_ACCESS_REQUEST_ALERTS)
        )
    ).all()
    pending_total = int(
        await db.scalar(
            select(func.count(AccessRequest.id)).where(
                AccessRequest.status == AccessRequestStatus.PENDING.value
            )
        )
        or 0
    )
    findings = []
    for request_id, role, created_at in rows:
        role_text = role or "any role"
        findings.append(
            Finding(
                rule=c.ACCESS_REQUEST,
                dedupe_key=f"{c.ACCESS_REQUEST}:{request_id}",
                severity=Severity.INFO,
                title=f"New dashboard access request ({role_text})",
                summary=(
                    "A signed-in user asked for dashboard access and is waiting "
                    f"for approval ({pending_total} pending in total)."
                ),
                component="User management",
                details={
                    "requested_role": role_text,
                    "requested_at": created_at.isoformat() if created_at else None,
                    "counts": {"pending_requests": pending_total},
                },
            )
        )
    return findings


_DEAD_JOBS_SQL = text(
    """
    SELECT type, count(*) AS n, max(finished_at) AS last_at,
           (array_agg(last_error ORDER BY finished_at DESC))[1] AS last_error,
           (array_agg(id::text ORDER BY finished_at DESC))[1:5] AS ids
    FROM jobs
    WHERE status = 'dead' AND finished_at >= :since AND type <> :notify
    GROUP BY type
    ORDER BY type
    LIMIT 50
    """
)


async def job_failures_rule(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    window = cfg.alert_failure_window_minutes
    since = now - timedelta(minutes=window)
    rows = (
        await db.execute(_DEAD_JOBS_SQL, {"since": since, "notify": NOTIFY_JOB})
    ).all()
    return [
        Finding(
            rule=c.JOB_FAILURES,
            dedupe_key=f"{c.JOB_FAILURES}:{row.type}",
            severity=Severity.WARNING,
            title=f"{row.n} '{row.type}' job(s) failed permanently",
            summary=(
                f"{row.n} '{row.type}' background job(s) exhausted their retries "
                f"in the last {window} minutes."
            ),
            component="Job worker",
            details={
                "value": int(row.n),
                "threshold": 1,
                "unit": "jobs",
                "window_minutes": window,
                "counts": {"dead_jobs": int(row.n)},
                "job_type": row.type,
                "last_error": safe_text(row.last_error),
                "job_ids": list(row.ids or []),
            },
        )
        for row in rows
    ]


async def ingestion_failures_rule(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    window = cfg.alert_failure_window_minutes
    since = naive_utc(now) - timedelta(minutes=window)
    is_ingest = AuditLog.detail["path"].as_string().in_(INGEST_PATHS)
    count = int(
        await db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "server_error",
                AuditLog.created_at >= since,
                is_ingest,
            )
        )
        or 0
    )
    if count == 0:
        return []
    return [
        Finding(
            rule=c.INGESTION_FAILURES,
            dedupe_key=c.INGESTION_FAILURES,
            severity=Severity.WARNING,
            title=f"{count} document ingestion failure(s)",
            summary=(
                f"{count} upload(s) to /ingest failed with a server error in the "
                f"last {window} minutes."
            ),
            component="Ingestion",
            details={
                "value": count,
                "threshold": 1,
                "unit": "failures",
                "window_minutes": window,
                "counts": {"failed_uploads": count},
                "request_ids": await _recent_request_ids(
                    db, "server_error", since, is_ingest
                ),
            },
        )
    ]


_ONLINE_EVAL_FLAGS_SQL = text(
    """
    SELECT flag, count(*)
    FROM audit_log a
    CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(CAST(a.detail -> 'guardrail_flags' AS jsonb)) = 'array'
             THEN CAST(a.detail -> 'guardrail_flags' AS jsonb)
             ELSE '[]'::jsonb END
    ) AS flag
    WHERE a.action = 'online_eval' AND a.created_at >= :since
    GROUP BY flag
    ORDER BY flag
    LIMIT 20
    """
)


async def online_eval_rule(
    db: AsyncSession, now: datetime, cfg: Settings
) -> list[Finding]:
    window = cfg.alert_failure_window_minutes
    since = naive_utc(now) - timedelta(minutes=window)
    rows = (await db.execute(_ONLINE_EVAL_FLAGS_SQL, {"since": since})).all()
    by_flag = {str(f): int(n) for f, n in rows}
    total = int(
        await db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "online_eval", AuditLog.created_at >= since
            )
        )
        or 0
    )
    leaked = any(f in _LEAK_FLAGS for f in by_flag)
    if total < cfg.alert_online_eval_violations_threshold and not leaked:
        return []
    return [
        Finding(
            rule=c.ONLINE_EVAL_VIOLATIONS,
            dedupe_key=c.ONLINE_EVAL_VIOLATIONS,
            severity=Severity.CRITICAL if leaked else Severity.WARNING,
            title=f"{total} answers flagged by online evaluation",
            summary=(
                f"Online evaluation flagged {total} answers in the last {window} "
                "minutes" + (" including a secret leak." if leaked else ".")
            ),
            component="Answer quality",
            details={
                "value": total,
                "threshold": cfg.alert_online_eval_violations_threshold,
                "unit": "answers",
                "window_minutes": window,
                "counts": by_flag,
                "request_ids": await _recent_request_ids(db, "online_eval", since),
            },
        )
    ]


DATABASE_RULE_FUNCS: dict[str, Rule] = {
    c.ERROR_RATE: error_rate_rule,
    c.P95_LATENCY: p95_latency_rule,
    c.LLM_FAILURES: llm_failures_rule,
    c.MONTHLY_BUDGET: monthly_budget_rule,
    c.FEEDBACK_SPIKE: feedback_spike_rule,
    c.ACCESS_REQUEST: access_request_rule,
    c.JOB_FAILURES: job_failures_rule,
    c.INGESTION_FAILURES: ingestion_failures_rule,
    c.ONLINE_EVAL_VIOLATIONS: online_eval_rule,
    c.AZURE_QUOTA_NEAR_LIMIT: azure_quota_rule,
    c.AZURE_RESOURCE_UNHEALTHY: azure_unhealthy_rule,
    c.AZURE_SYNC_FAILING: azure_sync_failing_rule,
    # One pass over resource_samples raises all five resource rules.
    RESOURCES_GROUP: resource_rule,
}

# Registry key -> catalogue rule ids it evaluates (a key not listed covers
# itself). Only rules whose function ran cleanly may auto-resolve.
RULE_GROUPS: dict[str, frozenset[str]] = {RESOURCES_GROUP: c.RESOURCE_RULES}


def rules_covered(key: str) -> frozenset[str]:
    return RULE_GROUPS.get(key, frozenset({key}))
