"""Cost Management billed cost (Azure Insights Phase 3, job ``azure.costs``).

Once a day (``AZURE_COSTS_INTERVAL_SECONDS``, at ``AZURE_COSTS_RUN_AT`` in
``INSIGHTS_TIMEZONE``) one Cost Management Query reads the ActualCost of the
Foundry account per UTC day and meter:

    POST /subscriptions/{sub}/resourceGroups/{rg}/providers/
         Microsoft.CostManagement/query?api-version=...

- window: the last 7 days plus month to date; until day 5 of a month also
  the previous month (Azure rerates until month close);
- columns are read **by name** (the cost column is ``Cost`` or
  ``PreTaxCost`` depending on the agreement type; ``UsageDate`` is an int
  ``yyyymmdd``); ``nextLink`` pages are POSTed with the same body;
- rows are upserted on ``(usage_date, meter, resource_id)``; rows of the
  re-read window that Azure no longer reports are removed (rerated away);
  ``is_final`` once the month closed + 3 days; history kept
  ``AZURE_COSTS_RETENTION_DAYS``;
- an unsupported subscription / billing setup (classic CSP, sponsorship,
  disabled charge visibility) is recorded as state ``unsupported`` with a
  clear message instead of a failure streak.

Throttling (429 with the QPU retry-after header) is handled by the client and
the scheduler (the job is deferred, no attempt spent). Never calls Azure from
a page view.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.azure_insights import status as sync_status
from app.azure_insights.client import MAX_PAGES, AzureClient
from app.azure_insights.errors import (
    AzureApiError,
    AzureNotConfiguredError,
    AzureUnsupportedError,
)
from app.azure_insights.scheduler import (
    AzureSource,
    SourceContext,
    SourceResult,
    register_source,
)
from app.core.config import Settings
from app.core.logging_config import get_logger
from app.models.db_models import AzureBilledCost

logger = get_logger(__name__)

COSTS_SOURCE = "costs"
# Snapshot kind of the "Billed vs estimated" card (builder in billed.py).
COSTS_KIND = "azure.costs.billed"

RECENT_DAYS = 7
# Until this day of a month the previous month is re-read too.
PREVIOUS_MONTH_UNTIL_DAY = 5
# A month is final this many days after it closed.
FINAL_AFTER_DAYS = 3
COST_TYPE = "ActualCost"
# Requested aggregation alias; the response names it Cost or PreTaxCost.
COST_ALIAS = "Cost"
COST_COLUMNS = ("Cost", "PreTaxCost")
NO_CURRENCY = "XXX"  # ISO 4217 "no currency" (column missing)
_INSERT_CHUNK = 1000
_HTTP_CLIENT_ERROR = 400
_HTTP_SERVER_ERROR = 500
_HTTP_TOO_MANY = 429

UNSUPPORTED_OFFER_MESSAGE = (
    "Cost Management is not available for this subscription type (for "
    "example classic CSP, sponsorship or Azure for Students). Billed cost "
    "stays unavailable; metrics, prices, quota and health still work."
)
UNSUPPORTED_POLICY_MESSAGE = (
    "Azure hides the charges for this billing account: an EA administrator "
    "must enable 'Account owners can view charges', or an MCA billing "
    "profile owner 'Azure charges' (see foundry-usage-data-review.md 9.1.1)."
)
# Error codes Cost Management returns when charges are hidden by a policy.
_POLICY_CODES = frozenset(
    {
        "accountcostdisabled",
        "departmentcostdisabled",
        "indirectcostdisabled",
        "billingaccessdenied",
    }
)
_OFFER_CODES = frozenset(
    {
        "unsupportedsubscription",
        "subscriptiontypenotsupported",
        "unsupportedoffertype",
        "offertypenotsupported",
        "unsupportedbillingtype",
    }
)
_OFFER_TEXT = re.compile(
    r"(?i)(cost management supports only|not associated with a valid offer|"
    r"offer type|subscription type is not supported|ms-azr-0145p|sponsorship|"
    r"azure for students)"
)


# ---------------------------------------------------------------------------
# Window, request and finality
# ---------------------------------------------------------------------------


def month_start(day: date) -> date:
    return day.replace(day=1)


def previous_month_start(day: date) -> date:
    return month_start(month_start(day) - timedelta(days=1))


def query_window(today: date) -> tuple[date, date]:
    """Inclusive UTC days re-read by one poll."""
    start = min(today - timedelta(days=RECENT_DAYS), month_start(today))
    if today.day <= PREVIOUS_MONTH_UNTIL_DAY:
        start = min(start, previous_month_start(today))
    return start, today


def final_before(today: date) -> date:
    """Usage days before this date are final (their month closed + 3 days)."""
    return month_start(today - timedelta(days=FINAL_AFTER_DAYS))


def is_final_day(usage_date: date, today: date) -> bool:
    return usage_date < final_before(today)


def cost_query_path(settings: Settings) -> str:
    return (
        f"/subscriptions/{settings.azure_subscription_id}"
        f"/resourceGroups/{settings.azure_resource_group}"
        "/providers/Microsoft.CostManagement/query"
    )


def query_body(account_id: str, start: date, end: date) -> dict[str, Any]:
    """ActualCost per day, grouped by resource and meter, for the account."""
    return {
        "type": COST_TYPE,
        "timeframe": "Custom",
        "timePeriod": {
            "from": f"{start.isoformat()}T00:00:00Z",
            "to": f"{end.isoformat()}T23:59:59Z",
        },
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": COST_ALIAS, "function": "Sum"}},
            "grouping": [
                {"type": "Dimension", "name": "ResourceId"},
                {"type": "Dimension", "name": "Meter"},
            ],
            "filter": {
                "dimensions": {
                    "name": "ResourceId",
                    "operator": "In",
                    "values": [account_id.lower()],
                }
            },
        },
    }


# ---------------------------------------------------------------------------
# Parsing (columns by name)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CostRow:
    usage_date: date
    meter: str
    resource_id: str
    cost: Decimal
    currency: str


@dataclass(slots=True)
class CostFetch:
    rows: list[CostRow]
    pages: int = 0
    cost_column: str | None = None


def parse_usage_date(value: Any) -> date:
    """``UsageDate``: int ``yyyymmdd`` (Daily), or a ``yyyymmdd`` / ISO string."""
    raw = str(value).strip()
    if re.fullmatch(r"\d{8}", raw):
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
    return date.fromisoformat(raw[:10])


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value)) if value is not None else Decimal(0)
    except InvalidOperation:
        raise AzureApiError(
            "invalid_cost", "Cost Management returned a non-numeric cost"
        ) from None


def parse_page(
    properties: dict[str, Any], default_resource: str
) -> tuple[list[CostRow], str | None]:
    """Rows of one Query page and the cost column used; columns by name."""
    columns = properties.get("columns") or []
    rows = properties.get("rows") or []
    index = {
        str(col.get("name", "")).lower(): i
        for i, col in enumerate(columns)
        if isinstance(col, dict)
    }
    if not rows:
        return [], None
    cost_column = next((c for c in COST_COLUMNS if c.lower() in index), None)
    if cost_column is None or "usagedate" not in index:
        names = ", ".join(str(c.get("name")) for c in columns if isinstance(c, dict))
        raise AzureApiError(
            "unexpected_columns", f"Cost Management columns not recognised: {names}"
        )

    def cell(row: list[Any], name: str) -> Any:
        i = index.get(name)
        return row[i] if i is not None and i < len(row) else None

    out: list[CostRow] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        out.append(
            CostRow(
                usage_date=parse_usage_date(cell(row, "usagedate")),
                meter=str(cell(row, "meter") or "")[:200],
                resource_id=str(cell(row, "resourceid") or default_resource).lower()[
                    :512
                ],
                cost=_decimal(cell(row, cost_column.lower())),
                currency=str(cell(row, "currency") or NO_CURRENCY).upper()[:3],
            )
        )
    return out, cost_column


def merge_rows(rows: Iterable[CostRow]) -> list[CostRow]:
    """One row per natural key (a key split across pages is summed)."""
    merged: dict[tuple[date, str, str], CostRow] = {}
    for row in rows:
        key = (row.usage_date, row.meter, row.resource_id)
        seen = merged.get(key)
        merged[key] = (
            row if seen is None else CostRow(*key, seen.cost + row.cost, seen.currency)
        )
    return list(merged.values())


async def fetch_costs(  # pylint: disable=too-many-arguments
    api: AzureClient,
    path: str,
    body: dict[str, Any],
    api_version: str,
    default_resource: str,
    *,
    max_pages: int = MAX_PAGES,
) -> CostFetch:
    """Every row of the query; ``nextLink`` pages are POSTed with the body."""
    result = CostFetch(rows=[])
    url: str | None = path
    params: dict[str, Any] | None = {"api-version": api_version}
    while url and result.pages < max_pages:
        page = await api.post_json(url, body, params)
        result.pages += 1
        props = page.get("properties") or {}
        rows, column = parse_page(props, default_resource)
        result.rows.extend(rows)
        result.cost_column = result.cost_column or column
        url = props.get("nextLink") or None
        params = None  # the next link carries api-version and $skiptoken
    if url:
        logger.warning("azure_costs_pagination_capped", pages=result.pages)
    result.rows = merge_rows(result.rows)
    return result


def classify_unsupported(error: AzureApiError) -> AzureUnsupportedError | None:
    """The friendly "unsupported" error for an offer / policy refusal, else None."""
    status = error.status_code
    if (
        status is None
        or not _HTTP_CLIENT_ERROR <= status < _HTTP_SERVER_ERROR
        or status == _HTTP_TOO_MANY
    ):
        return None
    code = error.code.lower()
    if code in _POLICY_CODES:
        message = UNSUPPORTED_POLICY_MESSAGE
    elif code in _OFFER_CODES or _OFFER_TEXT.search(error.message):
        message = UNSUPPORTED_OFFER_MESSAGE
    else:
        return None
    return AzureUnsupportedError(error.code, message, status_code=status)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def upsert_costs(
    session: AsyncSession, rows: list[CostRow], *, now: datetime, today: date
) -> int:
    """``INSERT ... ON CONFLICT (usage_date, meter, resource_id) DO UPDATE``."""
    for i in range(0, len(rows), _INSERT_CHUNK):
        chunk = rows[i : i + _INSERT_CHUNK]
        stmt = insert(AzureBilledCost).values(
            [
                {
                    "usage_date": r.usage_date,
                    "meter": r.meter,
                    "resource_id": r.resource_id,
                    "cost": r.cost,
                    "currency": r.currency,
                    "is_final": is_final_day(r.usage_date, today),
                    "fetched_at": now,
                }
                for r in chunk
            ]
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    AzureBilledCost.usage_date,
                    AzureBilledCost.meter,
                    AzureBilledCost.resource_id,
                ],
                set_={
                    "cost": stmt.excluded.cost,
                    "currency": stmt.excluded.currency,
                    "is_final": stmt.excluded.is_final,
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )
        )
    return len(rows)


async def store_costs(  # pylint: disable=too-many-arguments
    session: AsyncSession,
    rows: list[CostRow],
    *,
    start: date,
    end: date,
    now: datetime,
    today: date,
    retention_days: int,
) -> int:
    """Upsert the window, drop rows Azure no longer reports in it (rerated
    away; only when Azure returned rows, so an empty glitch never wipes
    history), mark closed months final and prune past the retention."""
    written = await upsert_costs(session, rows, now=now, today=today)
    if rows:
        await session.execute(
            delete(AzureBilledCost).where(
                AzureBilledCost.usage_date.between(start, end),
                AzureBilledCost.fetched_at < now,
            )
        )
    await session.execute(
        update(AzureBilledCost)
        .where(
            AzureBilledCost.is_final.is_(False),
            AzureBilledCost.usage_date < final_before(today),
        )
        .values(is_final=True)
    )
    await session.execute(
        delete(AzureBilledCost).where(
            AzureBilledCost.usage_date < today - timedelta(days=retention_days)
        )
    )
    return written


async def billed_through(session: AsyncSession) -> date | None:
    return await session.scalar(select(func.max(AzureBilledCost.usage_date)))


def _main_currency(rows: list[CostRow]) -> str | None:
    totals: dict[str, int] = {}
    for row in rows:
        totals[row.currency] = totals.get(row.currency, 0) + 1
    return max(totals, key=lambda c: totals[c]) if totals else None


async def _covered_from(ctx: SourceContext, start: date) -> date:
    """Earliest day ever re-read (days before it have no billed data)."""
    async with ctx.session_factory() as session:
        row = (await sync_status.read_all(session)).get(COSTS_SOURCE)
    previous = (row.detail or {}).get("covered_from") if row is not None else None
    try:
        return min(start, date.fromisoformat(previous)) if previous else start
    except (TypeError, ValueError):
        return start


async def run_costs(ctx: SourceContext) -> SourceResult:
    settings = ctx.settings
    account = settings.azure_account_resource_id
    if account is None:
        raise AzureNotConfiguredError()
    today = ctx.now.astimezone(UTC).date()
    start, end = query_window(today)
    covered_from = await _covered_from(ctx, start)
    try:
        async with ctx.arm_client() as api:
            fetched = await fetch_costs(
                api,
                cost_query_path(settings),
                query_body(account, start, end),
                settings.azure_costs_api_version,
                account.lower(),
            )
    except AzureApiError as exc:
        unsupported = classify_unsupported(exc)
        if unsupported is not None:
            raise unsupported from None
        raise
    async with ctx.session_factory() as session:
        written = await store_costs(
            session,
            fetched.rows,
            start=start,
            end=end,
            now=ctx.now,
            today=today,
            retention_days=settings.azure_costs_retention_days,
        )
        through = await billed_through(session)
        await session.commit()
    return SourceResult(
        rows=written,
        detail={
            "from": start.isoformat(),
            "to": end.isoformat(),
            "covered_from": covered_from.isoformat(),
            "pages": fetched.pages,
            "cost_column": fetched.cost_column,
            "currency": _main_currency(fetched.rows),
            "billed_through": through.isoformat() if through else None,
        },
    )


def _anchor(settings: Settings) -> tuple[str, time]:
    return settings.insights_timezone, time.fromisoformat(settings.azure_costs_run_at)


register_source(
    AzureSource(
        name=COSTS_SOURCE,
        label="Billed cost (Cost Management)",
        interval=lambda s: s.azure_costs_interval_seconds,
        run=run_costs,
        snapshot_kinds=(COSTS_KIND,),
        order=30,
        anchor=_anchor,
    )
)
