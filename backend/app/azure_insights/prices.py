"""Azure retail meter mapping, unit normalisation and the stored-price reads.

The mapping is explicit (no fuzzy matching): meter names are inconsistent
("Inpt"/"inp", "DZone"/"Dzone", 1K vs 1M units). A model / deployment type /
role without an entry, or without a stored row, is "no official price".
Verified against the live API on 2026-10-20 (australiaeast and eastus2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.db_models import AzureRetailPrice

Role = Literal["prompt", "cached", "output", "embed"]
ROLES: tuple[Role, ...] = ("prompt", "cached", "output", "embed")

# Role -> model_settings price field (USD per 1M tokens).
ROLE_FIELDS: dict[Role, str] = {
    "prompt": "price_prompt_per_million",
    "cached": "price_cached_per_million",
    "output": "price_output_per_million",
    "embed": "price_embed_per_million",
}

# (model_key, deployment_type) -> role -> exact meterName.
RETAIL_METERS: dict[tuple[str, str], dict[Role, str]] = {
    ("gpt-5-mini", "Global"): {
        "prompt": "GPT 5 Mini Inpt Glbl 1M Tokens",
        "cached": "GPT 5 Mini cchd Inpt Glbl 1M Tokens",
        "output": "GPT 5 Mini outpt Glbl 1M Tokens",
    },
    ("gpt-5-mini", "DataZone"): {
        "prompt": "GPT 5 Mini Inpt DZone 1M Tokens",
        "cached": "GPT 5 Mini cchd Inpt DZone 1M Tokens",
        "output": "GPT 5 Mini outpt DZone 1M Tokens",
    },
    ("text-embedding-3-small", "Global"): {
        "embed": "text-embedding-3-small-glbl Tokens",
    },
    ("text-embedding-3-small", "Regional"): {
        "embed": "text-embedding-3-small-regional Tokens",
    },
}

# model_key -> substring for the API's contains(meterName, ...) filter.
METER_SEARCH: dict[str, str] = {
    "gpt-5-mini": "GPT 5 Mini",
    "text-embedding-3-small": "text-embedding-3-small",
}

# meterName -> (model_key, deployment_type, role)
_BY_METER: dict[str, tuple[str, str, Role]] = {
    meter: (model, dtype, role)
    for (model, dtype), roles in RETAIL_METERS.items()
    for role, meter in roles.items()
}

_UNIT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KM]?)\b", re.IGNORECASE)
_UNIT_MULTIPLIER = {"": 1, "K": 1_000, "M": 1_000_000}
_MILLION = Decimal(1_000_000)


def classify_meter(meter_name: str) -> tuple[str, str, Role] | None:
    """(model_key, deployment_type, role) of a mapped meter, else None."""
    return _BY_METER.get(meter_name)


def units_per_price(unit_of_measure: str) -> int | None:
    """Tokens one price covers: '1K' -> 1000, '1M' -> 1e6, '1' -> 1."""
    match = _UNIT_RE.match(unit_of_measure or "")
    if not match:
        return None
    count = Decimal(match.group(1)) * _UNIT_MULTIPLIER[match.group(2).upper()]
    return int(count) if count > 0 else None


def per_million(retail_price: object, unit_of_measure: str) -> Decimal | None:
    units = units_per_price(unit_of_measure)
    if units is None:
        return None
    try:
        price = Decimal(str(retail_price))
    except (InvalidOperation, ValueError):
        return None
    return (price * _MILLION / units).quantize(Decimal("0.000001"))


def model_keys(settings: Settings) -> dict[Role, str]:
    """The model each role is priced for."""
    llm, embed = settings.azure_retail_llm_model, settings.azure_retail_embedding_model
    return {"prompt": llm, "cached": llm, "output": llm, "embed": embed}


def meter_for(settings: Settings, role: Role) -> str | None:
    model = model_keys(settings)[role]
    return RETAIL_METERS.get((model, settings.azure_deployment_type), {}).get(role)


@dataclass(frozen=True, slots=True)
class OfficialPrice:  # pylint: disable=too-many-instance-attributes
    role: Role
    field: str
    model_key: str
    meter_name: str | None
    usd_per_million: float | None
    local_per_million: float | None
    effective_date: date | None
    fetched_at: datetime | None


async def _latest(
    session: AsyncSession, region: str, meter: str, currency: str, today: date
) -> AzureRetailPrice | None:
    result = await session.execute(
        select(AzureRetailPrice)
        .where(
            AzureRetailPrice.arm_region_name == region,
            AzureRetailPrice.meter_name == meter,
            AzureRetailPrice.currency == currency,
            AzureRetailPrice.effective_start_date <= today,
        )
        .order_by(
            AzureRetailPrice.effective_start_date.desc(),
            AzureRetailPrice.fetched_at.desc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def official_prices(
    session: AsyncSession, settings: Settings, today: date
) -> list[OfficialPrice]:
    """The latest stored official price per role (None when not found)."""
    region = settings.azure_retail_region
    local = settings.azure_pricing_currency
    out = []
    for role in ROLES:
        model = model_keys(settings)[role]
        meter = meter_for(settings, role)
        usd = loc = None
        if meter is not None:
            usd = await _latest(session, region, meter, "USD", today)
            loc = (
                usd
                if local == "USD"
                else await _latest(session, region, meter, local, today)
            )
        out.append(
            OfficialPrice(
                role=role,
                field=ROLE_FIELDS[role],
                model_key=model,
                meter_name=meter,
                usd_per_million=_float(usd.price_per_million if usd else None),
                local_per_million=_float(loc.price_per_million if loc else None),
                effective_date=usd.effective_start_date if usd else None,
                fetched_at=usd.fetched_at if usd else None,
            )
        )
    return out


def _float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None
