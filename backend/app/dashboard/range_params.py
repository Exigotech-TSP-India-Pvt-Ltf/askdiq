"""Optional ``from`` / ``to`` day-range query dependency for admin endpoints.

Endpoints that predate the global date range keep their old behaviour when
neither bound is sent (the dependency returns ``None``); with either bound
they get the same inclusive UTC ``DayRange`` (and the same 422s) as the
entity pages' ``day_range``.
"""

from __future__ import annotations

from datetime import date

from fastapi import Query

from app.core.config import get_settings
from app.dashboard.window import DayRange, resolve_days, utcnow


def optional_day_range(
    first: date | None = Query(
        None, alias="from", description="First day, YYYY-MM-DD (UTC), inclusive."
    ),
    last: date | None = Query(
        None, alias="to", description="Last day, YYYY-MM-DD (UTC), inclusive."
    ),
) -> DayRange | None:
    """``None`` without bounds (legacy window); else the validated day range."""
    if first is None and last is None:
        return None
    return resolve_days(
        first,
        last,
        today=utcnow().date(),
        max_days=get_settings().usage_max_range_days,
    )


# The shared range picker's presets as the console sends them to day-based
# endpoints (UTC days ending today; "Last 24 hours" touches yesterday too).
PRESET_SPANS_DAYS = (2, 7, 30, 90)


def preset_day_ranges() -> list[DayRange | None]:
    """``None`` (no bounds) plus every preset, for snapshot defaults."""
    today = utcnow().date()
    return [None] + [
        resolve_days(
            date.fromordinal(today.toordinal() - (span - 1)),
            today,
            today=today,
            max_days=get_settings().usage_max_range_days,
        )
        for span in PRESET_SPANS_DAYS
    ]


def day_params(days: DayRange | None) -> dict[str, str | None]:
    """Snapshot parameters of a day range (JSON-safe, canonical)."""
    if days is None:
        return {"from": None, "to": None}
    return {"from": days.first.isoformat(), "to": days.last.isoformat()}


def params_days(params: dict) -> DayRange | None:
    """Inverse of :func:`day_params`."""
    if not params.get("from"):
        return None
    return DayRange(
        date.fromisoformat(params["from"]), date.fromisoformat(params["to"])
    )
