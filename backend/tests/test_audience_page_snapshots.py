"""Visitors & Conversations page: precomputed views and in-memory searches."""

from __future__ import annotations

from typing import Any

import pytest

from app.api.routes import admin, admin_entities
from app.core.cache import dashboard_cache
from app.core.config import get_settings
from app.dashboard import snapshots
from app.dashboard.range_params import PRESET_SPANS_DAYS

AUDIENCES = {"overview", "users", "testing"}


def test_every_preset_and_audience_is_precomputed() -> None:
    presets = len(PRESET_SPANS_DAYS)
    visitor_stats = admin_entities._visitor_stats_defaults()  # noqa: SLF001
    conversation_stats = admin_entities._conversation_stats_defaults()  # noqa: SLF001
    conversation_list = admin._conversation_list_defaults()  # noqa: SLF001
    assert len(visitor_stats) == len(conversation_stats) == presets * len(AUDIENCES)
    # The list also keeps its unbounded first page.
    assert len(conversation_list) == (presets + 1) * len(AUDIENCES)
    assert {p["audience"] for p in conversation_stats} == AUDIENCES
    assert all(p["ip"] is None and p["q"] is None for p in conversation_stats)
    assert all(p["range_start"] for p in conversation_list[len(AUDIENCES) :])
    for kind in (
        admin_entities.CONVERSATION_STATS_KIND,
        admin.CONVERSATION_DETAIL_KIND,
        admin_entities.VISITOR_STATS_KIND,
    ):
        assert kind in snapshots.registered_kinds()


async def test_memory_snapshot_caches_in_process_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "dashboard_cache_fresh_seconds", 60.0)
    built: list[int] = []

    async def build() -> dict[str, Any]:
        built.append(1)
        return {"n": len(built)}

    params = {"q": "policy"}
    first = await admin_entities.memory_snapshot("test.search", params, build)
    again = await admin_entities.memory_snapshot("test.search", params, build)
    assert first == again == {"n": 1}
    # Dropping snapshots (e.g. a test-IP change) clears searches too.
    dashboard_cache.invalidate_prefix(snapshots.CACHE_PREFIX)
    assert await admin_entities.memory_snapshot("test.search", params, build) == {
        "n": 2
    }
