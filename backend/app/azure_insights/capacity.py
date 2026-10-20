"""Capacity and quota (Phase 4): deployments and regional quota usages.

Polled every ``AZURE_CAPACITY_INTERVAL_SECONDS`` (6 h) with free ARM reads:

- ``{account}/deployments`` (api 2024-10-01): SKU, capacity, model and the
  real TPM / RPM from ``properties.rateLimits`` (``count`` per
  ``renewalPeriod`` seconds, normalised to a minute);
- ``/subscriptions/{sub}/providers/Microsoft.CognitiveServices/locations/
  {region}/usages`` (api 2024-10-01): regional quota ``currentValue`` /
  ``limit``. Needs the optional Cognitive Services Usages Reader role at
  subscription scope: a 403 is recorded as ``forbidden`` on the status row
  (the regional card says "not permitted"); deployment limits keep working.

Each poll stores one row per deployment / quota (history by ``fetched_at``),
refreshes ``azure.capacity`` and the quota gauges of every process (real
limits instead of AZURE_OPENAI_*_LIMIT) and announces both on the console
live stream.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.azure_insights import status as sync_status
from app.azure_insights.capacity_schemas import (
    AzureCapacityRead,
    DeploymentCapacity,
    RegionalQuota,
    RegionalQuotaItem,
)
from app.azure_insights.client import AzureClient
from app.azure_insights.errors import AzureApiError
from app.azure_insights.scheduler import (
    AzureSource,
    SourceContext,
    SourceResult,
    register_source,
)
from app.core import database
from app.core.concurrency import SessionFactory
from app.core.config import Settings, get_settings
from app.core.events import Event
from app.core.logging_config import get_logger
from app.core.quota import ExternalLimit, quota_monitor
from app.core.tasks import spawn
from app.dashboard.snapshots import Gather, register
from app.jobs.queue import utcnow
from app.model_settings.service import current_model_settings
from app.models.db_models import AzureDeploymentSnapshot, AzureQuotaUsage

logger = get_logger(__name__)

CAPACITY_SOURCE = "capacity"
CAPACITY_KIND = "azure.capacity"
# Open Monitoring views refetch (their quota gauges read the new limits).
MONITORING_SCOPE = "governance.monitoring"
COGNITIVE_API_VERSION = "2024-10-01"
# History kept per poll (4 rows a day per deployment): about 13 months.
HISTORY_RETENTION_DAYS = 400
CHANGES_LIMIT = 20
SECONDS_PER_MINUTE = 60
HTTP_FORBIDDEN = 403

USAGES_OK = "ok"
USAGES_FORBIDDEN = "forbidden"
USAGES_ERROR = "error"


# ---------------------------------------------------------------------------
# Parsing (pure)
# ---------------------------------------------------------------------------


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def per_minute(count: Any, renewal_period: Any) -> int | None:
    """``count`` per ``renewal_period`` seconds as a per-minute limit."""
    n, period = _float(count), _float(renewal_period)
    if n is None or n <= 0 or not period or period <= 0:
        return None
    return round(n * SECONDS_PER_MINUTE / period)


def rate_limits_per_minute(rate_limits: Any) -> tuple[int | None, int | None]:
    """(TPM, RPM) from ``properties.rateLimits``: keys ``token`` and
    ``request``; with several entries per key the most restrictive wins."""
    found: dict[str, int] = {}
    for entry in rate_limits if isinstance(rate_limits, list) else ():
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").lower()
        value = per_minute(entry.get("count"), entry.get("renewalPeriod"))
        if key in {"token", "request"} and value is not None:
            found[key] = min(value, found.get(key, value))
    return found.get("token"), found.get("request")


def _clip(value: Any, size: int) -> str | None:
    if value is None or value == "":
        return None
    return str(value)[:size]


def parse_deployment(item: dict[str, Any], fetched_at: datetime) -> dict | None:
    name = item.get("name")
    if not name:
        return None
    props = item.get("properties") or {}
    sku = item.get("sku") or {}
    model = props.get("model") or {}
    raw_limits = props.get("rateLimits")
    tpm, rpm = rate_limits_per_minute(raw_limits)
    return {
        "name": str(name)[:128],
        "fetched_at": fetched_at,
        "model_name": _clip(model.get("name"), 128),
        "model_version": _clip(model.get("version"), 64),
        "sku_name": _clip(sku.get("name"), 64),
        "sku_capacity": _int(sku.get("capacity")),
        "current_capacity": _int(props.get("currentCapacity")),
        "tpm_limit": tpm,
        "rpm_limit": rpm,
        "rate_limits": [
            {k: e.get(k) for k in ("key", "count", "renewalPeriod")}
            for e in raw_limits or []
            if isinstance(e, dict)
        ]
        or None,
        "provisioning_state": _clip(props.get("provisioningState"), 32),
    }


def parse_usage(
    item: dict[str, Any], location: str, fetched_at: datetime
) -> dict | None:
    name = item.get("name") or {}
    value = name.get("value") if isinstance(name, dict) else name
    if not value:
        return None
    localized = name.get("localizedValue") if isinstance(name, dict) else None
    return {
        "location": location[:64],
        "name": str(value)[:160],
        "fetched_at": fetched_at,
        "localized_name": _clip(localized, 200),
        "current_value": _float(item.get("currentValue")),
        "limit_value": _float(item.get("limit")),
        "unit": _clip(item.get("unit"), 32),
        "status": _clip(item.get("status"), 32),
    }


def keep_usage(row: dict[str, Any], models: set[str]) -> bool:
    """Keep quotas in use, plus every quota of a model we have deployed (the
    list has hundreds of zero rows for models we never use)."""
    if (row.get("current_value") or 0) > 0:
        return True
    name = str(row.get("name") or "").lower()
    return any(model and model in name for model in models)


def usage_pct(current: float | None, limit: float | None) -> float | None:
    if current is None or not limit or limit <= 0:
        return None
    return round(current * 100.0 / limit, 1)


# ---------------------------------------------------------------------------
# Poll
# ---------------------------------------------------------------------------


def deployments_path(settings: Settings) -> str:
    return f"{settings.azure_account_resource_id}/deployments"


def usages_path(settings: Settings) -> str:
    return (
        f"/subscriptions/{settings.azure_subscription_id}/providers/"
        f"Microsoft.CognitiveServices/locations/{settings.azure_openai_region}/usages"
    )


async def _upsert(
    session: AsyncSession, model: Any, rows: list[dict], keys: list[str]
) -> None:
    if not rows:
        return
    stmt = insert(model).values(rows)
    update = {c: stmt.excluded[c] for c in rows[0] if c not in keys}
    await session.execute(stmt.on_conflict_do_update(index_elements=keys, set_=update))


async def fetch_usages(
    api: AzureClient, settings: Settings, models: set[str], now: datetime
) -> tuple[list[dict], str, str | None]:
    """(rows, state, error code): a missing optional role is ``forbidden``,
    not a failed run (the deployment limits are still stored)."""
    region = str(settings.azure_openai_region or "")
    rows: list[dict] = []
    params = {"api-version": COGNITIVE_API_VERSION}
    try:
        async for item in api.paged(usages_path(settings), params):
            row = parse_usage(item, region, now)
            if row is not None and keep_usage(row, models):
                rows.append(row)
    except AzureApiError as exc:
        forbidden = exc.status_code == HTTP_FORBIDDEN or exc.code in {
            "AuthorizationFailed",
            "Forbidden",
        }
        return [], USAGES_FORBIDDEN if forbidden else USAGES_ERROR, exc.code
    return rows, USAGES_OK, None


async def run_capacity(ctx: SourceContext) -> SourceResult:
    settings, now = ctx.settings, ctx.now
    async with ctx.arm_client() as api:
        deployments = [
            row
            async for item in api.paged(
                deployments_path(settings), {"api-version": COGNITIVE_API_VERSION}
            )
            if (row := parse_deployment(item, now)) is not None
        ]
        models = {
            (d["model_name"] or "").lower() for d in deployments if d["model_name"]
        }
        usage_rows, usages_state, usages_code = await fetch_usages(
            api, settings, models, now
        )
    cutoff = now - timedelta(days=HISTORY_RETENTION_DAYS)
    async with ctx.session_factory() as session:
        await _upsert(
            session, AzureDeploymentSnapshot, deployments, ["name", "fetched_at"]
        )
        await _upsert(
            session, AzureQuotaUsage, usage_rows, ["location", "name", "fetched_at"]
        )
        await session.execute(
            delete(AzureDeploymentSnapshot).where(
                AzureDeploymentSnapshot.fetched_at < cutoff
            )
        )
        await session.execute(
            delete(AzureQuotaUsage).where(AzureQuotaUsage.fetched_at < cutoff)
        )
        await session.commit()
    # This process's gauges at once; the others on the live announcement.
    await refresh_quota_limits(ctx.session_factory)
    return SourceResult(
        rows=len(deployments) + len(usage_rows),
        detail={
            "deployments": len(deployments),
            "usages": usages_state,
            "usages_count": len(usage_rows),
            "usages_error": usages_code,
        },
    )


# ---------------------------------------------------------------------------
# Real limits for the quota gauges (every process)
# ---------------------------------------------------------------------------


async def latest_deployments(session: AsyncSession) -> list[AzureDeploymentSnapshot]:
    """The latest stored row of every deployment seen in the last poll."""
    last = select(func.max(AzureDeploymentSnapshot.fetched_at)).scalar_subquery()
    rows = await session.execute(
        select(AzureDeploymentSnapshot)
        .where(AzureDeploymentSnapshot.fetched_at == last)
        .order_by(AzureDeploymentSnapshot.name)
    )
    return list(rows.scalars().all())


def limits_by_name(rows: list[AzureDeploymentSnapshot]) -> dict[str, ExternalLimit]:
    """Deployment name -> limits; the model name maps too (the embedding
    tracker is keyed by EMBEDDING_MODEL) unless a deployment owns that name."""
    out: dict[str, ExternalLimit] = {}
    for row in rows:
        if row.tpm_limit or row.rpm_limit:
            out[row.name.lower()] = ExternalLimit(row.tpm_limit, row.rpm_limit)
    for row in rows:
        if (row.tpm_limit or row.rpm_limit) and row.model_name:
            out.setdefault(
                row.model_name.lower(), ExternalLimit(row.tpm_limit, row.rpm_limit)
            )
    return out


async def refresh_quota_limits(session_factory: SessionFactory | None = None) -> int:
    """Load the Azure-reported limits into this process's quota monitor
    (configured defaults while Azure Insights is off). Never raises."""
    if not get_settings().azure_insights_configured:
        quota_monitor.set_external_limits({})
        return 0
    factory = session_factory or database.AsyncSessionLocal
    try:
        async with factory() as session:
            limits = limits_by_name(await latest_deployments(session))
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("azure_quota_limits_load_failed", exc_info=True)
        return 0
    quota_monitor.set_external_limits(limits)
    return len(limits)


def on_console_changed(event: Event) -> None:
    """Event-bus callback: a capacity poll (any replica) stored new limits."""
    if event.id == CAPACITY_KIND:
        spawn(refresh_quota_limits(), name="azure-quota-limits")


# ---------------------------------------------------------------------------
# Read model (snapshot ``azure.capacity``)
# ---------------------------------------------------------------------------


def _role(row: AzureDeploymentSnapshot) -> str | None:
    models = current_model_settings()
    names = {row.name.lower(), (row.model_name or "").lower()}
    if (models.llm_deployment or "").lower() == row.name.lower():
        return "chat"
    if (models.embedding_model or "").lower() in names:
        return "embedding"
    return None


async def _regional(
    session: AsyncSession, settings: Settings, detail: dict[str, Any] | None
) -> RegionalQuota:
    region = settings.azure_openai_region
    last = (
        select(func.max(AzureQuotaUsage.fetched_at))
        .where(AzureQuotaUsage.location == (region or ""))
        .scalar_subquery()
    )
    rows = (
        (
            await session.execute(
                select(AzureQuotaUsage)
                .where(
                    AzureQuotaUsage.location == (region or ""),
                    AzureQuotaUsage.fetched_at == last,
                )
                .order_by(AzureQuotaUsage.current_value.desc().nulls_last())
            )
        )
        .scalars()
        .all()
    )
    state = (detail or {}).get("usages") or ("ok" if rows else "never_run")
    return RegionalQuota(
        state=state,
        location=region,
        error_code=(detail or {}).get("usages_error"),
        fetched_at=rows[0].fetched_at if rows else None,
        items=[
            RegionalQuotaItem(
                name=r.name,
                label=r.localized_name or r.name,
                current_value=r.current_value,
                limit_value=r.limit_value,
                unit=r.unit,
                pct=usage_pct(r.current_value, r.limit_value),
            )
            for r in rows
        ],
    )


async def _changes(session: AsyncSession) -> list[dict[str, Any]]:
    """Recent polls where a deployment's SKU, capacity or limits changed
    (the first poll of a deployment is not a change)."""
    t = AzureDeploymentSnapshot
    cols = ("sku_name", "sku_capacity", "tpm_limit", "rpm_limit")

    def lag(column: Any) -> Any:
        return func.lag(column).over(partition_by=t.name, order_by=t.fetched_at)

    inner = select(
        t.name,
        t.fetched_at,
        *(getattr(t, c) for c in cols),
        lag(t.fetched_at).label("prev_fetched_at"),
        *(lag(getattr(t, c)).label(f"prev_{c}") for c in cols),
    ).subquery()
    rows = await session.execute(
        select(inner)
        .where(
            inner.c.prev_fetched_at.isnot(None),
            or_(*(inner.c[c].is_distinct_from(inner.c[f"prev_{c}"]) for c in cols)),
        )
        .order_by(inner.c.fetched_at.desc())
        .limit(CHANGES_LIMIT)
    )
    return [dict(m) for m in rows.mappings()]


async def build_capacity(
    session: AsyncSession, settings: Settings, now: datetime | None = None
) -> AzureCapacityRead:
    now = now or utcnow()
    rows = await latest_deployments(session)
    status_row = (await sync_status.read_all(session)).get(CAPACITY_SOURCE)
    return AzureCapacityRead(
        connected=settings.azure_insights_configured,
        fetched_at=rows[0].fetched_at if rows else None,
        deployments=[
            DeploymentCapacity(
                name=r.name,
                model_name=r.model_name,
                model_version=r.model_version,
                sku_name=r.sku_name,
                sku_capacity=r.sku_capacity,
                current_capacity=r.current_capacity,
                tpm_limit=r.tpm_limit,
                rpm_limit=r.rpm_limit,
                provisioning_state=r.provisioning_state,
                role=_role(r),
            )
            for r in rows
        ],
        regional=await _regional(
            session, settings, status_row.detail if status_row else None
        ),
        changes=await _changes(session),
        generated_at=now,
    )


async def _capacity_snapshot(gather: Gather, _params: dict[str, Any]) -> dict:
    settings = get_settings()
    (payload,) = await gather(lambda db: build_capacity(db, settings))
    return payload.model_dump(mode="json")


register(CAPACITY_KIND, _capacity_snapshot, [{}])

register_source(
    AzureSource(
        name=CAPACITY_SOURCE,
        label="Capacity and quota",
        interval=lambda s: s.azure_capacity_interval_seconds,
        run=run_capacity,
        snapshot_kinds=(CAPACITY_KIND,),
        live_scopes=(MONITORING_SCOPE,),
        order=40,
    )
)
