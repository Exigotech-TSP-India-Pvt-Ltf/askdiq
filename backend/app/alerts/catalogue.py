"""Alert catalogue: every condition the platform alerts on.

Two kinds of rule:

- ``database`` rules read persisted rows and are evaluated by the
  ``alerts.evaluate`` job (at most once per ``ALERTS_EVALUATE_INTERVAL_SECONDS``
  across all workers). An open alert whose condition no longer holds is
  resolved on the next evaluation.
- ``process`` rules read per-process signals (circuit breaker, Azure quota
  and 429 counters, event-bus connection, database reachability). Every API
  process samples them (``app.alerts.sampler``); an alert nobody re-reports
  for ``ALERTS_LOCAL_RESOLVE_MINUTES`` is resolved.

Dedupe: one active alert per ``dedupe_key`` (Postgres partial unique index).
Cooldown: a new alert with a key emailed within ``ALERT_COOLDOWN_MINUTES`` is
stored and shown but not emailed again. Recipients: ``ALERT_EMAIL_TO`` for
every rule at or above ``ALERT_EMAIL_MIN_SEVERITY``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}


def severity_rank(value: str) -> int:
    try:
        return Severity(value).rank
    except ValueError:
        return 0


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class NotifyStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    SENT = "sent"
    RETRYING = "retrying"
    FAILED = "failed"
    DISABLED = "disabled"
    SKIPPED = "skipped"
    SUPPRESSED = "suppressed"


Source = Literal["database", "process"]


@dataclass(frozen=True, slots=True)
class AlertRule:
    id: str
    name: str
    component: str
    source: Source
    trigger: str
    severity: str
    console_path: str
    runbook: str


ERROR_RATE = "error_rate"
P95_LATENCY = "p95_latency"
LLM_FAILURES = "llm_failures"
MONTHLY_BUDGET = "monthly_budget"
FEEDBACK_SPIKE = "negative_feedback_spike"
ACCESS_REQUEST = "access_request_pending"
JOB_FAILURES = "job_failures"
INGESTION_FAILURES = "ingestion_failures"
ONLINE_EVAL_VIOLATIONS = "online_eval_violations"
LLM_CIRCUIT_OPEN = "llm_circuit_open"
AZURE_QUOTA = "azure_quota"
AZURE_THROTTLING = "azure_throttling"
EVENT_BUS_DOWN = "event_bus_disconnected"
DATABASE_UNAVAILABLE = "database_unavailable"

_GOVERNANCE = "/admin/governance"
_MONITORING = "/admin/governance?tab=Monitoring"
_LOGS = "/admin/governance?tab=Logs"

RULES: tuple[AlertRule, ...] = (
    AlertRule(
        ERROR_RATE,
        "Error rate",
        "API",
        "database",
        "Failed assistant turns + 5xx responses / all requests over "
        "GOVERNANCE_HEALTH_WINDOW_MINUTES, with at least ALERT_MIN_REQUESTS requests.",
        "warning at GOVERNANCE_HEALTH_ERROR_RATE_DEGRADED_PCT, critical at "
        "GOVERNANCE_HEALTH_ERROR_RATE_INCIDENT_PCT",
        _GOVERNANCE,
        "Open Governance > Logs filtered to ERROR, group by path, and check the "
        "Azure OpenAI and database health first.",
    ),
    AlertRule(
        P95_LATENCY,
        "p95 latency",
        "Query pipeline",
        "database",
        "95th percentile /query latency over GOVERNANCE_HEALTH_WINDOW_MINUTES "
        "(at least ALERT_LATENCY_MIN_SAMPLES requests).",
        "warning at GOVERNANCE_HEALTH_P95_DEGRADED_MS, critical at "
        "GOVERNANCE_HEALTH_P95_INCIDENT_MS",
        _MONITORING,
        "Compare the per-stage latency in Governance > Monitoring; a slow LLM "
        "stage usually points at Azure OpenAI throttling or capacity.",
    ),
    AlertRule(
        LLM_FAILURES,
        "LLM call failures",
        "Azure OpenAI",
        "database",
        "llm_call_failed audit events within ALERT_LLM_FAILURE_WINDOW_MINUTES.",
        "critical at ALERT_LLM_FAILURES_THRESHOLD",
        _LOGS,
        "Check the Azure OpenAI resource health, deployment name and API key; "
        "the error types below show whether it is throttling, timeouts or auth.",
    ),
    AlertRule(
        MONTHLY_BUDGET,
        "Monthly cost budget",
        "Cost",
        "database",
        "Month-to-date spend (UTC month) against MONTHLY_COST_BUDGET_USD.",
        "warning at ALERT_BUDGET_WARNING_PCT, critical at ALERT_BUDGET_CRITICAL_PCT",
        "/admin/usage",
        "Review the top users and sessions in Usage; raise the budget or the "
        "rate limits if the growth is expected.",
    ),
    AlertRule(
        FEEDBACK_SPIKE,
        "Negative feedback spike",
        "Answer quality",
        "database",
        "Share of 1-2 star ratings over ALERT_FEEDBACK_WINDOW_MINUTES above "
        "ALERT_FEEDBACK_NEGATIVE_PCT with at least ALERT_FEEDBACK_MIN_RATINGS ratings.",
        "warning",
        "/admin/feedback",
        "Open the feedback queue filtered to negative ratings and look for a "
        "shared topic, document or recent content change.",
    ),
    AlertRule(
        ACCESS_REQUEST,
        "Access request waiting",
        "User management",
        "database",
        "A pending dashboard access request (one alert per request; resolved "
        "when it is approved or rejected).",
        "info",
        "/admin/user-management?tab=requests",
        "Approve or reject the request in User Management > Requests.",
    ),
    AlertRule(
        JOB_FAILURES,
        "Background job failures",
        "Job worker",
        "database",
        "Jobs that exhausted their retries (status dead) within "
        "ALERT_FAILURE_WINDOW_MINUTES, per job type.",
        "warning",
        _LOGS,
        "Read last_error on the dead jobs (jobs table); fix the cause and "
        "re-queue them by setting status back to queued.",
    ),
    AlertRule(
        INGESTION_FAILURES,
        "Ingestion failures",
        "Ingestion",
        "database",
        "5xx responses from /ingest or /ingest/file within "
        "ALERT_FAILURE_WINDOW_MINUTES.",
        "warning",
        "/documents",
        "Search the backend logs for the request ids below (ingestion_failed) "
        "and retry the upload once the cause is fixed.",
    ),
    AlertRule(
        ONLINE_EVAL_VIOLATIONS,
        "Online evaluation violations",
        "Answer quality",
        "database",
        "Online evaluation flagged unfaithful answers or leaks within "
        "ALERT_FAILURE_WINDOW_MINUTES.",
        "warning at ALERT_ONLINE_EVAL_VIOLATIONS_THRESHOLD; critical on any "
        "secret leak",
        "/admin/evaluation-live",
        "Open Evaluation (live) and inspect the flagged turns; a secret leak "
        "needs the source document fixed or removed now.",
    ),
    AlertRule(
        LLM_CIRCUIT_OPEN,
        "LLM circuit breaker open",
        "Azure OpenAI",
        "process",
        "The chat-completion circuit breaker is open or half-open (repeated "
        "transient Azure failures); answers fail fast meanwhile.",
        "critical",
        _MONITORING,
        "Azure OpenAI is failing repeatedly: check its status page, quota and "
        "network path. The breaker closes by itself after a successful probe.",
    ),
    AlertRule(
        AZURE_QUOTA,
        "Azure OpenAI quota",
        "Azure OpenAI",
        "process",
        "Worst TPM/RPM utilisation (chat or embedding) in the last 60 s.",
        "warning at GOVERNANCE_HEALTH_QUOTA_DEGRADED_PCT, critical at "
        "GOVERNANCE_HEALTH_QUOTA_INCIDENT_PCT",
        _MONITORING,
        "Raise the deployment's TPM/RPM in Azure or lower traffic (rate limits).",
    ),
    AlertRule(
        AZURE_THROTTLING,
        "Azure OpenAI throttling (429)",
        "Azure OpenAI",
        "process",
        "429 responses from Azure OpenAI in the last 60 s.",
        "warning at ALERT_AZURE_429_THRESHOLD, critical at 5x that",
        _MONITORING,
        "The deployment is over its quota: raise TPM/RPM in Azure or reduce load.",
    ),
    AlertRule(
        EVENT_BUS_DOWN,
        "Event bus disconnected",
        "Event bus",
        "process",
        "The LISTEN/NOTIFY connection has been down for longer than "
        "ALERT_EVENT_BUS_GRACE_SECONDS (live updates and cross-worker cache "
        "invalidation are paused).",
        "warning",
        _MONITORING,
        "Check Postgres connectivity and max_connections; the bus reconnects "
        "with backoff on its own.",
    ),
    AlertRule(
        DATABASE_UNAVAILABLE,
        "Database unavailable",
        "Database",
        "process",
        "A process cannot reach Postgres. Emailed directly (it cannot be "
        "stored while the database is down) and recorded once it is back.",
        "critical",
        _GOVERNANCE,
        "Check the Postgres server, credentials, disk space and network; the "
        "API cannot serve requests until it is reachable again.",
    ),
)

RULES_BY_ID: dict[str, AlertRule] = {rule.id: rule for rule in RULES}
DATABASE_RULES = frozenset(r.id for r in RULES if r.source == "database")
PROCESS_RULES = frozenset(r.id for r in RULES if r.source == "process")


def rule_for(rule_id: str) -> AlertRule | None:
    return RULES_BY_ID.get(rule_id)
