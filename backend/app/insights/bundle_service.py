"""``GET /admin/insights/bundle`` and the person detail, answered from memory.

The facts (``app.insights.engine``) answer any filter set in milliseconds;
each response is encoded once per facts version and parameters and carries a
weak ETag derived from them, so a revalidation is a 304 without any work.

Right after a restart the facts are not in memory yet: the registered default
views (the page's first view per preset and audience) are then served from
their stored snapshot while the facts load in the background.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

from fastapi import Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import release_connection
from app.core.http_cache import encoded_response
from app.dashboard.snapshots import (
    Gather,
    canonical,
    register,
    register_maintenance,
    snapshot_response,
)
from app.governance.audience import get_classified_ips
from app.insights import engine
from app.insights.analytics_repository import InsightsAnalyticsRepository
from app.insights.analytics_service import ANALYTICS_PAGE_TZ, INSIGHTS_KIND_PREFIX
from app.schemas.insights import (
    Audience,
    BundlePart,
    ConversationPage,
    InsightsFilters,
    Period,
)

BUNDLE_KIND = f"{INSIGHTS_KIND_PREFIX}bundle"
# The page's first view: its default preset first, every audience.
DEFAULT_PERIODS = (Period.LAST_7_DAYS, Period.LAST_30_DAYS)
MS = 1000


@dataclass(frozen=True, slots=True)
class BundleRequest:
    filters: InsightsFilters
    page: ConversationPage
    parts: tuple[str, ...]

    def params(self) -> dict[str, Any]:
        return {
            "filters": self.filters.model_dump(mode="json"),
            "page": self.page.model_dump(mode="json"),
            "parts": list(self.parts),
        }


def normalise_parts(parts: list[BundlePart] | None) -> tuple[str, ...]:
    """Requested parts in canonical order (all when none are given)."""
    wanted = {p.value for p in parts} if parts else set(engine.PARTS)
    return tuple(p for p in engine.PARTS if p in wanted)


class InsightsBundleService:
    def __init__(self, db: AsyncSession, gather: Gather) -> None:
        self.db = db
        self.gather = gather

    async def _test_ips(self, audience: Audience) -> tuple[str, ...]:
        if audience is Audience.OVERVIEW:
            return ()
        ips, _users, _as_of = await get_classified_ips(self.db)
        return tuple(sorted(ips))

    async def _today(self, tz: str) -> date:
        today = engine.today_in(tz)
        if today is None:
            (today,) = await self.gather(
                lambda s: InsightsAnalyticsRepository(s).today(tz)
            )
        return today

    async def bundle(self, request: Request, req: BundleRequest) -> Response:
        filters = req.filters
        test_ips = await self._test_ips(filters.audience)
        # Everything below is memory (or its own sessions): give the
        # request's connection back now.
        await release_connection(self.db)
        if engine.peek_facts(filters.tz) is None and _cold_snapshot(req):
            engine.schedule_reload(filters.tz)
            return await snapshot_response(
                request, BUNDLE_KIND, req.params(), self.gather
            )
        started = time.perf_counter()
        facts = await engine.get_facts(filters.tz, self.gather)
        today = await self._today(filters.tz)
        key = canonical(req.params())

        def recipe(f: engine.Facts) -> tuple[str, Any]:
            day = engine.today_in(f.tz) or today
            query = engine.build_query(f, filters, day, test_ips)
            etag = engine.etag_for(f, "bundle", key, day.isoformat(), query.test_ips)
            return etag, lambda: engine.bundle(f, query, req.page, req.parts)

        etag, build = recipe(facts)
        body = await engine.encoded_async(etag, build)
        engine.remember(filters.tz, key, recipe)
        return _response(request, body, etag, facts, started)

    async def person(
        self, request: Request, person_id: str, filters: InsightsFilters
    ) -> Response | None:
        """The person detail (only ``audience`` and ``tz`` apply), or None."""
        test_ips = await self._test_ips(filters.audience)
        await release_connection(self.db)
        started = time.perf_counter()
        facts = await engine.get_facts(filters.tz, self.gather)
        etag = engine.etag_for(
            facts, "person", person_id, filters.audience.value, test_ips
        )
        detail = engine.person_detail(facts, person_id, filters.audience, test_ips)
        if detail is None:
            return None
        body = await engine.encoded_async(etag, lambda: detail)
        return _response(request, body, etag, facts, started)


def _response(
    request: Request, body: bytes, etag: str, facts: engine.Facts, started: float
) -> Response:
    elapsed = (time.perf_counter() - started) * MS
    return encoded_response(
        request,
        body,
        etag,
        {
            "X-Data-As-Of": facts.loaded_at.isoformat(),
            "Server-Timing": f"engine;dur={elapsed:.1f}",
        },
    )


def _cold_snapshot(req: BundleRequest) -> bool:
    """A default view while the facts are not in memory (stored snapshots on)."""
    if not get_settings().dashboard_snapshots_persist:
        return False
    return canonical(req.params()) in _default_keys()


def _default_params() -> list[dict[str, Any]]:
    out = []
    for audience in Audience:
        for period in DEFAULT_PERIODS:
            filters = InsightsFilters(
                period=period, audience=audience, tz=ANALYTICS_PAGE_TZ
            )
            req = BundleRequest(filters, ConversationPage(), engine.PARTS)
            out.append(req.params())
    return out


def _default_keys() -> set[str]:
    return {canonical(p) for p in _default_params()}


async def _bundle_snapshot(gather: Gather, params: dict[str, Any]) -> dict[str, Any]:
    """Snapshot builder: the bundle from the facts (loaded here if needed)."""
    filters = InsightsFilters.model_validate(params["filters"])
    page = ConversationPage.model_validate(params.get("page", {}))
    parts = tuple(params.get("parts") or engine.PARTS)

    async def test_ips(session: AsyncSession) -> tuple[str, ...]:
        if filters.audience is Audience.OVERVIEW:
            return ()
        ips, _users, _as_of = await get_classified_ips(session)
        return tuple(sorted(ips))

    (ips,) = await gather(test_ips)
    # A data change just triggered a reload: store the new numbers.
    await engine.settled(filters.tz)
    facts = await engine.get_facts(filters.tz, gather)
    today = engine.today_in(filters.tz)
    if today is None:
        (today,) = await gather(
            lambda s: InsightsAnalyticsRepository(s).today(filters.tz)
        )
    query = engine.build_query(facts, filters, today, ips)
    return await asyncio.to_thread(engine.bundle, facts, query, page, parts)


async def _warm_facts(_session: AsyncSession) -> None:
    """Refresher upkeep: the page's zone is in memory from startup on."""
    if engine.peek_facts(ANALYTICS_PAGE_TZ) is None:
        await engine.reload_facts(ANALYTICS_PAGE_TZ)


register(BUNDLE_KIND, _bundle_snapshot, _default_params)
register_maintenance("insights.facts", _warm_facts)
