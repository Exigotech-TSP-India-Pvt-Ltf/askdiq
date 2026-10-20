"""Instant reads and real-time updates: snapshot responses (ETag before any
encoding, as-of header), cross-process memory refresh, the data watcher and
the console live stream (GET /admin/live)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from starlette.requests import Request

from app.core.cache import dashboard_cache
from app.core.config import get_settings
from app.core.events import TOPIC_CONSOLE_CHANGED, TOPIC_SNAPSHOT_UPDATED, Event
from app.dashboard import live, snapshots
from app.dashboard.range_params import day_params, params_days, preset_day_ranges

KIND = "test.live"


def _request(if_none_match: str | None = None) -> Request:
    headers = [(b"if-none-match", if_none_match.encode())] if if_none_match else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict]]:
    monkeypatch.setattr(get_settings(), "dashboard_snapshots_persist", False)
    seen: list[dict] = []

    async def build(_gather, params):
        seen.append(params)
        return {"n": len(seen), **params}

    snapshots.register(KIND, build, [])
    yield seen
    snapshots._KINDS.pop(KIND, None)  # noqa: SLF001


async def _no_gather(*_factories):  # the builder above needs no session
    return []


async def test_snapshot_response_etag_matches_without_rebuilding(builds):
    first = await snapshots.snapshot_response(_request(), KIND, {"a": 1}, _no_gather)
    assert first.status_code == 200
    etag = first.headers["etag"]
    assert first.headers["cache-control"].startswith("private")
    assert first.body == b'{"n":1,"a":1}'
    assert first.headers["x-data-as-of"]

    again = await snapshots.snapshot_response(
        _request(etag), KIND, {"a": 1}, _no_gather
    )
    assert again.status_code == 304
    assert again.headers["etag"] == etag
    assert len(builds) == 1  # served from memory, encoded once


async def test_snapshot_response_body_equals_the_payload(builds):
    payload = await snapshots.get_snapshot(KIND, {"b": 2}, _no_gather)
    resp = await snapshots.snapshot_response(_request(), KIND, {"b": 2}, _no_gather)
    assert json.loads(resp.body) == payload


async def test_compute_and_store_announces_kind(monkeypatch, builds):
    published: list[tuple[str, str]] = []

    async def fake_publish(topic: str, item_id: str) -> None:
        published.append((topic, str(item_id)))

    monkeypatch.setattr(snapshots, "publish_event", fake_publish)

    async def gather(*factories):
        return [None for _ in factories]

    await snapshots.compute_and_store(KIND, {"c": 3}, gather)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    key = snapshots.snapshot_key(KIND, {"c": 3})
    assert (TOPIC_SNAPSHOT_UPDATED, f"{KIND}|{key}") in published
    assert (TOPIC_CONSOLE_CHANGED, KIND) in published
    assert key in snapshots._as_of  # noqa: SLF001


async def test_snapshot_updated_reloads_only_served_keys(monkeypatch):
    reloaded: list[str] = []

    async def fake_reload(key: str) -> None:
        reloaded.append(key)

    monkeypatch.setattr(snapshots, "_reload", fake_reload)
    dashboard_cache.put(snapshots.CACHE_PREFIX + "served", {"x": 1})
    snapshots.on_snapshot_updated(Event(TOPIC_SNAPSHOT_UPDATED, "k|served", "other"))
    snapshots.on_snapshot_updated(Event(TOPIC_SNAPSHOT_UPDATED, "k|unseen", "other"))
    # Own events are ignored (this process already holds the new payload).
    snapshots.on_snapshot_updated(
        Event(TOPIC_SNAPSHOT_UPDATED, "k|served", "me"), own_instance="me"
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert reloaded == ["served"]


async def test_watch_once_announces_changes_after_the_first_check(monkeypatch):
    published: list[str] = []

    async def fake_publish(topic: str, item_id: str) -> None:
        assert topic == TOPIC_CONSOLE_CHANGED
        published.append(item_id)

    monkeypatch.setattr(snapshots, "publish_event", fake_publish)
    stamps = iter(
        [
            {"data": "1", "alerts": "a"},
            {"data": "1", "alerts": "a"},
            {"data": "2", "alerts": "a"},
            {"data": "2", "alerts": "b"},
        ]
    )

    async def gather(factory):
        return [next(stamps)]

    watcher = snapshots.SnapshotRefresher(gather=gather)
    watcher._watched = {}  # noqa: SLF001

    async def run_stamps():
        return await watcher.watch_once()

    assert await run_stamps() is False  # first check only records
    assert await run_stamps() is False
    assert await run_stamps() is True
    assert await run_stamps() is False  # alerts changed, data did not
    assert published == ["data", "alerts"]


class _Bus:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Event] = asyncio.Queue()
        self.unsubscribed = False

    def subscribe(self, topic: str) -> asyncio.Queue[Event]:
        assert topic == TOPIC_CONSOLE_CHANGED
        return self.queue

    def unsubscribe(self, _topic: str, _queue) -> None:
        self.unsubscribed = True


async def test_console_events_coalesce_a_burst(monkeypatch):
    monkeypatch.setattr(get_settings(), "console_stream_coalesce_seconds", 0.05)
    bus = _Bus()
    stream = live.console_events(bus)  # type: ignore[arg-type]
    ready = await anext(stream)
    assert ready.event == "ready"
    for scope in ("data", "usage.summary", "data", "alerts"):
        bus.queue.put_nowait(Event(TOPIC_CONSOLE_CHANGED, scope, "x"))
    frame = await anext(stream)
    assert frame.event == "changed"
    assert frame.data["scopes"] == ["alerts", "data", "usage.summary"]
    await stream.aclose()
    assert bus.unsubscribed


def test_day_params_round_trip_and_presets() -> None:
    assert day_params(None) == {"from": None, "to": None}
    assert params_days(day_params(None)) is None
    ranges = preset_day_ranges()
    assert ranges[0] is None
    spans = [(r.last - r.first).days + 1 for r in ranges[1:]]
    assert spans == [2, 7, 30, 90]
    today = datetime.now(UTC).date()
    assert all(r.last == today for r in ranges[1:])
    for r in ranges[1:]:
        assert params_days(day_params(r)) == r


async def test_feedback_analytics_snapshot_equals_direct_compute(client, db_session):
    """Parity: the precomputed response is exactly the current formula."""
    from fastapi.encoders import jsonable_encoder

    from app.feedback import analytics
    from app.feedback import repository as repo
    from app.governance.audience import build_audience_filter
    from app.models.db_models import ChatSession
    from tests.conftest import shared_session_factory

    for query in ("audience=overview", "audience=users&from=2026-01-01&to=2026-12-31"):
        dashboard_cache.clear()
        resp = await client.get(f"/admin/feedback/analytics?{query}")
        assert resp.status_code == 200, resp.text
        scope = "users" if "users" in query else "overview"
        window = None
        if "from=" in query:
            days = params_days({"from": "2026-01-01", "to": "2026-12-31"})
            window = (days.start, days.end)
        direct = await analytics.compute_analytics(
            await build_audience_filter(db_session, repo.CLIENT_IP, scope),
            await build_audience_filter(db_session, ChatSession.created_ip, scope),
            shared_session_factory(db_session),
            window=window,
        )
        assert resp.json() == jsonable_encoder(direct)


async def test_live_evaluation_snapshot_equals_direct_compute(client, db_session):
    from fastapi.encoders import jsonable_encoder

    from app.api.routes.metrics import _live_evaluation_payload

    for audience in ("users", "overview"):
        dashboard_cache.clear()
        resp = await client.get(f"/metrics/evaluation/live?audience={audience}")
        assert resp.status_code == 200, resp.text
        direct = await _live_evaluation_payload(db_session, audience, None)
        assert resp.json() == jsonable_encoder(direct)
