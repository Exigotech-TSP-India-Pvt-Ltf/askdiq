"""Time windows, buckets and the pure arithmetic behind the chart series.

Everything here is side-effect free so it is unit-tested without a database.
Datetimes are naive UTC, matching the ``DateTime`` columns (``utcnow`` writes);
API output appends ``Z``.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import HTTPException, status

Bucket = Literal["hour", "day"]

# Windows up to this long use hourly buckets, longer ones daily buckets.
HOUR_BUCKET_MAX = timedelta(hours=48)
DEFAULT_SPAN = timedelta(days=7)
ACTIVE_SPAN = timedelta(hours=24)
MONEY_DP = 6
RATIO_DP = 6


def utcnow() -> datetime:
    """Naive UTC "now" (the DB columns are naive UTC)."""
    return datetime.now(UTC).replace(tzinfo=None)


def to_naive_utc(value: datetime) -> datetime:
    """Aware -> naive UTC; naive values are taken as UTC already."""
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def iso_z(value: datetime) -> str:
    """Naive UTC -> ``2026-10-16T13:00:00Z`` (seconds precision)."""
    return to_naive_utc(value).replace(microsecond=0).isoformat() + "Z"


def floor_minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def truncate(value: datetime, bucket: Bucket) -> datetime:
    """Start of the hour or UTC day containing ``value``."""
    if bucket == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def bucket_for(span: timedelta) -> Bucket:
    return "hour" if span <= HOUR_BUCKET_MAX else "day"


def bucket_label(start: datetime, bucket: Bucket) -> str:
    """``YYYY-MM-DD`` for day buckets, ISO UTC for hour buckets."""
    return start.date().isoformat() if bucket == "day" else iso_z(start)


def bucket_starts(start: datetime, end: datetime, bucket: Bucket) -> list[datetime]:
    """Every bucket start overlapping ``[start, end)``, oldest first."""
    step = timedelta(hours=1) if bucket == "hour" else timedelta(days=1)
    current = truncate(start, bucket)
    out: list[datetime] = []
    while current < end:
        out.append(current)
        current += step
    return out


def unprocessable(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail
    )


@dataclass(frozen=True, slots=True)
class Window:
    """``[start, end)`` in naive UTC plus the equal-length window before it."""

    start: datetime
    end: datetime

    @property
    def span(self) -> timedelta:
        return self.end - self.start

    @property
    def bucket(self) -> Bucket:
        return bucket_for(self.span)

    @property
    def previous_start(self) -> datetime:
        return self.start - self.span

    def starts(self) -> list[datetime]:
        return bucket_starts(self.start, self.end, self.bucket)

    def label(self, start: datetime) -> str:
        return bucket_label(start, self.bucket)

    def echo(self) -> dict[str, str]:
        return {
            "from": iso_z(self.start),
            "to": iso_z(self.end),
            "bucket": self.bucket,
            "previous_from": iso_z(self.previous_start),
            "previous_to": iso_z(self.start),
        }


def resolve_window(
    start: datetime | None,
    end: datetime | None,
    *,
    now: datetime,
    max_days: int,
) -> Window | None:
    """The requested window, or None when neither bound was sent (legacy mode).

    A missing ``to`` is "now" (to the minute); a missing ``from`` is 7 days
    before ``to``. ``to <= from`` or a span above ``max_days`` is a 422.
    """
    if start is None and end is None:
        return None
    end_utc = to_naive_utc(end) if end is not None else floor_minute(now)
    start_utc = to_naive_utc(start) if start is not None else end_utc - DEFAULT_SPAN
    if end_utc <= start_utc:
        raise unprocessable("`to` must be after `from`.")
    if end_utc - start_utc > timedelta(days=max_days):
        raise unprocessable(
            f"The requested range exceeds the maximum of {max_days} days."
        )
    return Window(start_utc, end_utc)


@dataclass(frozen=True, slots=True)
class DayRange:
    """Inclusive ``YYYY-MM-DD`` range (entity pages), as a half-open datetime span."""

    first: date
    last: date

    @property
    def start(self) -> datetime:
        return datetime.combine(self.first, datetime.min.time())

    @property
    def end(self) -> datetime:
        return datetime.combine(self.last + timedelta(days=1), datetime.min.time())

    def days(self) -> list[date]:
        return [
            self.first + timedelta(days=i)
            for i in range((self.last - self.first).days + 1)
        ]


DEFAULT_DAYS = 7


def parse_day(value: str | None, name: str) -> date | None:
    """``YYYY-MM-DD`` (blank = absent); anything else is a 422."""
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise unprocessable(f"`{name}` must be a day (YYYY-MM-DD).") from None


def resolve_days(
    first: date | None, last: date | None, *, today: date, max_days: int
) -> DayRange:
    """Inclusive day range; default is the last 7 days ending today (UTC)."""
    last_day = last or today
    first_day = first or (last_day - timedelta(days=DEFAULT_DAYS - 1))
    if last_day < first_day:
        raise unprocessable("`to` must not be before `from`.")
    if (last_day - first_day).days + 1 > max_days:
        raise unprocessable(
            f"The requested range exceeds the maximum of {max_days} days."
        )
    return DayRange(first_day, last_day)


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------


def delta(current: float | None, previous: float | None) -> float | None:
    """``(current - previous) / previous``; None when either side is unknown or previous is 0."""
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / previous, RATIO_DP)


def ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, RATIO_DP) if denominator else None


def percent(numerator: float, denominator: float) -> float | None:
    return numerator * 100 / denominator if denominator else None


def money(value: float | None) -> float:
    return round(float(value or 0.0), MONEY_DP)


@dataclass(frozen=True, slots=True)
class MonthForecast:
    """Current-month cost with a linear month-end projection."""

    month: str
    mtd_usd: float
    forecast_month_end_usd: float | None
    budget_usd: float | None
    days_elapsed: int
    days_in_month: int
    daily: list[dict[str, float | str | None]]

    def summary(self) -> dict[str, object]:
        return {
            "currency": "USD",
            "month": self.month,
            "mtd_usd": self.mtd_usd,
            "forecast_month_end_usd": self.forecast_month_end_usd,
            "budget_usd": self.budget_usd,
            "days_elapsed": self.days_elapsed,
            "days_in_month": self.days_in_month,
        }


def month_bounds(today: date) -> tuple[datetime, datetime]:
    """``[first day 00:00, first day of next month 00:00)`` for ``today``'s month."""
    first = today.replace(day=1)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    start = datetime.combine(first, datetime.min.time())
    return start, start + timedelta(days=days_in_month)


def month_forecast(
    daily_cost: Mapping[date, float], today: date, budget_usd: float | None
) -> MonthForecast:
    """Linear projection ``mtd / days_elapsed * days_in_month``.

    ``days_elapsed`` is today's day of the month (today counts, partially). The
    projection is None on day 1 (no complete day yet). ``daily`` has one point
    per day: ``actual_usd`` is the cumulative MTD up to that day (None for
    future days); ``forecast_usd`` is the projected cumulative cost from today
    on (None before today), so both lines meet at today.
    """
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    elapsed = today.day
    mtd = sum(v for d, v in daily_cost.items() if d.month == today.month and d <= today)
    rate = mtd / elapsed if elapsed > 1 else None
    forecast = money(rate * days_in_month) if rate is not None else None

    points: list[dict[str, float | str | None]] = []
    running = 0.0
    for day_no in range(1, days_in_month + 1):
        day = today.replace(day=day_no)
        actual: float | None = None
        if day_no <= elapsed:
            running += daily_cost.get(day, 0.0)
            actual = money(running)
        projected: float | None = None
        if rate is not None and day_no >= elapsed:
            projected = money(mtd + rate * (day_no - elapsed))
        points.append(
            {"t": day.isoformat(), "actual_usd": actual, "forecast_usd": projected}
        )
    return MonthForecast(
        month=f"{today.year:04d}-{today.month:02d}",
        mtd_usd=money(mtd),
        forecast_month_end_usd=forecast,
        budget_usd=budget_usd,
        days_elapsed=elapsed,
        days_in_month=days_in_month,
        daily=points,
    )
