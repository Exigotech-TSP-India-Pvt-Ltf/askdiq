"""Phase 1: Azure Retail Prices sync (public API, no credentials, free).

``azure.retail`` runs weekly (when Azure Insights is enabled) and on demand
from Settings > Models & Pricing ("Sync official prices", always available).
Each run fetches USD and ``AZURE_PRICING_CURRENCY`` for the configured models
in ``azure_retail_region``, upserts every row (history: one row per meter,
currency and effective date), then the scheduler refreshes the
``azure.retail`` snapshot and announces it on the console live stream.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.azure_insights import prices
from app.azure_insights.errors import AzureApiError
from app.azure_insights.scheduler import (
    AzureSource,
    SourceContext,
    SourceResult,
    register_source,
)
from app.azure_insights.schemas import OfficialPriceRead, OfficialPricesRead
from app.core.config import Settings, get_settings
from app.core.logging_config import get_logger
from app.dashboard.snapshots import Gather, register
from app.jobs.queue import utcnow
from app.models.db_models import AzureRetailPrice

logger = get_logger(__name__)

SOURCE = "retail"
RETAIL_KIND = "azure.retail"
RETAIL_BASE_URL = "https://prices.azure.com"
RETAIL_PATH = "/api/retail/prices"
SERVICE_NAME = "Foundry Models"
MAX_PAGES = 10


def retail_filter(settings: Settings) -> str | None:
    """OData filter for the configured models; None when none is mapped.

    Values are case-sensitive. The region is validated by the settings and
    the meter substrings are constants (nothing user-supplied)."""
    searches = sorted(
        {
            prices.METER_SEARCH[model]
            for model in prices.model_keys(settings).values()
            if model in prices.METER_SEARCH
        }
    )
    if not searches:
        return None
    contains = " or ".join(f"contains(meterName,'{s}')" for s in searches)
    return (
        f"serviceName eq '{SERVICE_NAME}' "
        f"and armRegionName eq '{settings.azure_retail_region}' "
        f"and priceType eq 'Consumption' and ({contains})"
    )


def currencies(settings: Settings) -> list[str]:
    return list(dict.fromkeys(("USD", settings.azure_pricing_currency)))


def _parse_date(value: Any) -> date | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def to_row(item: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """A table row from one API item; None when it lacks the essentials."""
    try:
        meter_id = uuid.UUID(str(item.get("meterId")))
    except ValueError:
        return None
    effective = _parse_date(item.get("effectiveStartDate"))
    currency = str(item.get("currencyCode") or "").upper()[:3]
    meter_name = str(item.get("meterName") or "")
    if effective is None or len(currency) != 3 or not meter_name:  # noqa: PLR2004
        return None
    unit = str(item.get("unitOfMeasure") or "")
    mapped = prices.classify_meter(meter_name)
    return {
        "meter_id": meter_id,
        "currency": currency,
        "effective_start_date": effective,
        "meter_name": meter_name[:200],
        "product_name": (item.get("productName") or None),
        "sku_name": (item.get("skuName") or None),
        "service_name": (item.get("serviceName") or None),
        "arm_region_name": str(item.get("armRegionName") or "")[:40],
        "unit_of_measure": unit[:20],
        "retail_price": item.get("retailPrice") or 0,
        "price_per_million": prices.per_million(item.get("retailPrice") or 0, unit),
        "model_key": mapped[0] if mapped else None,
        "deployment_type": mapped[1] if mapped else None,
        "role": mapped[2] if mapped else None,
        "fetched_at": now,
    }


async def upsert_rows(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Idempotent: the natural key is (meter, currency, effective date)."""
    if not rows:
        return 0
    stmt = insert(AzureRetailPrice).values(rows)
    keep = {"meter_id", "currency", "effective_start_date"}
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=list(keep),
            set_={
                col: stmt.excluded[col]
                for col in rows[0]
                if col not in keep and col != "first_seen_at"
            },
        )
    )
    return len(rows)


async def fetch_items(ctx: SourceContext, flt: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    async with ctx.public_client(RETAIL_BASE_URL) as api:
        for currency in currencies(ctx.settings):
            params = {"currencyCode": f"'{currency}'", "$filter": flt}
            async for item in api.paged(
                RETAIL_PATH, params, items_key="Items", max_pages=MAX_PAGES
            ):
                items.append(item)
    return items


async def run_retail(ctx: SourceContext) -> SourceResult:
    flt = retail_filter(ctx.settings)
    if flt is None:
        raise AzureApiError(
            "no_mapping", "No retail meter mapping for the configured models"
        )
    items = await fetch_items(ctx, flt)
    rows = [row for item in items if (row := to_row(item, ctx.now)) is not None]
    # One row per key (the API may repeat a meter across pages).
    unique = {
        (r["meter_id"], r["currency"], r["effective_start_date"]): r for r in rows
    }
    async with ctx.session_factory() as session:
        written = await upsert_rows(session, list(unique.values()))
        await session.commit()
    mapped = sorted({r["meter_name"] for r in unique.values() if r["role"]})
    missing = [
        role
        for role in prices.ROLES
        if (meter := prices.meter_for(ctx.settings, role)) is None
        or meter not in mapped
    ]
    if missing:
        logger.warning("azure_retail_meters_not_found", roles=missing)
    return SourceResult(
        rows=written,
        detail={
            "region": ctx.settings.azure_retail_region,
            "items": len(items),
            "missing_roles": missing,
        },
    )


async def official_prices_view(
    session: AsyncSession, settings: Settings, today: date | None = None
) -> OfficialPricesRead:
    today = today or utcnow().date()
    found = await prices.official_prices(session, settings, today)
    fetched = [p.fetched_at for p in found if p.fetched_at]
    effective = [p.effective_date for p in found if p.effective_date]
    return OfficialPricesRead(
        region=settings.azure_retail_region,
        deployment_type=settings.azure_deployment_type,
        local_currency=settings.azure_pricing_currency,
        synced_at=max(fetched) if fetched else None,
        effective_date=max(effective) if effective else None,
        prices=[
            OfficialPriceRead(
                role=p.role,
                field=p.field,
                model_key=p.model_key,
                meter_name=p.meter_name,
                found=p.usd_per_million is not None,
                usd_per_million=p.usd_per_million,
                local_per_million=p.local_per_million,
                effective_date=p.effective_date,
            )
            for p in found
        ],
    )


async def _retail_snapshot(gather: Gather, _params: dict[str, Any]) -> dict:
    settings = get_settings()
    (view,) = await gather(lambda db: official_prices_view(db, settings))
    return view.model_dump(mode="json")


register(RETAIL_KIND, _retail_snapshot, [{}])

register_source(
    AzureSource(
        name=SOURCE,
        label="Retail prices",
        interval=lambda s: s.azure_retail_interval_seconds,
        run=run_retail,
        needs_credentials=False,
        snapshot_kinds=(RETAIL_KIND,),
        order=10,
    )
)
