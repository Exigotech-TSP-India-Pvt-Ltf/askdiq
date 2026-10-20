"""Azure Monitor platform metrics of the Foundry / Azure OpenAI account (Phase 2).

Free API only: ``GET {account}/providers/Microsoft.Insights/metrics`` (Metrics
- List) on the single account; never ``metrics:getBatch``, Log Analytics,
App Insights or alert rules. Every call goes through the budgeted ARM client,
so it counts against ``AZURE_METRICS_MONTHLY_CALL_CAP``.

Each poll (``azure.metrics``, every ``AZURE_METRICS_INTERVAL_SECONDS``):

- calls A-D below over the last 2 h at PT1M (late data is re-read);
- once a UTC day, calls A-D again over the last 3 days at PT1H (backfill);
- upserts every point into ``azure_metric_points`` on its natural key
  (deployment, metric, status_code, grain, ts) and prunes PT1M rows older
  than 7 days and PT1H rows older than 93 days;
- the scheduler then recomputes the ``azure.metrics.*`` snapshots and
  announces them on ``/admin/live``.

Call D (``FoundryModelEstimatedCost``) is a Preview metric: it is disabled
automatically after 3 days without data and re-probed weekly on the backfill.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.azure_insights import status as sync_status
from app.azure_insights.client import AzureClient
from app.azure_insights.errors import AzureNotConfiguredError
from app.azure_insights.scheduler import (
    AzureSource,
    SourceContext,
    SourceResult,
    register_source,
)
from app.core.config import Settings
from app.core.logging_config import get_logger
from app.models.db_models import AzureMetricPoint

logger = get_logger(__name__)

SOURCE_NAME = "metrics"
API_VERSION = "2023-10-01"
NAMESPACE = "Microsoft.CognitiveServices/accounts"
GRAIN_MINUTE = "PT1M"
GRAIN_HOUR = "PT1H"
GRAINS = (GRAIN_MINUTE, GRAIN_HOUR)
# Late data: every poll re-reads this window at PT1M.
POLL_WINDOW = timedelta(hours=2)
# Once a UTC day: this window at PT1H.
BACKFILL_WINDOW = timedelta(days=3)
RETENTION = {GRAIN_MINUTE: timedelta(days=7), GRAIN_HOUR: timedelta(days=93)}
# Time series per call (the API default is 10).
TOP_SERIES = 50
SUCCESS = "Success"
UPSERT_CHUNK = 1000

# Metric names (one place: Microsoft is migrating names; plan §7).
PROMPT_TOKENS = "ProcessedPromptTokens"
GENERATED_TOKENS = "GeneratedTokens"
TOKEN_TRANSACTION = "TokenTransaction"  # noqa: S105 - a metric name
REQUESTS = "AzureOpenAIRequests"
TTLT_MS = "AzureOpenAITTLTInMS"
TTFT_MS = "AzureOpenAINormalizedTTFTInMS"
AVAILABILITY = "AzureOpenAIAvailabilityRate"
ESTIMATED_COST = "FoundryModelEstimatedCost"

DEPLOYMENT_DIMENSION = "modeldeploymentname"
STATUS_DIMENSION = "statuscode"
ALL_DEPLOYMENTS = "ModelDeploymentName eq '*'"

# Preview cost metric: disabled after this many empty days, re-probed weekly.
PREVIEW_EMPTY_DAYS = 3
PREVIEW_REPROBE_DAYS = 7


@dataclass(frozen=True, slots=True)
class MetricCall:
    """One Metrics - List request (plan §4.2)."""

    key: str
    metrics: tuple[str, ...]
    aggregation: str
    by_status: bool = False
    preview: bool = False

    @property
    def filter(self) -> str:
        if self.by_status:
            return f"{ALL_DEPLOYMENTS} and StatusCode eq '*'"
        return ALL_DEPLOYMENTS


CALL_TOKENS = MetricCall(
    "tokens", (PROMPT_TOKENS, GENERATED_TOKENS, TOKEN_TRANSACTION), "Total"
)
CALL_REQUESTS = MetricCall("requests", (REQUESTS,), "Total", by_status=True)
CALL_LATENCY = MetricCall(
    "latency", (TTLT_MS, TTFT_MS, AVAILABILITY), "Average,Maximum"
)
CALL_COST = MetricCall("cost", (ESTIMATED_COST,), "Total", preview=True)
CALLS: tuple[MetricCall, ...] = (CALL_TOKENS, CALL_REQUESTS, CALL_LATENCY, CALL_COST)
KNOWN_METRICS: tuple[str, ...] = tuple(m for call in CALLS for m in call.metrics)


@dataclass(frozen=True, slots=True)
class MetricPoint:  # pylint: disable=too-many-instance-attributes
    deployment: str
    metric: str
    status_code: str
    grain: str
    ts: datetime
    total: float | None
    average: float | None
    maximum: float | None


@dataclass(slots=True)
class ParsedResponse:
    points: list[MetricPoint]
    grain: str | None
    # Metric -> errorCode, for metrics Azure did not answer with Success.
    failed: dict[str, str]


# ---------------------------------------------------------------------------
# Request + parsing
# ---------------------------------------------------------------------------


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def metrics_path(account_id: str) -> str:
    return f"{account_id}/providers/Microsoft.Insights/metrics"


def request_params(
    call: MetricCall, grain: str, start: datetime, end: datetime
) -> dict[str, str]:
    """Query string of one call (plan §4.2: exact parameters)."""
    return {
        "api-version": API_VERSION,
        "metricnamespace": NAMESPACE,
        "metricnames": ",".join(call.metrics),
        "aggregation": call.aggregation,
        "interval": grain,
        "timespan": f"{_iso(start)}/{_iso(end)}",
        "$filter": call.filter,
        "top": str(TOP_SERIES),
        "AutoAdjustTimegrain": "true",
        "ValidateDimensions": "false",
    }


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _name(node: Any) -> str:
    """``name.value`` of a metric or dimension (case kept)."""
    if isinstance(node, dict):
        value = node.get("value")
        return value if isinstance(value, str) else ""
    return node if isinstance(node, str) else ""


def _dimensions(series: dict[str, Any]) -> dict[str, str]:
    """Dimension name (lower-case) -> value, from ``metadatavalues``."""
    found: dict[str, str] = {}
    for item in series.get("metadatavalues") or []:
        if not isinstance(item, dict):
            continue
        key = _name(item.get("name")).lower()
        value = item.get("value")
        if key and isinstance(value, str):
            found[key] = value
    return found


def _series_points(
    metric: str, grain: str, series: dict[str, Any]
) -> Iterator[MetricPoint]:
    dims = _dimensions(series)
    deployment = dims.get(DEPLOYMENT_DIMENSION, "").strip()
    if not deployment:
        return
    status_code = dims.get(STATUS_DIMENSION, "").strip()[:16]
    for item in series.get("data") or []:
        if not isinstance(item, dict):
            continue
        ts = _parse_ts(item.get("timeStamp"))
        if ts is None:
            continue
        total = _float(item.get("total"))
        average = _float(item.get("average"))
        maximum = _float(item.get("maximum"))
        if total is None and average is None and maximum is None:
            continue  # no data in this bucket
        yield MetricPoint(
            deployment[:128], metric, status_code, grain, ts, total, average, maximum
        )


def parse_response(body: dict[str, Any], requested_grain: str) -> ParsedResponse:
    """Points of a Metrics - List body, matched by metric name and dimension
    metadata (never by position). A metric whose ``errorCode`` is not
    ``Success`` is reported in ``failed`` and skipped. Points are stored only
    at PT1M / PT1H: an auto-adjusted grain we do not keep is dropped."""
    grain = body.get("interval") or requested_grain
    if grain not in GRAINS:
        logger.warning("azure_metrics_grain_adjusted", grain=str(grain)[:16])
        return ParsedResponse([], None, {})
    points: list[MetricPoint] = []
    failed: dict[str, str] = {}
    for metric in body.get("value") or []:
        if not isinstance(metric, dict):
            continue
        name = _name(metric.get("name"))
        if not name:
            continue
        error = metric.get("errorCode") or SUCCESS
        if error != SUCCESS:
            failed[name] = str(error)[:80]
            continue
        for series in metric.get("timeseries") or []:
            if isinstance(series, dict):
                points.extend(_series_points(name, grain, series))
    return ParsedResponse(points, grain, failed)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


async def upsert_points(
    session: AsyncSession, points: Iterable[MetricPoint], fetched_at: datetime
) -> int:
    """``INSERT ... ON CONFLICT (natural key) DO UPDATE``; returns rows written.
    The last point wins when a response repeats a key."""
    unique: dict[tuple[str, str, str, str, datetime], dict[str, Any]] = {}
    for p in points:
        unique[(p.deployment, p.metric, p.status_code, p.grain, p.ts)] = {
            "deployment": p.deployment,
            "metric": p.metric,
            "status_code": p.status_code,
            "grain": p.grain,
            "ts": p.ts,
            "total": p.total,
            "average": p.average,
            "maximum": p.maximum,
            "fetched_at": fetched_at,
        }
    rows = list(unique.values())
    table = AzureMetricPoint.__table__
    for chunk in _chunks(rows, UPSERT_CHUNK):
        stmt = insert(table).values(chunk)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    table.c.deployment,
                    table.c.metric,
                    table.c.status_code,
                    table.c.grain,
                    table.c.ts,
                ],
                set_={
                    "total": stmt.excluded.total,
                    "average": stmt.excluded.average,
                    "maximum": stmt.excluded.maximum,
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )
        )
    return len(rows)


async def prune_points(session: AsyncSession, now: datetime) -> int:
    """Drop PT1M rows older than 7 days and PT1H rows older than 93 days."""
    removed = 0
    for grain, keep in RETENTION.items():
        result = await session.execute(
            delete(AzureMetricPoint).where(
                AzureMetricPoint.grain == grain, AzureMetricPoint.ts < now - keep
            )
        )
        removed += int(getattr(result, "rowcount", 0) or 0)
    return removed


# ---------------------------------------------------------------------------
# Preview cost metric state (kept in the status row's ``detail``)
# ---------------------------------------------------------------------------


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@dataclass(slots=True)
class PreviewState:
    """``FoundryModelEstimatedCost``: on until 3 days without data."""

    empty_since: date | None = None
    last_seen: date | None = None
    disabled: bool = False
    probed: date | None = None

    @classmethod
    def from_detail(cls, detail: dict[str, Any] | None) -> PreviewState:
        raw = (detail or {}).get("preview_cost") or {}
        if not isinstance(raw, dict):
            raw = {}
        return cls(
            empty_since=_date(raw.get("empty_since")),
            last_seen=_date(raw.get("last_seen")),
            disabled=bool(raw.get("disabled")),
            probed=_date(raw.get("probed")),
        )

    def as_detail(self) -> dict[str, Any]:
        return {
            "empty_since": self.empty_since.isoformat() if self.empty_since else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "disabled": self.disabled,
            "probed": self.probed.isoformat() if self.probed else None,
        }

    def should_call(self, today: date, backfill: bool) -> bool:
        """Called while enabled; once disabled, only re-probed weekly on the
        daily backfill."""
        if not self.disabled:
            return True
        if not backfill:
            return False
        return self.probed is None or today - self.probed >= timedelta(
            days=PREVIEW_REPROBE_DAYS
        )

    def observe(self, today: date, has_data: bool) -> None:
        if self.disabled:
            self.probed = today
        if has_data:
            self.empty_since = None
            self.last_seen = today
            self.disabled = False
            return
        if self.empty_since is None:
            self.empty_since = today
        if today - self.empty_since >= timedelta(days=PREVIEW_EMPTY_DAYS):
            if not self.disabled:
                logger.info(
                    "azure_preview_cost_metric_disabled", since=str(self.empty_since)
                )
            self.disabled = True


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------


def _floor(moment: datetime, step: timedelta) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    seconds = int((moment - epoch).total_seconds())
    size = int(step.total_seconds())
    return epoch + timedelta(seconds=seconds - seconds % size)


def poll_windows(
    now: datetime, last_backfill: date | None
) -> list[tuple[str, datetime, datetime]]:
    """(grain, start, end): the PT1M poll window, plus the PT1H backfill once
    a UTC day. Ends are rounded up to the next bucket so the current,
    still-filling bucket is read (and re-read by the next poll)."""
    minute_end = _floor(now, timedelta(minutes=1)) + timedelta(minutes=1)
    windows = [(GRAIN_MINUTE, minute_end - POLL_WINDOW, minute_end)]
    if last_backfill is None or last_backfill < now.date():
        hour_end = _floor(now, timedelta(hours=1)) + timedelta(hours=1)
        windows.append((GRAIN_HOUR, hour_end - BACKFILL_WINDOW, hour_end))
    return windows


async def _previous_detail(ctx: SourceContext) -> dict[str, Any]:
    async with ctx.session_factory() as session:
        row = (await sync_status.read_all(session)).get(SOURCE_NAME)
    detail = row.detail if row is not None else None
    return detail if isinstance(detail, dict) else {}


async def _fetch_and_store(
    ctx: SourceContext,
    api: AzureClient,
    call: MetricCall,
    window: tuple[str, datetime, datetime],
) -> ParsedResponse:
    grain, start, end = window
    account = ctx.settings.azure_account_resource_id
    if not account:
        raise AzureNotConfiguredError()
    body = await api.get_json(
        metrics_path(account), request_params(call, grain, start, end)
    )
    if "cost" in body:
        logger.debug("azure_metrics_query_cost", call=call.key, cost=body.get("cost"))
    parsed = parse_response(body, grain)
    # Persist after every call: a cap or throttle later in the run keeps
    # what was already fetched.
    async with ctx.session_factory() as session:
        await upsert_points(session, parsed.points, ctx.now)
        await session.commit()
    return parsed


async def run_metrics(ctx: SourceContext) -> SourceResult:  # pylint: disable=too-many-locals
    """One poll: calls A-D (PT1M, and PT1H once a day), upsert, prune."""
    previous = await _previous_detail(ctx)
    preview = PreviewState.from_detail(previous)
    last_backfill = _date(previous.get("last_backfill"))
    today = ctx.now.date()
    windows = poll_windows(ctx.now, last_backfill)
    rows = 0
    failed: dict[str, str] = {}
    deployments: set[str] = set()
    calls_made = 0
    async with ctx.arm_client(budgeted=True) as api:
        for window in windows:
            backfill = window[0] == GRAIN_HOUR
            for call in CALLS:
                if call.preview and not preview.should_call(today, backfill):
                    continue
                parsed = await _fetch_and_store(ctx, api, call, window)
                calls_made += 1
                rows += len(parsed.points)
                failed.update(parsed.failed)
                deployments.update(p.deployment for p in parsed.points)
                if call.preview:
                    preview.observe(
                        today,
                        any(
                            (p.total or 0) > 0
                            for p in parsed.points
                            if p.metric == ESTIMATED_COST
                        ),
                    )
            if backfill:
                last_backfill = today
    async with ctx.session_factory() as session:
        pruned = await prune_points(session, ctx.now)
        await session.commit()
    if failed:
        logger.warning("azure_metrics_missing", metrics=sorted(failed))
    return SourceResult(
        rows=rows,
        detail={
            "window": "PT2H",
            "grains": [w[0] for w in windows],
            "calls": calls_made,
            "points": rows,
            "pruned": pruned,
            "deployments": sorted(deployments),
            "missing_metrics": failed,
            "last_backfill": last_backfill.isoformat() if last_backfill else None,
            "preview_cost": preview.as_detail(),
        },
    )


def _interval(settings: Settings) -> int:
    return settings.azure_metrics_interval_seconds


def _call_cap(settings: Settings) -> int:
    return settings.azure_metrics_monthly_call_cap


# The reconciliation module registers these kinds (snapshot builders).
SUMMARY_KIND = "azure.metrics.summary"
SERIES_KIND = "azure.metrics.series"

METRICS_SOURCE = register_source(
    AzureSource(
        name=SOURCE_NAME,
        label="Monitor metrics",
        interval=_interval,
        run=run_metrics,
        snapshot_kinds=(SUMMARY_KIND, SERIES_KIND),
        call_cap=_call_cap,
        needs_credentials=True,
        order=20,
    )
)
