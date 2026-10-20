"""Azure vs AskDIQ reconciliation and metric series (read models, Phase 2).

Built from Postgres only (``azure_metric_points`` plus AskDIQ's own records)
and served as snapshots (``azure.metrics.summary`` / ``azure.metrics.series``),
recomputed by the scheduler after every metrics poll. Never calls Azure.

Azure side, per deployment and UTC hour: a PT1H point once its hour has
closed before it was fetched (complete), else the PT1M points of that hour
summed (totals) / averaged (averages) / maxed (maxima), else the partial
PT1H point. Hours are then rolled up to UTC days and the last 24 h.

AskDIQ side (all naive-UTC tables), mapped to Azure deployment names
case-insensitively through Settings > Models (``model_settings``):

- LLM deployment: assistant ``chat_messages`` (one Azure request per LLM
  attempt in ``token_details.llm_attempts``; prompt / completion tokens),
  failed attempts and their 429s from ``audit_log`` (``llm_call_failed``;
  circuit-open failures never reached Azure), and the LLM stage latency
  (``latency_metrics.llm_ms``). ``usage_turns`` mirrors these assistant rows.
- Insight classifier calls (``insight_llm_usage``), by their ``model``.
- Embedding deployment: query + guardrail embedding tokens of the assistant
  rows and the embedding stage latency (``latency_metrics.embedding_ms``).
  Ingestion embeddings are not recorded per call, so they show as "not seen".

The in-process quota tracker (``app.core.quota``) only keeps a rolling 60 s
window per process, so persisted 429s come from the audit trail instead.
Chat history is attributed to the current LLM deployment.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.azure_insights import metrics as m
from app.azure_insights import status as sync_status
from app.azure_insights.schemas import (
    AzureMetricSeriesRead,
    AzureMetricsSummaryRead,
    DeploymentRole,
    DeploymentTokens,
    MetricSeries,
    PreviewCostState,
    ReconciliationCell,
    ReconciliationRow,
    SeriesPoint,
)
from app.core.config import Settings, get_settings
from app.dashboard.snapshots import Gather, register
from app.jobs.queue import utcnow
from app.model_settings.service import resolve
from app.models.db_models import ModelSettings

DEFAULT_DAYS = 7
MAX_DAYS = 90
# A PT1H point fetched this long after its hour started covers the whole hour.
HOUR_COMPLETE_AFTER = timedelta(minutes=75)
RANGES: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}
# PT1M series are served for short ranges only (payload size).
MINUTE_RANGES = frozenset({"1h", "6h"})
ALL = "*"
SERIES_DEFAULT: dict[str, Any] = {
    "deployment": ALL,
    "metric": m.TOKEN_TRANSACTION,
    "range": "7d",
    "grain": m.GRAIN_HOUR,
}
_STATUS_429 = "429"
_PCT = 100.0

Key = tuple[str, str, str, datetime]  # deployment, metric, status_code, hour


@dataclass(slots=True)
class Agg:
    """Totals summed, averages averaged (per bucket), maxima maxed."""

    total: float | None = None
    avg_sum: float = 0.0
    avg_n: int = 0
    maximum: float | None = None

    def add(
        self, total: float | None, average: float | None, maximum: float | None
    ) -> None:
        if total is not None:
            self.total = (self.total or 0.0) + total
        if average is not None:
            self.avg_sum += average
            self.avg_n += 1
        if maximum is not None:
            self.maximum = (
                maximum if self.maximum is None else max(self.maximum, maximum)
            )

    @property
    def average(self) -> float | None:
        return self.avg_sum / self.avg_n if self.avg_n else None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def floor_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Azure side
# ---------------------------------------------------------------------------

_MINUTE_HOURS_SQL = text(
    """
    SELECT deployment, metric, status_code,
           date_trunc('hour', ts AT TIME ZONE 'UTC') AS h,
           sum(total) AS total, avg(average) AS average, max(maximum) AS maximum
    FROM azure_metric_points
    WHERE grain = 'PT1M' AND ts >= :since AND ts < :until
    GROUP BY 1, 2, 3, 4
    """
)
_HOUR_POINTS_SQL = text(
    """
    SELECT deployment, metric, status_code, ts AT TIME ZONE 'UTC' AS h,
           total, average, maximum, fetched_at >= ts + :complete AS complete
    FROM azure_metric_points
    WHERE grain = 'PT1H' AND ts >= :since AND ts < :until
    """
)
_MINUTE_POINTS_SQL = text(
    """
    SELECT deployment, status_code, ts, total, average, maximum
    FROM azure_metric_points
    WHERE grain = 'PT1M' AND metric = :metric AND ts >= :since AND ts < :until
      AND (:deployment = '*' OR lower(deployment) = lower(:deployment))
    ORDER BY deployment, status_code, ts
    """
)
_LAST_POINT_SQL = text("SELECT max(ts) FROM azure_metric_points")


Values = tuple[float | None, float | None, float | None]


async def azure_hours(
    session: AsyncSession, since: datetime, until: datetime
) -> dict[Key, Values]:
    """(deployment, metric, status, UTC hour) -> (total, average, maximum)."""
    params = {"since": since, "until": until}
    minute: dict[Key, Values] = {
        (r.deployment, r.metric, r.status_code, _utc(r.h)): (
            r.total,
            r.average,
            r.maximum,
        )
        for r in (await session.execute(_MINUTE_HOURS_SQL, params)).all()
    }
    merged: dict[Key, Values] = dict(minute)
    rows = await session.execute(
        _HOUR_POINTS_SQL, {**params, "complete": HOUR_COMPLETE_AFTER}
    )
    for r in rows.all():
        key = (r.deployment, r.metric, r.status_code, _utc(r.h))
        if r.complete or key not in minute:
            merged[key] = (r.total, r.average, r.maximum)
    return merged


@dataclass(slots=True)
class AzureBucket:  # pylint: disable=too-many-instance-attributes
    """Azure measures of one deployment in one bucket (day or 24 h)."""

    requests: Agg = field(default_factory=Agg)
    throttled: Agg = field(default_factory=Agg)
    tokens_in: Agg = field(default_factory=Agg)
    tokens_out: Agg = field(default_factory=Agg)
    tokens_total: Agg = field(default_factory=Agg)
    ttlt: Agg = field(default_factory=Agg)
    ttft: Agg = field(default_factory=Agg)
    availability: Agg = field(default_factory=Agg)
    cost: Agg = field(default_factory=Agg)

    def add(self, metric: str, status_code: str, values: Values) -> None:
        total, average, maximum = values
        target = {
            m.PROMPT_TOKENS: self.tokens_in,
            m.GENERATED_TOKENS: self.tokens_out,
            m.TOKEN_TRANSACTION: self.tokens_total,
            m.TTLT_MS: self.ttlt,
            m.TTFT_MS: self.ttft,
            m.AVAILABILITY: self.availability,
            m.ESTIMATED_COST: self.cost,
        }.get(metric)
        if metric == m.REQUESTS:
            self.requests.add(total, None, None)
            if status_code == _STATUS_429:
                self.throttled.add(total, None, None)
            else:
                # Every status was returned: a missing 429 series means none.
                self.throttled.add(0.0, None, None)
            return
        if target is not None:
            target.add(total, average, maximum)

    @property
    def total_tokens(self) -> float | None:
        if self.tokens_total.total is not None:
            return self.tokens_total.total
        parts = [
            a.total for a in (self.tokens_in, self.tokens_out) if a.total is not None
        ]
        return sum(parts) if parts else None


# ---------------------------------------------------------------------------
# AskDIQ side
# ---------------------------------------------------------------------------

_CHAT_SQL = text(
    """
    SELECT date_trunc('hour', created_at) AS h,
           sum(CASE
                 WHEN json_typeof(token_details -> 'llm_attempts') = 'number'
                   THEN (token_details ->> 'llm_attempts')::int
                 WHEN prompt_tokens IS NOT NULL OR completion_tokens IS NOT NULL
                   THEN 1
                 ELSE 0
               END) AS llm_requests,
           sum(coalesce(prompt_tokens, 0)) AS tokens_in,
           sum(coalesce(completion_tokens, 0)) AS tokens_out,
           sum(coalesce(embedding_tokens, 0) + coalesce(guardrail_tokens, 0))
             AS embed_tokens,
           count(*) FILTER (WHERE coalesce(embedding_tokens, 0) > 0)
             + count(*) FILTER (WHERE coalesce(guardrail_tokens, 0) > 0)
             AS embed_requests
    FROM chat_messages
    WHERE role = 'assistant' AND created_at >= :since AND created_at < :until
    GROUP BY 1
    """
)
_FAILED_SQL = text(
    """
    SELECT date_trunc('hour', created_at) AS h,
           count(*) AS failed,
           count(*) FILTER (WHERE detail ->> 'status_code' = '429') AS throttled
    FROM audit_log
    WHERE action = 'llm_call_failed' AND created_at >= :since AND created_at < :until
      AND coalesce(detail ->> 'error_type', '') <> 'CircuitOpenError'
    GROUP BY 1
    """
)
_INSIGHT_SQL = text(
    """
    SELECT lower(model) AS model, date_trunc('hour', created_at AT TIME ZONE 'UTC') AS h,
           count(*) AS requests, sum(prompt_tokens) AS tokens_in,
           sum(completion_tokens) AS tokens_out
    FROM insight_llm_usage
    WHERE created_at >= :since_tz AND created_at < :until_tz
    GROUP BY 1, 2
    """
)
_LATENCY_SQL = text(
    """
    SELECT date_trunc('hour', created_at) AS h,
           sum(llm_ms) AS llm_sum, count(llm_ms) AS llm_n, max(llm_ms) AS llm_max,
           sum(embedding_ms) AS emb_sum, count(embedding_ms) AS emb_n,
           max(embedding_ms) AS emb_max
    FROM latency_metrics
    WHERE created_at >= :since AND created_at < :until
    GROUP BY 1
    """
)


@dataclass(slots=True)
class AppBucket:  # pylint: disable=too-many-instance-attributes
    requests: float = 0.0
    throttled: float = 0.0
    tokens_in: float = 0.0
    tokens_out: float = 0.0
    lat_sum: float = 0.0
    lat_n: int = 0
    lat_max: float | None = None
    seen: bool = False

    def add(  # pylint: disable=too-many-arguments
        self,
        *,
        requests: float = 0.0,
        throttled: float = 0.0,
        tokens_in: float = 0.0,
        tokens_out: float = 0.0,
    ) -> None:
        self.requests += requests
        self.throttled += throttled
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.seen = True

    def add_latency(self, total: float, n: int, maximum: float | None) -> None:
        self.lat_sum += total
        self.lat_n += n
        if maximum is not None:
            self.lat_max = (
                maximum if self.lat_max is None else max(self.lat_max, maximum)
            )
        self.seen = True

    def merge(self, other: AppBucket) -> None:
        self.add(
            requests=other.requests,
            throttled=other.throttled,
            tokens_in=other.tokens_in,
            tokens_out=other.tokens_out,
        )
        self.seen = self.seen or other.seen
        if other.lat_n:
            self.add_latency(other.lat_sum, other.lat_n, other.lat_max)

    @property
    def latency_avg(self) -> float | None:
        return self.lat_sum / self.lat_n if self.lat_n else None


def _f(value: Any) -> float:
    return float(value or 0)


async def app_hours(  # pylint: disable=too-many-locals
    session: AsyncSession,
    since: datetime,
    until: datetime,
    llm: str,
    embedding: str,
) -> dict[tuple[str, datetime], AppBucket]:
    """(lower-case deployment, UTC hour) -> what AskDIQ recorded."""
    naive = {"since": _naive(since), "until": _naive(until)}
    out: dict[tuple[str, datetime], AppBucket] = defaultdict(AppBucket)
    for r in (await session.execute(_CHAT_SQL, naive)).all():
        h = _utc(r.h)
        out[(llm, h)].add(
            requests=_f(r.llm_requests),
            tokens_in=_f(r.tokens_in),
            tokens_out=_f(r.tokens_out),
        )
        if r.embed_tokens or r.embed_requests:
            out[(embedding, h)].add(
                requests=_f(r.embed_requests), tokens_in=_f(r.embed_tokens)
            )
    for r in (await session.execute(_FAILED_SQL, naive)).all():
        out[(llm, _utc(r.h))].add(requests=_f(r.failed), throttled=_f(r.throttled))
    insight = {"since_tz": since, "until_tz": until}
    for r in (await session.execute(_INSIGHT_SQL, insight)).all():
        out[(r.model, _utc(r.h))].add(
            requests=_f(r.requests),
            tokens_in=_f(r.tokens_in),
            tokens_out=_f(r.tokens_out),
        )
    for r in (await session.execute(_LATENCY_SQL, naive)).all():
        h = _utc(r.h)
        if r.llm_n:
            out[(llm, h)].add_latency(_f(r.llm_sum), int(r.llm_n), r.llm_max)
        if r.emb_n:
            out[(embedding, h)].add_latency(_f(r.emb_sum), int(r.emb_n), r.emb_max)
    return out


# ---------------------------------------------------------------------------
# Reconciliation math
# ---------------------------------------------------------------------------


def drift_pct(azure: float | None, askdiq: float | None) -> float | None:
    """``|azure - askdiq| / azure * 100``; 100 when Azure saw none but AskDIQ
    recorded some; None without Azure data or when both are zero."""
    if azure is None:
        return None
    mine = askdiq or 0.0
    if azure == 0:
        return _PCT if mine > 0 else None
    return round(abs(azure - mine) / azure * _PCT, 2)


def cell(
    azure: float | None,
    askdiq: float | None,
    threshold: float,
    *,
    flag: bool = True,
) -> ReconciliationCell:
    drift = drift_pct(azure, askdiq)
    return ReconciliationCell(
        azure=None if azure is None else round(azure, 3),
        askdiq=None if askdiq is None else round(askdiq, 3),
        delta=None if azure is None else round(azure - (askdiq or 0.0), 3),
        drift_pct=drift,
        drifted=bool(flag and drift is not None and drift >= threshold),
    )


def _rounded(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def not_seen(azure: float | None, askdiq: float | None) -> float | None:
    if azure is None:
        return None
    return round(max(0.0, azure - (askdiq or 0.0)), 3)


def build_row(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    deployment: str,
    role: DeploymentRole,
    day: str | None,
    azure: AzureBucket | None,
    app: AppBucket | None,
    threshold: float,
) -> ReconciliationRow:
    used = role != "other"
    has_app = used and app is not None and app.seen

    def mine(value: float) -> float | None:
        return value if has_app else (0.0 if used else None)

    a = azure or AzureBucket()
    b = app or AppBucket()
    app_tokens = (b.tokens_in + b.tokens_out) if has_app else (0.0 if used else None)
    row = ReconciliationRow(
        deployment=deployment,
        role=role,
        day=day,
        requests=cell(a.requests.total, mine(b.requests), threshold, flag=used),
        tokens_in=cell(a.tokens_in.total, mine(b.tokens_in), threshold, flag=used),
        tokens_out=cell(a.tokens_out.total, mine(b.tokens_out), threshold, flag=used),
        throttled=cell(a.throttled.total, mine(b.throttled), threshold, flag=used),
        latency_avg_ms=cell(
            a.ttlt.average, b.latency_avg if has_app else None, threshold, flag=False
        ),
        latency_max_ms=cell(
            a.ttlt.maximum, b.lat_max if has_app else None, threshold, flag=False
        ),
        ttft_avg_ms=_rounded(a.ttft.average, 1),
        availability_pct=_rounded(a.availability.average, 3),
        estimated_cost_usd=_rounded(a.cost.total, 6),
        not_seen_tokens=not_seen(a.total_tokens, app_tokens),
    )
    row.drifted = any(
        c.drifted for c in (row.requests, row.tokens_in, row.tokens_out, row.throttled)
    )
    return row


@dataclass(slots=True)
class Deployments:
    """Azure deployment names (as Azure reports them) and AskDIQ's roles."""

    llm: str
    embedding: str
    names: dict[str, str] = field(default_factory=dict)  # lower -> display

    def role(self, lower: str) -> DeploymentRole:
        if lower == self.llm:
            return "llm"
        if lower == self.embedding:
            return "embedding"
        return "other"

    def note(self, name: str) -> str:
        lower = name.lower()
        self.names.setdefault(lower, name)
        return lower

    def ordered(self) -> list[str]:
        rank = {"llm": 0, "embedding": 1, "other": 2}
        return sorted(self.names, key=lambda d: (rank[self.role(d)], d))


async def current_deployments(session: AsyncSession, settings: Settings) -> Deployments:
    row = (
        await session.execute(select(ModelSettings).where(ModelSettings.id == 1))
    ).scalar_one_or_none()
    effective = resolve(row, settings)
    deps = Deployments(
        llm=effective.llm_deployment.lower(),
        embedding=effective.embedding_model.lower(),
    )
    deps.names[deps.llm] = effective.llm_deployment
    deps.names.setdefault(deps.embedding, effective.embedding_model)
    return deps


def _bucket_azure(
    hours: dict[Key, Values], deps: Deployments, bucket_of: Any
) -> dict[tuple[str, Any], AzureBucket]:
    out: dict[tuple[str, Any], AzureBucket] = defaultdict(AzureBucket)
    for (deployment, metric, status_code, hour), values in hours.items():
        bucket = bucket_of(hour)
        if bucket is None:
            continue
        out[(deps.note(deployment), bucket)].add(metric, status_code, values)
    return out


def _bucket_app(
    hours: dict[tuple[str, datetime], AppBucket], bucket_of: Any
) -> dict[tuple[str, Any], AppBucket]:
    out: dict[tuple[str, Any], AppBucket] = defaultdict(AppBucket)
    for (deployment, hour), values in hours.items():
        bucket = bucket_of(hour)
        if bucket is not None:
            out[(deployment, bucket)].merge(values)
    return out


def _token_totals(
    rows: Iterable[ReconciliationRow], deps: Deployments
) -> list[DeploymentTokens]:
    acc: dict[str, dict[str, float | None]] = {}
    for row in rows:
        lower = row.deployment.lower()
        item = acc.setdefault(
            lower, {"in": None, "out": None, "app": None, "total": None}
        )
        for key, value in (
            ("in", row.tokens_in.azure),
            ("out", row.tokens_out.azure),
            ("app", _app_tokens(row)),
        ):
            if value is not None:
                item[key] = (item[key] or 0.0) + value
    totals = []
    for lower in deps.ordered():
        item = acc.get(lower)
        if item is None:
            continue
        azure = None
        if item["in"] is not None or item["out"] is not None:
            azure = (item["in"] or 0.0) + (item["out"] or 0.0)
        unseen = not_seen(azure, item["app"])
        totals.append(
            DeploymentTokens(
                deployment=deps.names[lower],
                role=deps.role(lower),
                azure_tokens=azure,
                azure_tokens_in=item["in"],
                azure_tokens_out=item["out"],
                askdiq_tokens=item["app"],
                not_seen_tokens=unseen,
                not_seen_pct=(
                    round(unseen / azure * _PCT, 2)
                    if azure and unseen is not None
                    else None
                ),
            )
        )
    return totals


def _app_tokens(row: ReconciliationRow) -> float | None:
    if row.tokens_in.askdiq is None and row.tokens_out.askdiq is None:
        return None
    return (row.tokens_in.askdiq or 0.0) + (row.tokens_out.askdiq or 0.0)


async def build_summary(  # pylint: disable=too-many-locals
    session: AsyncSession,
    settings: Settings,
    days: int = DEFAULT_DAYS,
    now: datetime | None = None,
) -> AzureMetricsSummaryRead:
    now = now or utcnow()
    days = max(1, min(MAX_DAYS, int(days)))
    today = now.date()
    first_day = today - timedelta(days=days - 1)
    since = min(
        datetime(first_day.year, first_day.month, first_day.day, tzinfo=UTC),
        floor_hour(now) - timedelta(hours=23),
    )
    until = floor_hour(now) + timedelta(hours=1)
    deps = await current_deployments(session, settings)
    azure = await azure_hours(session, since, until)
    app = await app_hours(session, since, until, deps.llm, deps.embedding)
    for deployment, _hour in app:
        deps.names.setdefault(deployment, deployment)
    threshold = settings.azure_reconciliation_drift_pct

    def day_of(hour: datetime) -> date | None:
        return hour.date() if hour.date() >= first_day else None

    recent_start = floor_hour(now) - timedelta(hours=23)

    def recent(hour: datetime) -> str | None:
        return "24h" if hour >= recent_start else None

    az_days, app_days = _bucket_azure(azure, deps, day_of), _bucket_app(app, day_of)
    az_24, app_24 = _bucket_azure(azure, deps, recent), _bucket_app(app, recent)

    daily: list[ReconciliationRow] = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        for lower in deps.ordered():
            a, b = az_days.get((lower, day)), app_days.get((lower, day))
            if a is None and b is None:
                continue
            daily.append(
                build_row(
                    deps.names[lower],
                    deps.role(lower),
                    day.isoformat(),
                    a,
                    b,
                    threshold,
                )
            )
    last_24h = [
        build_row(
            deps.names[lower],
            deps.role(lower),
            None,
            az_24.get((lower, "24h")),
            app_24.get((lower, "24h")),
            threshold,
        )
        for lower in deps.ordered()
        if (lower, "24h") in az_24
        or (lower, "24h") in app_24
        or deps.role(lower) != "other"
    ]
    row = (await sync_status.read_all(session)).get(m.SOURCE_NAME)
    preview = m.PreviewState.from_detail(row.detail if row is not None else None)
    last_point = (await session.execute(_LAST_POINT_SQL)).scalar_one_or_none()
    return AzureMetricsSummaryRead(
        connected=settings.azure_insights_configured,
        state=_state(row, settings),
        as_of=row.last_success_at if row is not None else None,
        last_point_at=last_point,
        days=days,
        drift_threshold_pct=threshold,
        llm_deployment=deps.names[deps.llm],
        embedding_deployment=deps.names[deps.embedding],
        last_24h=last_24h,
        daily=daily,
        tokens=_token_totals(daily, deps),
        preview_cost=PreviewCostState(
            enabled=not preview.disabled,
            empty_since=preview.empty_since.isoformat()
            if preview.empty_since
            else None,
            last_seen=preview.last_seen.isoformat() if preview.last_seen else None,
        ),
        generated_at=now,
    )


def _state(row: Any, settings: Settings) -> Any:
    if not settings.azure_insights_configured:
        return "not_configured"
    return row.state if row is not None else "never_run"


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------


async def build_series(  # pylint: disable=too-many-arguments,too-many-locals
    session: AsyncSession,
    settings: Settings,
    *,
    metric: str,
    deployment: str = ALL,
    range_: str = "24h",
    grain: str = m.GRAIN_HOUR,
    now: datetime | None = None,
) -> AzureMetricSeriesRead:
    """Points of ``metric`` per deployment (and status code for requests)."""
    now = now or utcnow()
    span = RANGES[range_]
    if grain == m.GRAIN_MINUTE:
        until = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        since = until - span
        rows = (
            await session.execute(
                _MINUTE_POINTS_SQL,
                {
                    "metric": metric,
                    "since": since,
                    "until": until,
                    "deployment": deployment,
                },
            )
        ).all()
        grouped: dict[tuple[str, str], list[SeriesPoint]] = defaultdict(list)
        for r in rows:
            grouped[(r.deployment, r.status_code)].append(
                SeriesPoint(
                    t=_utc(r.ts), total=r.total, average=r.average, maximum=r.maximum
                )
            )
    else:
        until = floor_hour(now) + timedelta(hours=1)
        since = until - span
        hours = await azure_hours(session, since, until)
        grouped = defaultdict(list)
        wanted = deployment.lower()
        for (dep, met, status_code, hour), (total, average, maximum) in sorted(
            hours.items(), key=lambda kv: (kv[0][0], kv[0][2], kv[0][3])
        ):
            if met != metric or (deployment != ALL and dep.lower() != wanted):
                continue
            grouped[(dep, status_code)].append(
                SeriesPoint(t=hour, total=total, average=average, maximum=maximum)
            )
    status_row = (await sync_status.read_all(session)).get(m.SOURCE_NAME)
    return AzureMetricSeriesRead(
        connected=settings.azure_insights_configured,
        as_of=status_row.last_success_at if status_row is not None else None,
        metric=metric,  # type: ignore[arg-type]
        deployment=deployment,
        range=range_,  # type: ignore[arg-type]
        grain=grain,  # type: ignore[arg-type]
        since=since,
        until=until,
        series=[
            MetricSeries(deployment=dep, status_code=code, points=points)
            for (dep, code), points in sorted(grouped.items())
        ],
        generated_at=now,
    )


# ---------------------------------------------------------------------------
# Snapshot kinds (recomputed after every metrics poll)
# ---------------------------------------------------------------------------


def summary_params(days: int) -> dict[str, Any]:
    return {"days": days}


def series_params(
    metric: str, deployment: str, range_: str, grain: str
) -> dict[str, Any]:
    return {"deployment": deployment, "metric": metric, "range": range_, "grain": grain}


async def _summary_snapshot(gather: Gather, params: dict[str, Any]) -> dict:
    settings = get_settings()
    days = int(params.get("days", DEFAULT_DAYS))
    (payload,) = await gather(lambda db: build_summary(db, settings, days))
    return payload.model_dump(mode="json")


async def _series_snapshot(gather: Gather, params: dict[str, Any]) -> dict:
    settings = get_settings()
    (payload,) = await gather(
        lambda db: build_series(
            db,
            settings,
            metric=str(params["metric"]),
            deployment=str(params.get("deployment", ALL)),
            range_=str(params.get("range", "24h")),
            grain=str(params.get("grain", m.GRAIN_HOUR)),
        )
    )
    return payload.model_dump(mode="json")


register(m.SUMMARY_KIND, _summary_snapshot, [summary_params(DEFAULT_DAYS)])
register(m.SERIES_KIND, _series_snapshot, [dict(SERIES_DEFAULT)])
