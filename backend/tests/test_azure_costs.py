"""Azure Insights Phase 3: Cost Management billed cost, billed vs estimated.

Recorded-shape responses (tests/fixtures/azure/cost_*.json, per the Cost
Management Query docs: ``Cost`` and ``PreTaxCost`` variants, ``nextLink``,
429 with the QPU retry-after header, unsupported-subscription errors) go
through ``httpx.MockTransport``. No real Azure call is made.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.alerts import catalogue as c
from app.alerts.azure_cost_rules import (
    azure_cost_rule,
    billed_budget,
)
from app.alerts.rules import DATABASE_RULE_FUNCS, rules_covered
from app.azure_insights import billed, costs, scheduler, status
from app.azure_insights.client import QPU_RETRY_AFTER, AzureClient
from app.azure_insights.costs import (
    COSTS_KIND,
    CostRow,
    classify_unsupported,
    fetch_costs,
    final_before,
    is_final_day,
    parse_page,
    parse_usage_date,
    query_body,
    query_window,
)
from app.azure_insights.errors import (
    AzureApiError,
    AzureThrottledError,
    AzureUnsupportedError,
)
from app.azure_insights.scheduler import anchored_slot
from app.core.config import get_settings
from app.core.events import TOPIC_CONSOLE_CHANGED
from app.dashboard import snapshots
from app.jobs.queue import JobDeferredError
from tests.conftest import shared_session_factory, shared_session_runner

FIXTURES = Path(__file__).parent / "fixtures" / "azure"
SUB = "33333333-3333-4333-8333-333333333333"
ACCOUNT = (
    f"/subscriptions/{SUB}/resourceGroups/rg-askdiq/providers/"
    "Microsoft.CognitiveServices/accounts/askdiq-foundry"
)
QUERY_PATH = (
    f"/subscriptions/{SUB}/resourceGroups/rg-askdiq/providers/"
    "Microsoft.CostManagement/query"
)
SYDNEY = "Australia/Sydney"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Window, finality, request body
# ---------------------------------------------------------------------------


def test_window_rereads_last_7_days_and_month_to_date() -> None:
    assert query_window(date(2026, 10, 20)) == (date(2026, 10, 1), date(2026, 10, 20))
    # Early in a month: the last 7 days reach back into the previous month.
    assert query_window(date(2026, 10, 8)) == (date(2026, 10, 1), date(2026, 10, 8))


def test_window_rereads_previous_month_until_day_5() -> None:
    assert query_window(date(2026, 11, 3)) == (date(2026, 10, 1), date(2026, 11, 3))
    assert query_window(date(2026, 11, 5)) == (date(2026, 10, 1), date(2026, 11, 5))
    assert query_window(date(2026, 11, 6)) == (date(2026, 10, 30), date(2026, 11, 6))
    assert query_window(date(2027, 1, 2)) == (date(2026, 12, 1), date(2027, 1, 2))


def test_month_is_final_three_days_after_close() -> None:
    assert not is_final_day(date(2026, 10, 31), date(2026, 11, 3))
    assert is_final_day(date(2026, 10, 31), date(2026, 11, 4))
    assert is_final_day(date(2026, 10, 1), date(2026, 11, 4))
    assert not is_final_day(date(2026, 11, 1), date(2026, 11, 30))
    assert final_before(date(2027, 1, 3)) == date(2026, 12, 1)
    assert final_before(date(2027, 1, 4)) == date(2027, 1, 1)


def test_query_body_matches_the_plan() -> None:
    body = query_body(ACCOUNT, date(2026, 10, 1), date(2026, 10, 20))
    assert body["type"] == "ActualCost"
    assert body["timeframe"] == "Custom"
    assert body["timePeriod"] == {
        "from": "2026-10-01T00:00:00Z",
        "to": "2026-10-20T23:59:59Z",
    }
    dataset = body["dataset"]
    assert dataset["granularity"] == "Daily"
    assert [g["name"] for g in dataset["grouping"]] == ["ResourceId", "Meter"]
    values = dataset["filter"]["dimensions"]["values"]
    assert values == [ACCOUNT.lower()]
    assert dataset["filter"]["dimensions"]["operator"] == "In"


# ---------------------------------------------------------------------------
# Parsing by column name
# ---------------------------------------------------------------------------


def test_parse_cost_columns_by_name() -> None:
    rows, column = parse_page(fixture("cost_query_page1.json")["properties"], "x")
    assert column == "Cost"
    assert len(rows) == 4
    first = rows[0]
    assert first.usage_date == date(2026, 10, 18)
    assert first.meter == "gpt 5 mini outpt glbl"
    assert first.resource_id == ACCOUNT.lower()
    assert str(first.cost) == "0.41213578"
    assert first.currency == "AUD"


def test_parse_pretaxcost_variant_with_reordered_columns() -> None:
    rows, column = parse_page(fixture("cost_query_pretax.json")["properties"], "x")
    assert column == "PreTaxCost"
    assert [(r.usage_date, float(r.cost), r.currency) for r in rows] == [
        (date(2026, 10, 15), 1.25, "USD"),
        (date(2026, 10, 15), 0.25, "USD"),
        (date(2026, 10, 16), 2.0, "USD"),
    ]


def test_parse_empty_result_and_unknown_columns() -> None:
    assert parse_page(fixture("cost_query_empty.json")["properties"], "x") == ([], None)
    bad = {"columns": [{"name": "Amount"}], "rows": [[1.0]]}
    with pytest.raises(AzureApiError) as exc:
        parse_page(bad, "x")
    assert exc.value.code == "unexpected_columns"


def test_usage_date_formats() -> None:
    assert parse_usage_date(20261018) == date(2026, 10, 18)
    assert parse_usage_date("20261018") == date(2026, 10, 18)
    assert parse_usage_date("2026-10-18T00:00:00") == date(2026, 10, 18)


def test_missing_resource_and_currency_columns_fall_back() -> None:
    props = {
        "columns": [{"name": "Cost"}, {"name": "UsageDate"}, {"name": "Meter"}],
        "rows": [[0.5, 20261018, "m"]],
    }
    (row,), _ = parse_page(props, "/subscriptions/x/acct")
    assert (row.resource_id, row.currency) == ("/subscriptions/x/acct", "XXX")


# ---------------------------------------------------------------------------
# HTTP: nextLink, throttling, unsupported subscriptions
# ---------------------------------------------------------------------------


class CostTransport:
    """Scripted Cost Management responses; records every request."""

    def __init__(self, *responses: tuple[int, Any, dict[str, str] | None]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            code, body, headers = self.responses.pop(0)
            return httpx.Response(code, json=body, headers=headers or {})

        return httpx.MockTransport(handler)


class FakeTokens:
    async def token(self) -> str:
        return "tok"

    def invalidate(self) -> None:
        return None


def api_client(script: CostTransport, **kwargs: Any) -> AzureClient:
    async def no_sleep(_seconds: float) -> None:
        return None

    return AzureClient(
        transport=script(), token_source=FakeTokens(), sleep=no_sleep, **kwargs
    )


async def test_next_link_is_posted_with_the_same_body() -> None:
    script = CostTransport(
        (200, fixture("cost_query_page1.json"), None),
        (200, fixture("cost_query_page2.json"), None),
    )
    body = query_body(ACCOUNT, date(2026, 10, 1), date(2026, 10, 20))
    async with api_client(script) as api:
        result = await fetch_costs(api, QUERY_PATH, body, "2026-06-01", "x")
    assert result.pages == 2
    assert len(result.rows) == 6
    first, second = script.requests
    assert first.method == second.method == "POST"
    assert first.url.params["api-version"] == "2026-06-01"
    assert second.url.params["$skiptoken"] == "AQAAAA=="
    assert json.loads(first.content) == json.loads(second.content) == body


async def test_pagination_is_capped() -> None:
    page = fixture("cost_query_page1.json")
    script = CostTransport(*[(200, page, None)] * 3)
    async with api_client(script) as api:
        result = await fetch_costs(api, QUERY_PATH, {}, "v", "x", max_pages=2)
    assert result.pages == 2
    # The same keys on both pages are summed into one row each.
    assert len(result.rows) == 4


async def test_qpu_retry_after_defers_the_run() -> None:
    script = CostTransport(
        (
            429,
            fixture("cost_query_throttled.json"),
            {
                QPU_RETRY_AFTER: "120",
                "x-ms-ratelimit-microsoft.costmanagement-qpu-consumed": "1",
                "x-ms-ratelimit-microsoft.costmanagement-qpu-remaining": "0",
            },
        )
    )
    async with api_client(script) as api:
        with pytest.raises(AzureThrottledError) as exc:
            await fetch_costs(api, QUERY_PATH, {}, "v", "x")
    assert exc.value.retry_after == 120


@pytest.mark.parametrize(
    ("name", "status_code", "expected"),
    [
        ("cost_unsupported_offer.json", 422, costs.UNSUPPORTED_OFFER_MESSAGE),
        ("cost_policy_disabled.json", 403, costs.UNSUPPORTED_POLICY_MESSAGE),
    ],
)
def test_unsupported_subscription_errors_are_recognised(
    name: str, status_code: int, expected: str
) -> None:
    err = fixture(name)["error"]
    found = classify_unsupported(
        AzureApiError(err["code"], err["message"], status_code=status_code)
    )
    assert isinstance(found, AzureUnsupportedError)
    assert found.message == expected
    assert found.code == err["code"]


def test_other_errors_are_not_unsupported() -> None:
    forbidden = fixture("arm_forbidden.json")["error"]
    assert (
        classify_unsupported(
            AzureApiError(forbidden["code"], forbidden["message"], status_code=403)
        )
        is None
    )
    assert (
        classify_unsupported(AzureApiError("x", "offer type", status_code=500)) is None
    )
    assert (
        classify_unsupported(AzureApiError("x", "offer type", status_code=429)) is None
    )


# ---------------------------------------------------------------------------
# Schedule: daily at 03:00 Australia/Sydney
# ---------------------------------------------------------------------------


def _has_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return False
    return True


AEDT = timezone(timedelta(hours=11))
AEST = timezone(timedelta(hours=10))


@pytest.mark.parametrize(
    ("zone", "now", "start", "nxt"),
    [
        # 03:00 AEDT (UTC+11) is 16:00 UTC the day before.
        (
            AEDT,
            datetime(2026, 10, 20, 10, tzinfo=UTC),
            datetime(2026, 10, 19, 16, tzinfo=UTC),
            datetime(2026, 10, 20, 16, tzinfo=UTC),
        ),
        (
            AEST,
            datetime(2026, 6, 10, 17, 30, tzinfo=UTC),
            datetime(2026, 6, 10, 17, tzinfo=UTC),
            datetime(2026, 6, 11, 17, tzinfo=UTC),
        ),
        # Just before 03:00 local: still yesterday's slot.
        (
            AEST,
            datetime(2026, 6, 10, 16, 59, tzinfo=UTC),
            datetime(2026, 6, 9, 17, tzinfo=UTC),
            datetime(2026, 6, 10, 17, tzinfo=UTC),
        ),
    ],
)
def test_daily_slot_is_anchored_to_local_time(zone, now, start, nxt) -> None:
    assert anchored_slot(now, 86_400, zone, time(3, 0)) == (start, nxt)


@pytest.mark.skipif(not _has_zone(SYDNEY), reason="no IANA tz database on this host")
def test_daily_slot_follows_sydney_dst() -> None:
    at = time(3, 0)
    october = anchored_slot(datetime(2026, 10, 20, 10, tzinfo=UTC), 86_400, SYDNEY, at)
    assert october[0] == datetime(2026, 10, 19, 16, tzinfo=UTC)  # AEDT
    june = anchored_slot(datetime(2026, 6, 10, 17, 30, tzinfo=UTC), 86_400, SYDNEY, at)
    assert june[0] == datetime(2026, 6, 10, 17, tzinfo=UTC)  # AEST
    # DST starts 2026-10-04: the slot after 03:00 AEST is 03:00 AEDT (23 h).
    start, nxt = anchored_slot(
        datetime(2026, 10, 3, 18, tzinfo=UTC), 86_400, SYDNEY, at
    )
    assert start == datetime(2026, 10, 3, 17, tzinfo=UTC)
    assert nxt == datetime(2026, 10, 4, 16, tzinfo=UTC)


def test_unknown_zone_falls_back_to_utc() -> None:
    start, nxt = anchored_slot(
        datetime(2026, 10, 20, 10, tzinfo=UTC), 86_400, "Nowhere/Atlantis", time(3, 0)
    )
    assert (start, nxt) == (
        datetime(2026, 10, 20, 3, tzinfo=UTC),
        datetime(2026, 10, 21, 3, tzinfo=UTC),
    )


def test_sub_daily_anchored_slots_restart_each_day() -> None:
    start, nxt = anchored_slot(
        datetime(2026, 6, 11, 2, tzinfo=UTC), 21_600, AEST, time(3, 0)
    )
    # 12:00 AEST = 03:00 + 9 h: slot 09:00-15:00 local.
    assert start == datetime(2026, 6, 10, 23, tzinfo=UTC)
    assert nxt == datetime(2026, 6, 11, 5, tzinfo=UTC)


def test_costs_source_is_registered_daily_at_3am() -> None:
    source = scheduler.get_source("costs")
    cfg = get_settings()
    assert source.job_type == "azure.costs"
    assert source.interval(cfg) == cfg.azure_costs_interval_seconds
    assert source.anchor is not None
    assert source.anchor(cfg) == (cfg.insights_timezone, time(3, 0))
    assert COSTS_KIND in source.snapshot_kinds


# ---------------------------------------------------------------------------
# Variance math and day status (no DB)
# ---------------------------------------------------------------------------


def test_variance_is_estimate_minus_billed_over_billed() -> None:
    assert billed.variance_pct(39.85, 41.20) == -3.28
    assert billed.variance_pct(11.5, 10.0) == 15.0
    assert billed.variance_pct(1.0, 0.0) is None
    assert billed.variance_pct(1.0, None) is None


def _estimate(by_day: dict[date, float], **kw: Any) -> billed.Estimate:
    return billed.Estimate(
        by_day,
        by_day,
        kw.get("currency", "USD"),
        kw.get("repriced", False),
        kw.get("mismatch", False),
    )


def test_days_status_totals_and_pending() -> None:
    today = date(2026, 11, 10)
    info = billed.BilledInfo(
        {date(2026, 10, 31): 10.0, date(2026, 11, 7): 4.0, date(2026, 11, 9): 1.0},
        "USD",
        date(2026, 11, 9),
    )
    est = _estimate(
        {date(2026, 10, 31): 11.0, date(2026, 11, 7): 4.0, date(2026, 11, 10): 2.0}
    )
    days = billed.build_days(
        date(2026, 10, 31), today, today, info, est, date(2026, 10, 1)
    )
    by = {d.date: d for d in days}
    assert by[date(2026, 10, 31)].status == "final"
    assert by[date(2026, 10, 31)].variance_pct == 10.0
    assert by[date(2026, 10, 31)].closed
    assert by[date(2026, 11, 7)].status == "estimated"
    assert by[date(2026, 11, 7)].closed
    # A billed period without a charge is 0, not unknown.
    assert by[date(2026, 11, 1)].billed == 0.0
    # Yesterday and today are pending, even when partly billed.
    assert by[date(2026, 11, 9)].status == "pending"
    assert by[date(2026, 11, 9)].billed == 1.0
    assert by[date(2026, 11, 9)].variance_pct is None
    assert by[date(2026, 11, 10)].billed is None
    assert not by[date(2026, 11, 8)].closed  # less than 72 h old
    total = billed.totals(days, comparable=True)
    assert total.billed == 15.0
    assert total.estimate == 17.0
    assert total.estimate_billed_days == 15.0  # pending days excluded
    assert total.variance_pct == 7.14  # (15 - 14) / 14
    assert not total.final


def test_days_before_the_first_poll_have_no_data() -> None:
    info = billed.BilledInfo({}, None, None)
    days = billed.build_days(
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 10, 20),
        info,
        _estimate({}),
        date(2026, 10, 1),
    )
    assert {d.status for d in days} == {"no_data"}


def test_currency_mismatch_shows_both_without_variance() -> None:
    today = date(2026, 10, 20)
    info = billed.BilledInfo({date(2026, 10, 10): 10.0}, "AUD", date(2026, 10, 18))
    est = _estimate({date(2026, 10, 10): 6.5}, mismatch=True)
    (day,) = billed.build_days(
        date(2026, 10, 10), date(2026, 10, 10), today, info, est, None
    )
    assert (day.billed, day.estimate, day.variance_pct) == (10.0, 6.5, None)
    assert billed.totals([day], comparable=False).variance_pct is None


def test_rounded_aud_embedding_price_is_derived_from_prompt_ratio() -> None:
    usd = billed.RetailCard(0.25, 0.025, 2.0, 0.02)
    aud = billed.RetailCard(0.4, 0.04, 3.2, 0.0)
    filled, derived = billed.fill_rounded_embed(aud, usd)
    assert derived
    assert filled.price_embed_per_million == pytest.approx(0.032)
    same, derived = billed.fill_rounded_embed(usd, usd)
    assert (same, derived) == (usd, False)


def test_range_presets_and_mapping_from_the_shared_picker() -> None:
    today = date(2026, 10, 20)
    assert billed.preset_range("7d", today) == (date(2026, 10, 14), today)
    assert billed.preset_range("24h", today) == (date(2026, 10, 19), today)
    assert billed.preset_range("mtd", today) == (date(2026, 10, 1), today)
    assert billed.preset_range("last_month", today) == (
        date(2026, 9, 1),
        date(2026, 9, 30),
    )
    assert billed.range_params(None, date(2026, 9, 21), today, today) == {
        "range": "30d"
    }
    assert billed.range_params(None, None, None, today) == {"range": "30d"}
    assert billed.range_params("mtd", None, None, today) == {"range": "mtd"}
    assert billed.range_params(None, date(2026, 9, 2), date(2026, 9, 5), today) == {
        "from": "2026-09-02",
        "to": "2026-09-05",
    }


def test_billed_budget_never_converts_currency(monkeypatch) -> None:
    cfg = get_settings()
    monkeypatch.setattr(cfg, "azure_monthly_budget", None)
    monkeypatch.setattr(cfg, "monthly_cost_budget_usd", 100.0)
    assert billed_budget(cfg, "USD") == 100.0
    assert billed_budget(cfg, "AUD") is None
    monkeypatch.setattr(cfg, "azure_monthly_budget", 150.0)
    assert billed_budget(cfg, "AUD") == 150.0


def test_cost_rules_are_registered_together() -> None:
    assert c.rule_for(c.AZURE_BILLED_BUDGET) is not None
    assert c.rule_for(c.AZURE_ESTIMATE_DRIFT) is not None
    assert {c.AZURE_BILLED_BUDGET, c.AZURE_ESTIMATE_DRIFT} <= c.DATABASE_RULES
    assert DATABASE_RULE_FUNCS["azure_costs"] is azure_cost_rule
    assert rules_covered("azure_costs") == c.AZURE_COST_RULES


# ---------------------------------------------------------------------------
# Postgres (CI): poll -> persist -> snapshot -> announce
# ---------------------------------------------------------------------------


def _enable(monkeypatch) -> None:
    cfg = get_settings()
    values = {
        "azure_insights_enabled": True,
        "azure_tenant_id": "11111111-1111-4111-8111-111111111111",
        "azure_client_id": "22222222-2222-4222-8222-222222222222",
        "azure_client_secret": SecretStr("fixture-secret-value-0123456789"),
        "azure_subscription_id": SUB,
        "azure_resource_group": "rg-askdiq",
        "azure_openai_resource_name": "askdiq-foundry",
        "azure_openai_region": "australiaeast",
    }
    for name, value in values.items():
        monkeypatch.setattr(cfg, name, value)
    monkeypatch.setattr(scheduler, "get_token_provider", lambda _s: FakeTokens())


@pytest.fixture
def published(monkeypatch) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []

    async def fake_publish(topic: str, item_id: str) -> None:
        seen.append((topic, str(item_id)))

    monkeypatch.setattr(snapshots, "publish_event", fake_publish)
    monkeypatch.setattr(scheduler, "publish_event", fake_publish)
    return seen


async def _poll(db_session, script: CostTransport, now: datetime) -> Any:
    return await scheduler.run_source(
        "costs",
        now=now,
        session_factory=shared_session_factory(db_session),
        gather=shared_session_runner(db_session).gather,
        transport=script(),
        rearm=False,  # no jobs-table writes outside the test transaction
    )


async def _rows(db_session) -> list[tuple]:
    result = await db_session.execute(
        text(
            "SELECT usage_date, meter, cost, currency, is_final FROM azure_billed_costs "
            "ORDER BY usage_date, meter"
        )
    )
    return [tuple(r) for r in result.all()]


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_poll_upserts_reratings_and_refreshes_the_card(
    db_session, monkeypatch, published
) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr(get_settings(), "dashboard_snapshots_persist", True)
    now = datetime(2026, 10, 20, 16, 5, tzinfo=UTC)
    script = CostTransport(
        (200, fixture("cost_query_page1.json"), None),
        (200, fixture("cost_query_page2.json"), None),
    )
    result = await _poll(db_session, script, now)
    await _settle()
    assert result is not None and result.rows == 6
    assert result.detail["cost_column"] == "Cost"
    assert result.detail["currency"] == "AUD"
    assert result.detail["from"] == "2026-10-01"
    rows = await _rows(db_session)
    assert len(rows) == 6
    assert all(r[3] == "AUD" and r[4] is False for r in rows)

    # Re-run: the same response is idempotent; a rerated value replaces the
    # old one and a meter Azure no longer reports in the window is dropped.
    rerated = fixture("cost_query_page1.json")
    rerated["properties"]["nextLink"] = None
    rerated["properties"]["rows"][0][0] = 0.5
    script = CostTransport((200, rerated, None))
    await _poll(db_session, script, now + timedelta(days=1))
    await _settle()
    rows = await _rows(db_session)
    assert len(rows) == 4
    out = next(r for r in rows if r[0] == date(2026, 10, 18) and "outpt" in r[1])
    assert float(out[2]) == 0.5

    # The card's snapshot was recomputed and announced on the live stream.
    changed = {item for topic, item in published if topic == TOPIC_CONSOLE_CHANGED}
    assert {COSTS_KIND, scheduler.STATUS_KIND} <= changed
    stored = (
        await db_session.execute(
            text("SELECT count(*) FROM dashboard_snapshots WHERE kind = :k"),
            {"k": COSTS_KIND},
        )
    ).scalar_one()
    assert stored == len(billed.PRESETS)
    row = (await status.read_all(db_session))["costs"]
    assert row.state == "ok"
    assert row.detail["covered_from"] == "2026-10-01"


async def test_empty_response_never_wipes_history(
    db_session, monkeypatch, published
) -> None:
    _enable(monkeypatch)
    now = datetime(2026, 10, 20, 16, tzinfo=UTC)
    await _poll(
        db_session, CostTransport((200, fixture("cost_query_pretax.json"), None)), now
    )
    await _poll(
        db_session,
        CostTransport((200, fixture("cost_query_empty.json"), None)),
        now + timedelta(days=1),
    )
    assert len(await _rows(db_session)) == 3


async def test_closed_months_become_final_and_old_rows_are_pruned(db_session) -> None:
    today = date(2026, 11, 4)
    rows = [
        CostRow(date(2026, 10, 31), "m", "r", costs.Decimal("1.5"), "USD"),
        CostRow(date(2025, 9, 1), "m", "r", costs.Decimal("1"), "USD"),
    ]
    now = datetime(2026, 11, 4, tzinfo=UTC)
    await costs.upsert_costs(
        db_session, rows, now=now - timedelta(days=5), today=date(2026, 10, 31)
    )
    before = {r[0]: r[4] for r in await _rows(db_session)}
    assert before == {date(2026, 10, 31): False, date(2025, 9, 1): True}
    await costs.store_costs(
        db_session, [], start=today, end=today, now=now, today=today, retention_days=400
    )
    remaining = await _rows(db_session)
    assert remaining == [
        (date(2026, 10, 31), "m", costs.Decimal("1.50000000"), "USD", True)
    ]


async def test_unsupported_subscription_is_a_clear_state(
    db_session, monkeypatch, published
) -> None:
    _enable(monkeypatch)
    script = CostTransport((422, fixture("cost_unsupported_offer.json"), None))
    result = await _poll(db_session, script, datetime(2026, 10, 20, 16, tzinfo=UTC))
    assert result is None
    row = (await status.read_all(db_session))["costs"]
    assert (row.state, row.consecutive_failures) == ("unsupported", 0)
    assert row.last_error_message == costs.UNSUPPORTED_OFFER_MESSAGE
    card = await billed.build_costs(db_session, get_settings(), {"range": "7d"})
    assert card.state == "unsupported"
    assert card.message == costs.UNSUPPORTED_OFFER_MESSAGE


async def test_throttled_poll_is_deferred(db_session, monkeypatch, published) -> None:
    _enable(monkeypatch)
    script = CostTransport(
        (429, fixture("cost_query_throttled.json"), {QPU_RETRY_AFTER: "300"})
    )
    with pytest.raises(JobDeferredError) as exc:
        await _poll(db_session, script, datetime(2026, 10, 20, 16, tzinfo=UTC))
    assert exc.value.run_after > datetime.now(UTC) + timedelta(seconds=200)
    assert (await status.read_all(db_session))["costs"].state == "throttled"


# ---------------------------------------------------------------------------
# Billed vs estimated (Postgres)
# ---------------------------------------------------------------------------


async def _seed_billed(db_session, rows: list[tuple[date, float, str]]) -> None:
    await costs.upsert_costs(
        db_session,
        [
            CostRow(d, "m", ACCOUNT.lower(), costs.Decimal(str(v)), cur)
            for d, v, cur in rows
        ],
        now=datetime.now(UTC),
        today=datetime.now(UTC).date(),
    )


async def _seed_classifier(
    db_session, day: date, *, cost: float, prompt: int, completion: int
) -> None:
    await db_session.execute(
        text(
            "INSERT INTO insight_llm_usage (id, created_at, model, turns, prompt_tokens, "
            "completion_tokens, total_tokens, cost_usd) VALUES (gen_random_uuid(), :at, "
            "'gpt-5-mini', 1, :p, :c, :t, :cost)"
        ),
        {
            "at": datetime.combine(day, time(12), UTC),
            "p": prompt,
            "c": completion,
            "t": prompt + completion,
            "cost": cost,
        },
    )


async def test_usd_bill_compares_with_the_usd_estimate(db_session, monkeypatch) -> None:
    _enable(monkeypatch)
    today = datetime.now(UTC).date()
    day = today - timedelta(days=4)
    await _seed_billed(
        db_session, [(day, 2.0, "USD"), (today - timedelta(days=2), 1.0, "USD")]
    )
    await _seed_classifier(db_session, day, cost=1.5, prompt=1000, completion=100)
    card = await billed.build_costs(db_session, get_settings(), {"range": "7d"})
    assert (card.currency, card.estimate_currency) == ("USD", "USD")
    assert not card.repriced and not card.currency_mismatch
    entry = next(d for d in card.days if d.date == day)
    assert entry.billed == 2.0
    assert entry.estimate >= 1.5
    assert entry.variance_pct is not None
    assert card.billed_through == today - timedelta(days=2)
    assert card.month_to_date.month == f"{today:%Y-%m}"
    assert card.lag_note


async def test_aud_bill_without_retail_prices_is_a_currency_mismatch(
    db_session, monkeypatch
) -> None:
    _enable(monkeypatch)
    today = datetime.now(UTC).date()
    day = today - timedelta(days=4)
    await _seed_billed(db_session, [(day, 3.0, "AUD")])
    if await db_session.scalar(
        text("SELECT to_regclass('azure_retail_prices') IS NOT NULL")
    ):
        await db_session.execute(text("DELETE FROM azure_retail_prices"))
    card = await billed.build_costs(db_session, get_settings(), {"range": "7d"})
    assert card.currency == "AUD"
    assert card.estimate_currency == "USD"
    assert card.currency_mismatch
    assert all(d.variance_pct is None for d in card.days)


_RETAIL_DDL = """
CREATE TABLE IF NOT EXISTS azure_retail_prices (
    meter_id uuid NOT NULL, currency varchar(3) NOT NULL,
    effective_start_date date NOT NULL, meter_name varchar(200) NOT NULL,
    product_name varchar(200), sku_name varchar(200), service_name varchar(100),
    arm_region_name varchar(40) NOT NULL, unit_of_measure varchar(20) NOT NULL,
    retail_price numeric(18, 10) NOT NULL, price_per_million numeric(18, 6),
    role varchar(16), model_key varchar(64), deployment_type varchar(16),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (meter_id, currency, effective_start_date)
)
"""


async def _seed_retail(db_session, currency: str, prices: dict[str, float]) -> None:
    cfg = get_settings()
    await db_session.execute(text(_RETAIL_DDL))
    for role, price in prices.items():
        model = (
            cfg.azure_retail_embedding_model
            if role == "embed"
            else cfg.azure_retail_llm_model
        )
        await db_session.execute(
            text(
                "INSERT INTO azure_retail_prices (meter_id, currency, effective_start_date, "
                "meter_name, arm_region_name, unit_of_measure, retail_price, "
                "price_per_million, role, model_key, deployment_type) VALUES "
                "(gen_random_uuid(), :cur, '2026-01-01', :meter, :region, '1M', :p, :p, "
                ":role, :model, :dtype)"
            ),
            {
                "cur": currency,
                "meter": f"test {role} {currency}",
                "region": cfg.azure_retail_region,
                "p": price,
                "role": role,
                "model": model,
                "dtype": cfg.azure_deployment_type,
            },
        )


async def test_aud_bill_is_compared_with_the_aud_repriced_estimate(
    db_session, monkeypatch
) -> None:
    _enable(monkeypatch)
    today = datetime.now(UTC).date()
    day = today - timedelta(days=4)
    await _seed_billed(db_session, [(day, 1.0, "AUD")])
    # 1M prompt + 1M completion classifier tokens on that day.
    await _seed_classifier(
        db_session, day, cost=2.25, prompt=1_000_000, completion=1_000_000
    )
    await _seed_retail(
        db_session,
        "USD",
        {"prompt": 0.25, "cached": 0.025, "output": 2.0, "embed": 0.02},
    )
    await _seed_retail(
        db_session, "AUD", {"prompt": 0.4, "cached": 0.04, "output": 3.2, "embed": 0.03}
    )
    card = await billed.build_costs(db_session, get_settings(), {"range": "7d"})
    assert card.repriced and not card.currency_mismatch
    assert card.estimate_currency == "AUD"
    entry = next(d for d in card.days if d.date == day)
    assert entry.estimate == pytest.approx(3.6)  # 0.4 + 3.2 AUD
    assert entry.estimate_usd == pytest.approx(2.25)
    assert entry.variance_pct == pytest.approx(260.0)


async def test_cost_alert_rules_raise_on_billed_budget_and_drift(
    db_session, monkeypatch
) -> None:
    cfg = get_settings()
    monkeypatch.setattr(cfg, "azure_monthly_budget", None)
    monkeypatch.setattr(cfg, "monthly_cost_budget_usd", 10.0)
    now = datetime.now(UTC)
    today = now.date()
    day = today - timedelta(days=3)
    await _seed_billed(
        db_session, [(day, 9.0, "USD"), (today - timedelta(days=1), 0.5, "USD")]
    )
    await _seed_classifier(db_session, day, cost=4.0, prompt=10, completion=10)
    findings = await azure_cost_rule(db_session, now, cfg)
    by_rule = {f.rule: f for f in findings}
    if day >= today.replace(day=1):  # the seeded day is in this month
        budget = by_rule[c.AZURE_BILLED_BUDGET]
        assert budget.dedupe_key == f"azure_billed_budget:{today:%Y-%m}"
        assert budget.severity == "warning"
    drift = by_rule[c.AZURE_ESTIMATE_DRIFT]
    assert drift.dedupe_key == f"azure_estimate_drift:{day.isoformat()}"
    assert drift.details["counts"]["billed"] == 9.0


async def test_no_billed_data_raises_nothing(db_session) -> None:
    assert await azure_cost_rule(db_session, datetime.now(UTC), get_settings()) == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


async def test_costs_endpoint_not_connected_shape(client) -> None:
    response = await client.get("/admin/azure/costs", params={"range": "7d"})
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "not_connected"
    assert body["range"]["preset"] == "7d"
    assert len(body["days"]) == 7
    assert {"billed", "estimate", "variance_pct", "final"} <= set(body["month_to_date"])
    again = await client.get(
        "/admin/azure/costs",
        params={"range": "7d"},
        headers={"If-None-Match": response.headers["etag"]},
    )
    assert again.status_code == 304


async def test_costs_endpoint_maps_the_shared_range_to_a_preset(client) -> None:
    today = datetime.now(UTC).date()
    response = await client.get(
        "/admin/azure/costs",
        params={
            "from_date": (today - timedelta(days=29)).isoformat(),
            "to_date": today.isoformat(),
        },
    )
    assert response.status_code == 200
    assert response.json()["range"]["preset"] == "30d"
    lo, hi = today - timedelta(days=50), today - timedelta(days=47)
    custom = await client.get(
        "/admin/azure/costs",
        params={"from_date": lo.isoformat(), "to_date": hi.isoformat()},
    )
    assert custom.json()["range"] == {
        "preset": None,
        "from_date": lo.isoformat(),
        "to_date": hi.isoformat(),
    }
    assert len(custom.json()["days"]) == 4


@pytest.mark.parametrize(
    "params",
    [
        {"from_date": "2026-09-05", "to_date": "2026-09-01"},
        {"from_date": "2026-09-05"},
        {"from_date": "2020-01-01", "to_date": "2026-09-01"},
        {"range": "year"},
    ],
)
async def test_costs_endpoint_rejects_bad_ranges(client, params) -> None:
    response = await client.get("/admin/azure/costs", params=params)
    assert response.status_code == 422


def test_costs_endpoint_is_documented() -> None:
    from main import app  # noqa: PLC0415

    op = app.openapi()["paths"]["/admin/azure/costs"]["get"]
    assert op["summary"] == "Azure billed vs estimated cost"
    assert {"401", "403", "422"} <= set(op["responses"])
    names = {p["name"] for p in op["parameters"]}
    assert {"range", "from_date", "to_date"} <= names
