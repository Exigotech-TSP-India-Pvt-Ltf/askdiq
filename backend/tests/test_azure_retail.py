"""Azure Insights Phase 1: Retail Prices sync, meter mapping, price provenance.

The fixtures are real Retail Prices API responses (australiaeast, USD + AUD,
fetched 2026-10-20), served through pytest-httpx; nothing calls Azure.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.azure_insights import prices, retail
from app.azure_insights.scheduler import run_source
from app.core import rate_limit
from app.core.config import get_settings
from tests.conftest import shared_session_factory, shared_session_runner

FIXTURES = Path(__file__).parent / "fixtures" / "azure"
ONLY_RETAIL = pytest.mark.httpx_mock(
    should_mock=lambda request: request.url.host == "prices.azure.com",
    assert_all_responses_were_requested=False,
)


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def retail_api(request: httpx.Request) -> httpx.Response:
    """Answers like the live API: USD in two pages, AUD in one."""
    currency = request.url.params.get("currencyCode")
    assert request.url.path == "/api/retail/prices"
    if "$skip" not in str(request.url):
        assert "serviceName eq 'Foundry Models'" in request.url.params["$filter"]
    if currency == "'AUD'":
        return httpx.Response(200, json=fixture("retail_aud.json"))
    if "$skip" in str(request.url):
        return httpx.Response(200, json=fixture("retail_usd_page2.json"))
    return httpx.Response(200, json=fixture("retail_usd_page1.json"))


@pytest.fixture
def au_region(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = get_settings()
    monkeypatch.setattr(cfg, "azure_openai_region", None)
    monkeypatch.setattr(cfg, "azure_retail_fallback_region", "australiaeast")
    monkeypatch.setattr(cfg, "azure_deployment_type", "Global")
    monkeypatch.setattr(cfg, "azure_pricing_currency", "AUD")
    monkeypatch.setattr(cfg, "azure_retail_llm_model", "gpt-5-mini")
    monkeypatch.setattr(cfg, "azure_retail_embedding_model", "text-embedding-3-small")


# ---------------------------------------------------------------------------
# Mapping and normalisation (no DB)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("price", "unit", "expected"),
    [
        (0.25, "1M", Decimal("0.25")),
        (2e-05, "1K", Decimal("0.02")),
        (2.2e-05, "1K", Decimal("0.022")),
        (0.5, "1000", Decimal("500")),
        (1, "1 Hour", Decimal("1000000")),
    ],
)
def test_units_are_normalised_to_per_million(price, unit, expected) -> None:
    assert prices.per_million(price, unit) == expected


def test_unparseable_units_give_no_price() -> None:
    assert prices.per_million(1, "per call") is None
    assert prices.per_million("x", "1M") is None


def test_meter_mapping_is_exact() -> None:
    assert prices.classify_meter("GPT 5 Mini cchd Inpt Glbl 1M Tokens") == (
        "gpt-5-mini",
        "Global",
        "cached",
    )
    assert prices.classify_meter("text-embedding-3-small-glbl Tokens") == (
        "text-embedding-3-small",
        "Global",
        "embed",
    )
    # Batch and unknown meters are stored but never used as official prices.
    assert prices.classify_meter("GPT 5 Mini Batch Inpt Glbl 1M Tokens") is None
    assert prices.classify_meter("GPT 5 Mini Inpt Glbl") is None


def test_filter_names_only_mapped_models(au_region, monkeypatch) -> None:
    cfg = get_settings()
    flt = retail.retail_filter(cfg)
    assert flt == (
        "serviceName eq 'Foundry Models' and armRegionName eq 'australiaeast' "
        "and priceType eq 'Consumption' and (contains(meterName,'GPT 5 Mini') "
        "or contains(meterName,'text-embedding-3-small'))"
    )
    monkeypatch.setattr(cfg, "azure_retail_llm_model", "gpt-9-giant")
    monkeypatch.setattr(cfg, "azure_retail_embedding_model", "unknown-embed")
    assert retail.retail_filter(cfg) is None
    assert retail.currencies(cfg) == ["USD", "AUD"]


def test_rows_parse_the_api_fields() -> None:
    item = fixture("retail_usd_page2.json")["Items"][0]
    row = retail.to_row(item, now=retail.utcnow())
    assert row is not None
    assert row["currency"] == "USD"
    assert row["effective_start_date"].isoformat() == item["effectiveStartDate"][:10]
    assert retail.to_row({"meterId": "not-a-uuid"}, now=retail.utcnow()) is None


# ---------------------------------------------------------------------------
# Sync (Postgres, CI)
# ---------------------------------------------------------------------------


async def _sync(db_session) -> Any:
    return await run_source(
        retail.SOURCE,
        session_factory=shared_session_factory(db_session),
        gather=shared_session_runner(db_session).gather,
    )


async def _count(db_session) -> int:
    return (
        await db_session.execute(text("SELECT count(*) FROM azure_retail_prices"))
    ).scalar_one()


@ONLY_RETAIL
async def test_sync_stores_official_prices_idempotently(
    db_session, httpx_mock, au_region
) -> None:
    httpx_mock.add_callback(retail_api, is_reusable=True)
    result = await _sync(db_session)
    assert result is not None and result.rows == 16
    assert result.detail["missing_roles"] == []
    assert await _count(db_session) == 16
    again = await _sync(db_session)
    assert again is not None and again.rows == 16
    assert await _count(db_session) == 16  # same natural keys: no duplicates

    view = await retail.official_prices_view(db_session, get_settings())
    by_role = {p.role: p for p in view.prices}
    assert by_role["prompt"].usd_per_million == pytest.approx(0.25)
    assert by_role["cached"].usd_per_million == pytest.approx(0.025)
    assert by_role["output"].usd_per_million == pytest.approx(2.0)
    assert by_role["embed"].usd_per_million == pytest.approx(0.02)
    assert by_role["prompt"].local_per_million == pytest.approx(0.3562)
    assert by_role["output"].local_per_million == pytest.approx(2.8492)
    assert all(p.found for p in view.prices)
    assert str(by_role["prompt"].effective_date) == "2025-08-01"
    assert str(by_role["embed"].effective_date) == "2024-06-01"
    assert str(view.effective_date) == "2025-08-01"
    assert view.synced_at is not None
    status_row = (
        await db_session.execute(
            text("SELECT state FROM azure_sync_status WHERE source = 'retail'")
        )
    ).scalar_one()
    assert status_row == "ok"


@ONLY_RETAIL
async def test_unmapped_model_has_no_official_price(
    db_session, httpx_mock, au_region, monkeypatch
) -> None:
    httpx_mock.add_callback(retail_api, is_reusable=True)
    await _sync(db_session)
    cfg = get_settings()
    # DataZone meters are not sold in australiaeast; an unknown model has no mapping.
    monkeypatch.setattr(cfg, "azure_deployment_type", "DataZone")
    monkeypatch.setattr(cfg, "azure_retail_embedding_model", "unknown-embed")
    view = await retail.official_prices_view(db_session, cfg)
    by_role = {p.role: p for p in view.prices}
    assert by_role["prompt"].meter_name == "GPT 5 Mini Inpt DZone 1M Tokens"
    assert by_role["prompt"].found is False
    assert by_role["embed"].meter_name is None
    assert by_role["embed"].found is False
    assert by_role["embed"].usd_per_million is None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_rate_limit() -> None:
    rate_limit._retail_sync_log.clear()  # noqa: SLF001


@ONLY_RETAIL
async def test_sync_endpoint_previews_then_confirm_saves_with_provenance(
    client, httpx_mock, au_region, fresh_rate_limit
) -> None:
    httpx_mock.add_callback(retail_api, is_reusable=True)
    preview = await client.post("/admin/models/prices/sync")
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["source"] == "azure_retail"
    official = {p["field"]: p["usd_per_million"] for p in body["prices"]}
    assert official["price_cached_per_million"] == pytest.approx(0.025)

    # Preview changes nothing.
    current = (await client.get("/admin/models")).json()
    assert current["price_source"] is None

    stored = await client.get("/admin/models/prices/official")
    assert stored.status_code == 200
    assert stored.json()["prices"] == body["prices"]

    saved = await client.put(
        "/admin/models",
        json={
            "version": current["version"],
            "price_source": "azure_retail",
            **official,
        },
    )
    assert saved.status_code == 200, saved.text
    after = saved.json()
    assert after["price_source"] == "azure_retail"
    assert after["prices_effective_date"] == "2025-08-01"
    assert after["prices_synced_at"] is not None
    assert after["price_prompt_per_million"] == pytest.approx(0.25)

    # Claiming a non-official value as official is refused.
    bad = await client.put(
        "/admin/models",
        json={
            "version": after["version"],
            "price_source": "azure_retail",
            "price_prompt_per_million": 0.3,
        },
    )
    assert bad.status_code == 422
    # A manual edit is recorded as manual and drops the retail dates.
    manual = await client.put(
        "/admin/models",
        json={"version": after["version"], "price_prompt_per_million": 0.3},
    )
    assert manual.status_code == 200
    assert manual.json()["price_source"] == "manual"
    assert manual.json()["prices_effective_date"] is None


@ONLY_RETAIL
async def test_sync_endpoint_reports_azure_errors(
    client, httpx_mock, au_region, fresh_rate_limit
) -> None:
    httpx_mock.add_response(
        status_code=400,
        json={"Error": {"Code": "InvalidFilter", "Message": "bad filter"}},
        is_reusable=True,
    )
    response = await client.post("/admin/models/prices/sync")
    assert response.status_code == 502
    assert "InvalidFilter" in response.text


@ONLY_RETAIL
async def test_sync_endpoint_is_rate_limited(
    client, httpx_mock, au_region, fresh_rate_limit, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "azure_retail_sync_rate_limit_per_minute", 1)
    httpx_mock.add_callback(retail_api, is_reusable=True)
    assert (await client.post("/admin/models/prices/sync")).status_code == 200
    second = await client.post("/admin/models/prices/sync")
    assert second.status_code == 429
    assert "Retry-After" in second.headers


async def test_official_prices_before_any_sync(client, au_region) -> None:
    response = await client.get("/admin/models/prices/official")
    assert response.status_code == 200
    body = response.json()
    assert body["synced_at"] is None
    assert all(p["found"] is False for p in body["prices"])
