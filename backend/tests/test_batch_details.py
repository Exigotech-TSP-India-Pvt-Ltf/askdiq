"""Batched detail reads (``GET .../batch?ids=``).

Each batch item must equal the single-item response, unknown ids are listed
in ``missing``, the id list is bounded, and every batch route is declared
before its ``/{id}`` sibling (else ``batch`` would be read as an id).
Endpoint tests seed far-past rows (real Postgres, run in CI).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.api import batch
from app.api.routes import metrics
from app.entities import conversations as conversation_stats
from main import app
from tests.dashboard_helpers import exchange, session_at, unique_day, unique_ip

BATCH_ROUTES = {
    "/admin/conversations/batch": "/admin/conversations/{conversation_id}",
    "/admin/usage/queries/batch": "/admin/usage/queries/{request_id}",
    "/admin/governance/traces/batch": "/admin/governance/traces/{request_id}",
    "/metrics/evaluation/live/cases/batch": "/metrics/evaluation/live/cases/{case_id}",
}

# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------


def test_batch_ids_dedupes_in_order_and_drops_blanks() -> None:
    assert batch.batch_ids(["b", "a", "b", " ", "c "]) == ["b", "a", "c"]


@pytest.mark.parametrize(
    "ids",
    [
        [],
        [""],
        [str(i) for i in range(batch.BATCH_MAX_IDS + 1)],
        ["x" * (batch.BATCH_ID_MAX_LENGTH + 1)],
    ],
)
def test_batch_ids_out_of_bounds_is_422(ids: list[str]) -> None:
    with pytest.raises(HTTPException) as exc:
        batch.batch_ids(ids)
    assert exc.value.status_code == 422


async def test_collect_marks_none_and_404_missing_and_reraises_others() -> None:
    async def load(item_id: str) -> dict | None:
        if item_id == "gone":
            raise HTTPException(404)
        if item_id == "boom":
            raise HTTPException(500)
        return None if item_id == "none" else {"id": item_id}

    result = await batch.collect(["a", "gone", "none"], load)
    assert result.items == {"a": {"id": "a"}}
    assert result.missing == ["gone", "none"]
    with pytest.raises(HTTPException):
        await batch.collect(["boom"], load)


def test_batch_routes_precede_their_item_routes() -> None:
    order = [
        r.path for r in app.routes if isinstance(r, APIRoute) and "GET" in r.methods
    ]
    for batch_path, item_path in BATCH_ROUTES.items():
        assert order.index(batch_path) < order.index(item_path), batch_path


def test_batch_routes_are_documented() -> None:
    spec = app.openapi()
    for path in BATCH_ROUTES:
        op = spec["paths"][path]["get"]
        assert op["summary"], path
        assert op["description"], path
        params = {p["name"]: p for p in op["parameters"]}
        assert params["ids"]["required"] is True
        assert params["ids"]["schema"]["type"] == "array"
        schema = op["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith("/BatchDetails"), path
        assert {"401", "403", "422"} <= set(op["responses"]), path


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


async def _two_turns(db) -> tuple:
    d0 = unique_day()
    ip = unique_ip()
    first = await session_at(db, d0, ip, title="batch one")
    _, a1 = await exchange(db, first, 0, d0, latency_ms=900.0)
    second = await session_at(db, d0 + timedelta(hours=1), ip, title="batch two")
    _, a2 = await exchange(db, second, 0, d0 + timedelta(hours=1), latency_ms=1200.0)
    return first, second, a1, a2


async def test_conversations_batch_equals_single(client, db_session) -> None:
    first, second, _, _ = await _two_turns(db_session)
    unknown = str(uuid.uuid4())
    ids = [str(first.id), unknown, str(second.id), str(first.id)]
    resp = await client.get("/admin/conversations/batch", params={"ids": ids})
    assert resp.status_code == 200
    assert resp.headers["etag"]
    body = resp.json()
    assert list(body["items"]) == [str(first.id), str(second.id)]
    assert body["missing"] == [unknown]
    for cid in (first.id, second.id):
        single = await client.get(f"/admin/conversations/{cid}")
        assert body["items"][str(cid)] == single.json()

    again = await client.get(
        "/admin/conversations/batch",
        params={"ids": ids},
        headers={"If-None-Match": resp.headers["etag"]},
    )
    assert again.status_code == 304


def test_cap_preview_content_cuts_and_flags_only_long_messages() -> None:
    detail = {
        "conversation": {"id": "c"},
        "messages": [
            {"id": "u", "role": "user", "content": "short"},
            {"id": "a", "role": "assistant", "content": "y" * 12},
        ],
    }
    capped = conversation_stats.cap_preview_content(detail, max_chars=10)
    short, long_ = capped["messages"]
    assert short == detail["messages"][0]
    assert long_["content"] == "y" * 10
    assert long_["content_truncated"] is True
    assert long_["content_length"] == 12
    # The stored snapshot is never mutated; small payloads pass through as is.
    assert detail["messages"][1]["content"] == "y" * 12
    assert conversation_stats.cap_preview_content(detail, max_chars=20) is detail


async def test_conversations_batch_caps_long_messages(client, db_session) -> None:
    day = unique_day()
    session = await session_at(db_session, day, unique_ip(), title="long")
    cap = conversation_stats.PREVIEW_CONTENT_MAX_CHARS
    await exchange(db_session, session, 0, day, question="q" * (cap + 50))
    resp = await client.get(
        "/admin/conversations/batch", params={"ids": [str(session.id)]}
    )
    assert resp.status_code == 200
    item = resp.json()["items"][str(session.id)]
    user = next(m for m in item["messages"] if m["role"] == "user")
    assert len(user["content"]) == cap
    assert user["content_truncated"] is True
    assert user["content_length"] == cap + 50
    single = await client.get(f"/admin/conversations/{session.id}")
    full = next(m for m in single.json()["messages"] if m["role"] == "user")
    assert len(full["content"]) == cap + 50
    assert "content_truncated" not in full


async def test_usage_queries_batch_equals_single(client, db_session) -> None:
    _, _, a1, a2 = await _two_turns(db_session)
    ids = [a2.request_id, "unknown-request", a1.request_id]
    resp = await client.get("/admin/usage/queries/batch", params={"ids": ids})
    assert resp.status_code == 200
    body = resp.json()
    assert list(body["items"]) == [a2.request_id, a1.request_id]
    assert body["missing"] == ["unknown-request"]
    for rid in (a1.request_id, a2.request_id):
        single = await client.get(f"/admin/usage/queries/{rid}")
        assert body["items"][rid] == single.json()


async def test_traces_batch_equals_single_and_skips_invalid_ids(
    client, db_session
) -> None:
    _, _, a1, a2 = await _two_turns(db_session)
    ids = [a1.request_id, "bad id!", a2.request_id]
    params = {"ids": ids, "audience": "overview"}
    resp = await client.get("/admin/governance/traces/batch", params=params)
    assert resp.status_code == 200
    body = resp.json()
    assert body["missing"] == ["bad id!"]
    for rid in (a1.request_id, a2.request_id):
        single = await client.get(
            f"/admin/governance/traces/{rid}", params={"audience": "overview"}
        )
        assert body["items"][rid] == single.json()


async def test_batch_without_ids_is_422(client) -> None:
    for path in BATCH_ROUTES:
        assert (await client.get(path)).status_code == 422, path
    too_many = [f"id-{i}" for i in range(batch.BATCH_MAX_IDS + 1)]
    resp = await client.get("/admin/usage/queries/batch", params={"ids": too_many})
    assert resp.status_code == 422


async def test_evaluation_cases_batch_equals_single(client, monkeypatch) -> None:
    report = {
        "cases": [
            {"id": "case-a", "query": "a", "retrieved_chunks": [{"chunk_id": "c"}]},
            {"id": "case-b", "query": "b", "retrieved_chunks": [{"chunk_id": "d"}]},
        ]
    }

    async def fake_snapshot(*_args, **_kwargs):
        return report

    monkeypatch.setattr(metrics, "get_snapshot", fake_snapshot)
    monkeypatch.setattr(
        metrics.eval_views,
        "find_case",
        lambda payload, cid: next(
            (c for c in payload["cases"] if c["id"] == cid), None
        ),
    )
    resp = await client.get(
        "/metrics/evaluation/live/cases/batch",
        params={"ids": ["case-b", "nope", "case-a"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert list(body["items"]) == ["case-b", "case-a"]
    assert body["missing"] == ["nope"]
    single = await client.get("/metrics/evaluation/live/cases/case-a")
    assert body["items"]["case-a"] == single.json()
