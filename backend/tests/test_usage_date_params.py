"""Usage range params with UTC offsets ("Z", "+05:30") never 500.

The columns are naive UTC; an offset-aware bound is converted to UTC and
stripped before it is compared (app.api.routes.admin_usage.parse_usage_bound).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.api.routes.admin_usage import parse_usage_bound

OFFSET_RANGES = (
    ("2026-10-01T00:00:00+05:30", "2026-10-10T12:00:00Z"),
    ("2026-10-01T00:00:00Z", "2026-10-10T00:00:00+05:30"),
    ("2026-10-01", "2026-10-10"),
)


def test_offsets_become_naive_utc() -> None:
    assert parse_usage_bound("2026-10-01T05:30:00+05:30", "from") == datetime(
        2026, 10, 1
    )
    assert parse_usage_bound("2026-10-01T00:00:00Z", "from") == datetime(2026, 10, 1)
    assert parse_usage_bound("2026-10-01", "to", end=True) == datetime(
        2026, 10, 1, 23, 59, 59, 999999
    )


@pytest.mark.parametrize(("start", "end"), OFFSET_RANGES)
@pytest.mark.parametrize(
    "path",
    ["/admin/usage/summary", "/admin/usage/queries", "/admin/usage/cost-breakdown"],
)
async def test_usage_endpoints_accept_offset_ranges(client, path, start, end) -> None:
    response = await client.get(
        path, params={"audience": "overview", "from_date": start, "to_date": end}
    )
    assert response.status_code == 200, response.text
