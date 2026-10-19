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
