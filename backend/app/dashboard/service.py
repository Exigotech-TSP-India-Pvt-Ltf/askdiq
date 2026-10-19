"""Windowed dashboard KPIs, sparklines, deltas and chart series (B7.1).

``window_payload`` runs the single-statement aggregates of
``app.dashboard.queries`` in parallel (``ScopedRunner.gather``: one session
per task) and turns them into the additive fields of the dashboard contract
(``docs/technical/frontend/contracts/dashboard-aggregations.md``). Pure shaping lives
in small functions so it is unit-tested without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.concurrency import ScopedRunner
from app.core.config import get_settings
from app.dashboard import active_visitors, queries
from app.dashboard.queries import IntentCount, Split
from app.dashboard.window import (
    Window,
    delta,
    money,
    month_bounds,
    month_forecast,
    percent,
    ratio,
)
from app.governance.outcomes import ALL_OUTCOMES

KPI_KEYS: tuple[str, ...] = (
    "users",
    "active_users",
    "sessions",
    "queries",
    "feedback",
    "avg_latency_ms",
    "error_rate",
    "cost_usd",
)


def _int(values: dict[str, Any], key: str) -> int:
    return int(values.get(key) or 0)


def _float_or_none(value: Any) -> float | None:
    return None if value is None else float(value)


def error_rate(turns: dict[str, Any], server: dict[str, Any]) -> float | None:
    """Percent: (failed turns + server errors) / (turns + server errors)."""
    errors = _int(server, "errors")
    return percent(_int(turns, "failed") + errors, _int(turns, "turns") + errors)


def cost_value(exchanges: dict[str, Any]) -> float | None:
    """Summed cost; None when no exchange in the bucket carried a cost."""
    if not _int(exchanges, "cost_rows"):
        return None
    return money(exchanges.get("cost"))


def cumulative(starts: list[datetime], first_seen: dict[datetime, int]) -> list[int]:
    out, running = [], 0
    for start in starts:
        running += first_seen.get(start, 0)
        out.append(running)
    return out


def _series(window: Window, values: list[Any]) -> list[dict[str, Any]]:
    return [
        {"t": window.label(start), "value": value}
        for start, value in zip(window.starts(), values, strict=True)
    ]


@dataclass(frozen=True, slots=True)
class Sources:
    """The windowed aggregates one dashboard response is built from."""

    messages: Split
    first_seen: dict[datetime, int]
    sessions: Split
    feedback: Split
    latency: Split
    turns: Split
    server: Split
    exchanges: Split


def _at(split: Split, start: datetime) -> dict[str, Any]:
    return split.buckets.get(start, {})


def sparklines(window: Window, src: Sources) -> dict[str, list[dict[str, Any]]]:
    """One point per bucket for every KPI (null = no data in the bucket).

    ``users`` is cumulative (active visitors so far in the window, so the
    last point equals the KPI); ``active_users`` is active visitors per
    bucket (``app.dashboard.active_visitors``).
    """
    starts = window.starts()

    def column(split: Split, key: str) -> list[dict[str, Any]]:
        return _series(window, [_int(_at(split, s), key) for s in starts])

    return {
        "users": _series(window, cumulative(starts, src.first_seen)),
        "active_users": column(src.messages, "visitors"),
        "sessions": column(src.sessions, "sessions"),
        "queries": column(src.messages, "queries"),
        "feedback": column(src.feedback, "feedback"),
        "avg_latency_ms": _series(
            window,
            [_float_or_none(_at(src.latency, s).get("avg_ms")) for s in starts],
        ),
        "error_rate": _series(
            window,
            [error_rate(_at(src.turns, s), _at(src.server, s)) for s in starts],
        ),
        "cost_usd": _series(
            window, [cost_value(_at(src.exchanges, s)) for s in starts]
        ),
    }


def period_kpis(src: Sources, *, current: bool) -> dict[str, float | None]:
    """KPI totals of the current or the previous window."""

    def pick(split: Split) -> dict[str, Any]:
        return split.current if current else split.previous

    return {
        "users": _int(pick(src.messages), "visitors"),
        "active_users": _int(pick(src.messages), "active"),
        "sessions": _int(pick(src.sessions), "sessions"),
        "queries": _int(pick(src.messages), "queries"),
        "feedback": _int(pick(src.feedback), "feedback"),
        "avg_latency_ms": _float_or_none(pick(src.latency).get("avg_ms")),
        "error_rate": error_rate(pick(src.turns), pick(src.server)),
        "cost_usd": money(pick(src.exchanges).get("cost")),
    }


def outcome_series(window: Window, exchanges: Split) -> list[dict[str, Any]]:
    out = []
    for start in window.starts():
        row = exchanges.buckets.get(start, {})
        out.append(
            {"t": window.label(start), **{k: _int(row, k) for k in ALL_OUTCOMES}}
        )
    return out


def latency_series(window: Window, latency: Split) -> list[dict[str, Any]]:
    out = []
    for start in window.starts():
        row = latency.buckets.get(start, {})
        out.append(
            {
                "t": window.label(start),
                "p50_ms": _float_or_none(row.get("p50_ms")),
                "p95_ms": _float_or_none(row.get("p95_ms")),
                "count": _int(row, "count"),
            }
        )
    return out


def satisfaction(feedback: Split) -> dict[str, Any]:
    up, down = _int(feedback.current, "up"), _int(feedback.current, "down")
    return {"thumbs_up": up, "thumbs_down": down, "ratio": ratio(up, up + down)}


def intents_payload(rows: list[IntentCount]) -> list[dict[str, Any]]:
    return [
        {
            "intent": r.intent,
            "label": r.label,
            "count": r.count,
            "share": ratio(r.count, r.total) or 0.0,
        }
        for r in rows
    ]


async def window_payload(
    runner: ScopedRunner, audience: str, window: Window, now: datetime
) -> dict[str, Any]:
    """The additive dashboard fields for ``window`` (plus windowed KPI totals)."""
    month_start, month_end = month_bounds(now.date())
    results = await runner.gather(
        lambda s: queries.messages_split(s, audience, window),
        lambda s: queries.first_seen_per_bucket(s, audience, window),
        lambda s: queries.sessions_split(s, audience, window),
        lambda s: queries.feedback_split(s, audience, window),
        lambda s: queries.latency_split(s, audience, window),
        lambda s: queries.turn_errors_split(s, audience, window),
        lambda s: queries.server_errors_split(s, audience, window),
        lambda s: queries.exchanges_split(s, audience, window),
        lambda s: queries.top_intents(s, audience, window),
        lambda s: queries.daily_cost(s, audience, month_start, month_end),
        lambda s: active_visitors.active_counts(s, audience, now),
    )
    src = Sources(*results[:8])
    intents, month_daily, visitor_counts = results[8], results[9], results[10]
    current = period_kpis(src, current=True)
    previous = period_kpis(src, current=False)
    forecast = month_forecast(
        month_daily, now.date(), get_settings().monthly_cost_budget_usd
    )
    return {
        "users": current["users"],
        "active_users": current["active_users"],
        "sessions": current["sessions"],
        "conversations": current["sessions"],
        "queries": current["queries"],
        "feedback": current["feedback"],
        "avg_latency_ms": current["avg_latency_ms"] or 0.0,
        "error_rate": current["error_rate"] or 0.0,
        "window": window.echo(),
        "cost_usd": current["cost_usd"],
        "sparklines": sparklines(window, src),
        "deltas": {k: delta(current[k], previous[k]) for k in KPI_KEYS},
        "queries_by_outcome": outcome_series(window, src.exchanges),
        "latency_series": latency_series(window, src.latency),
        "cost": forecast.summary(),
        "satisfaction": satisfaction(src.feedback),
        "top_intents": intents_payload(intents),
        "active_visitors": active_visitors.summary(visitor_counts),
    }
