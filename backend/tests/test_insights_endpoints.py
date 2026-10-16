"""``/admin/insights/*`` endpoints (planchanges4 B6.3).

Validation, injection and RBAC tests need no database. The rest run against
the 49-conversation reference fixture (Postgres, in CI).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from sqlalchemy import text

from app.auth.deps import authenticate_session
from app.auth.permissions import Role, UserStatus
from app.auth.principal import AuthUser
from app.core.config import get_settings
from app.core.database import read_only_session
from app.insights import analytics_service
from main import app
from tests.insights_fixture import (
    FIXTURE_TIMEZONE,
    fixture_id,
    person_ip,
    session_id,
)

BASE = "/admin/insights"
READ_PATHS = (
    "summary",
    "trend",
    "hourly",
    "flow",
    "topics",
    "quality",
    "dropoff",
    "people",
    "conversations",
    f"conversations/{uuid.UUID(int=7)}",
    "pending",
    "taxonomy",
)
FILTERED_PATHS = READ_PATHS[:9]


@pytest.fixture
async def api() -> AsyncIterator[httpx.AsyncClient]:
    """Client without a database: every request here must fail validation or
    authorization before the handler touches the session."""

    async def _no_db() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[read_only_session] = _no_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-API-Key": get_settings().api_key_name or "ci-test-key"},
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(read_only_session, None)


def _as(role: Role | None, status: UserStatus = UserStatus.ACTIVE) -> Iterator[None]:
    async def _user() -> AuthUser:
        return AuthUser(
            id=uuid.uuid4(),
            email=f"{role or 'pending'}@example.com",
            name="Insights",
            role=role,
            status=status,
            version=1,
        )

    previous = app.dependency_overrides.get(authenticate_session)
    app.dependency_overrides[authenticate_session] = _user
    try:
        yield
    finally:
        if previous is None:
            app.dependency_overrides.pop(authenticate_session, None)
        else:
            app.dependency_overrides[authenticate_session] = previous


@pytest.fixture
def operator() -> Iterator[None]:
    yield from _as(Role.OPERATOR)


@pytest.fixture
def admin() -> Iterator[None]:
    yield from _as(Role.ADMIN)


@pytest.fixture
def pending_user() -> Iterator[None]:
    yield from _as(None, UserStatus.PENDING)


# ---------------------------------------------------------------------------
# Validation and injection (no database)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"period": "90d"},
        {"period": "custom"},
        {"period": "custom", "from": "2026-06-02", "to": "2026-06-01"},
        {"period": "custom", "from": "2020-01-01", "to": "2026-01-01"},
        {"from": "2026-06-01", "to": "2026-06-02"},
        {"from": "not-a-date", "period": "custom", "to": "2026-06-02"},
        {"outcome": "Served'; DROP TABLE chat_sessions; --"},
        {"rating": "Excellent"},
        {"intent": "SVC' OR '1'='1"},
        {"quality": "ok"},
        {"issue": "x" * 40},
        {"person": "P01"},
        {"person": "zzzzzzzzzzzzzzzz"},
        {"audience": "everyone"},
        {"tz": "../../etc/passwd"},
        {"tz": "UTC; SELECT 1"},
        {"q": "x" * 201},
        {"q": "abc\x00def"},
        {"user_type": "u" * 65},
        {"unknown_but_ignored": "1", "period": "bad"},
    ],
)
async def test_invalid_filters_are_rejected(api, params):
    for path in FILTERED_PATHS:
        response = await api.get(f"{BASE}/{path}", params=params)
        assert response.status_code == 422, (path, params, response.text)
        assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize(
    "params",
    [
        {"sort": "start; DROP TABLE chat_sessions"},
        {"sort": "s.session_id"},
        {"sort": "1"},
        {"dir": "desc; SELECT pg_sleep(5)"},
        {"limit": "101"},
        {"limit": "0"},
        {"offset": "-1"},
        {"offset": "10001"},
    ],
)
async def test_conversation_paging_and_sort_are_allow_listed(api, params):
    response = await api.get(f"{BASE}/conversations", params=params)
    assert response.status_code == 422, response.text


async def test_conversation_id_must_be_a_uuid(api):
    response = await api.get(f"{BASE}/conversations/1%20OR%201=1")
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "intent", "code": "lower", "label": "x"},
        {"kind": "intent", "code": "A' OR 1=1", "label": "x"},
        {"kind": "colour", "code": "NEW", "label": "x"},
        {"kind": "intent", "code": "NEW", "label": ""},
        {"kind": "intent", "code": "NEW", "label": "x", "color": "red"},
        {"kind": "intent", "code": "NEW", "label": "x", "is_system": True},
    ],
)
async def test_taxonomy_create_is_validated(api, payload):
    response = await api.post(f"{BASE}/taxonomy", json=payload)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "payload",
    [{}, {"code": "RENAMED"}, {"kind": "issue"}, {"label": None}, {"color": "#12"}],
)
async def test_taxonomy_update_cannot_change_the_code(api, payload):
    response = await api.patch(f"{BASE}/taxonomy/intent/SVC", json=payload)
    assert response.status_code == 422, response.text


async def test_taxonomy_path_is_validated(api):
    for path in ("taxonomy/colour/SVC", "taxonomy/intent/svc"):
        response = await api.patch(f"{BASE}/{path}", json={"label": "x"})
        assert response.status_code == 422, path
    response = await api.delete(f"{BASE}/taxonomy/intent/SVC")
    assert response.status_code == 405  # entries are archived, never deleted


# ---------------------------------------------------------------------------
# RBAC (no database)
# ---------------------------------------------------------------------------


async def test_pending_user_cannot_read_insights(api, pending_user):
    for path in READ_PATHS:
        response = await api.get(f"{BASE}/{path}")
        assert response.status_code == 403, path
        assert response.json()["detail"] == "Dashboard access requires approval."


@pytest.mark.parametrize("role_fixture", ["operator", "admin"])
async def test_taxonomy_writes_need_superadmin(api, request, role_fixture):
    request.getfixturevalue(role_fixture)
    calls = [
        ("POST", f"{BASE}/taxonomy", {"kind": "intent", "code": "NEW", "label": "x"}),
        ("PATCH", f"{BASE}/taxonomy/intent/SVC", {"label": "x"}),
        ("POST", f"{BASE}/taxonomy/intent/SVC/archive", None),
    ]
    for method, url, body in calls:
        response = await api.request(method, url, json=body)
        assert response.status_code == 403, (method, url)
        assert response.json()["detail"] == "Insufficient permissions."


async def test_no_service_key_is_401(api):
    response = await api.get(f"{BASE}/summary", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Endpoints on the reference fixture (Postgres)
# ---------------------------------------------------------------------------


async def _get(client: httpx.AsyncClient, path: str, **params: str) -> dict:
    response = await client.get(
        f"{BASE}/{path}", params={"tz": FIXTURE_TIMEZONE, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_operator_can_read_every_endpoint(insights_client, operator):
    for path in READ_PATHS[:-3]:
        await _get(insights_client, path)
    await _get(insights_client, "pending")
    await _get(insights_client, "taxonomy")


async def test_summary_kpis_and_etag(insights_client):
    response = await insights_client.get(f"{BASE}/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["conversations"] == 49
    assert body["substantive_questions"] == 106
    assert body["handled_questions"] == 51
    assert body["active_conversations"] == 47
    assert body["served_conversations"] == 11
    assert body["people"] == 46
    assert body["pending_classification"] == 0
    assert body["range"] == {"from": None, "to": None}
    etag = response.headers["etag"]
    again = await insights_client.get(
        f"{BASE}/summary", headers={"If-None-Match": etag}
    )
    assert again.status_code == 304


async def test_trend_hourly_and_time_zone(insights_client):
    trend = await _get(insights_client, "trend")
    assert trend["range"] == {"from": "2026-05-15", "to": "2026-10-05"}
    assert sum(d["questions"] for d in trend["days"]) == 106
    assert sum(d["conversations"] for d in trend["days"]) == 49
    sydney = await _get(insights_client, "hourly")
    utc = await _get(insights_client, "hourly", tz="UTC")
    assert sydney["total"] == utc["total"] == 106
    assert sydney["hours"] != utc["hours"]
    custom = await _get(
        insights_client,
        "trend",
        period="custom",
        **{"from": "2026-06-01", "to": "2026-06-07"},
    )
    assert custom["days"][0]["day"] == "2026-06-01"
    assert len(custom["days"]) == 7


async def test_flow_topics_quality_dropoff_people(insights_client):
    flow = await _get(insights_client, "flow")
    assert flow["total"] == 47
    assert all(link["outcome"] != "Greeting only" for link in flow["links"])
    topics = await _get(insights_client, "topics")
    assert topics["items"][0]["intent"] == "SVC"
    assert topics["total"] == 106
    quality = await _get(insights_client, "quality")
    assert quality["total"] == 106
    assert quality["items"][0]["quality"] == "OK"
    assert quality["items"][0]["color"] == "#008080"
    dropoff = await _get(insights_client, "dropoff")
    assert dropoff["items"][0] == {
        "issue": "JOBS",
        "label": "No live job openings",
        "conversations": 13,
    }
    people = await _get(insights_client, "people")
    assert people["total"] == 46
    assert people["items"][0]["person"] == "P01"
    assert sum(p["conversations"] for p in people["items"]) == 49


async def test_conversations_paging_and_sorting(insights_client):
    first = await _get(insights_client, "conversations")
    assert first["total"] == 49
    assert len(first["items"]) == 25
    second = await _get(insights_client, "conversations", offset="25")
    assert len(second["items"]) == 24
    ids = {r["conv_id"] for r in first["items"]} | {
        r["conv_id"] for r in second["items"]
    }
    assert len(ids) == 49
    starts = [r["start"] for r in first["items"]]
    assert starts == sorted(starts, reverse=True)

    by_exchanges = await _get(
        insights_client, "conversations", sort="exchanges", dir="desc"
    )
    counts = [r["exchanges"] for r in by_exchanges["items"]]
    assert counts == sorted(counts, reverse=True)
    by_person = await _get(insights_client, "conversations", sort="person", dir="asc")
    assert by_person["items"][0]["person"] == "P01"
    past_end = await _get(insights_client, "conversations", offset="9000")
    assert past_end["items"] == []
    assert past_end["total"] == 49


async def test_search_is_literal_and_injection_safe(insights_client):
    hits = await _get(insights_client, "conversations", q="cloud")
    assert hits["total"] > 0
    for q in ("%", "_", "%' OR 1=1 --", "\\", "'; DROP TABLE chat_sessions; --"):
        response = await _get(insights_client, "conversations", q=q)
        assert response["total"] == 0, q
    still_there = await _get(insights_client, "summary")
    assert still_there["conversations"] == 49


async def test_conversation_detail_with_turns(insights_client, insights_reference_db):
    sid = session_id("C01")
    detail = await _get(insights_client, f"conversations/{sid}")
    assert detail["conversation"]["conv_id"] == str(sid)
    assert detail["conversation"]["outcome"] == "Partly served"
    assert len(detail["turns"]) == 5
    first = detail["turns"][0]
    assert first["intent"] == "GRE"
    assert first["substantive"] is False
    assert first["quality_label"] == "Closing"
    assert first["pending"] is False

    # An exchange without labels yet shows as pending (and is counted).
    db = insights_reference_db
    for role, mid in (("user", uuid.uuid4()), ("assistant", fixture_id("x", "late"))):
        await db.execute(
            text(
                "INSERT INTO chat_messages (id, session_id, turn_index, role, "
                "content, created_at) VALUES (:id, :s, 99, :r, 'late question', "
                "now() AT TIME ZONE 'UTC')"
            ),
            {"id": mid, "s": sid, "r": role},
        )
    detail = await _get(insights_client, f"conversations/{sid}")
    assert detail["turns"][-1]["pending"] is True
    assert detail["turns"][-1]["intent"] is None
    pending = await _get(insights_client, "pending")
    assert pending["pending"] == 1
    assert pending["labelled"] == 117
    assert pending["progress_pct"] == pytest.approx(100 * 117 / 118, abs=0.01)

    missing = await insights_client.get(f"{BASE}/conversations/{uuid.uuid4()}")
    assert missing.status_code == 404


async def test_audience_scoping(insights_client, monkeypatch):
    test_ip = person_ip("P01")

    async def _classified(_db):
        return {test_ip}, set(), None

    monkeypatch.setattr(analytics_service, "get_classified_ips", _classified)
    testing = await _get(insights_client, "conversations", audience="testing")
    users = await _get(insights_client, "conversations", audience="users")
    overview = await _get(insights_client, "conversations", audience="overview")
    assert {r["person"] for r in testing["items"]} == {"P01"}
    assert testing["total"] + users["total"] == overview["total"] == 49
    summary = await _get(insights_client, "summary", audience="users")
    assert summary["conversations"] == users["total"]
    assert summary["source"] == "live"
    detail = await insights_client.get(
        f"{BASE}/conversations/{session_id('C01')}", params={"audience": "users"}
    )
    assert detail.status_code == 404


async def test_responses_expose_no_emails_or_ips(insights_client):
    for path in ("conversations", "people", f"conversations/{session_id('C03')}"):
        body = (await insights_client.get(f"{BASE}/{path}")).text
        assert "insights-fixture.invalid" not in body
        assert "203.0.113." not in body


# ---------------------------------------------------------------------------
# Taxonomy CRUD (Postgres)
# ---------------------------------------------------------------------------


async def _audit_actions(db, code: str) -> list[str]:
    rows = await db.execute(
        text(
            "SELECT action FROM audit_log WHERE action LIKE 'insights_taxonomy_%' "
            "AND detail->>'code' = :code ORDER BY created_at, id"
        ),
        {"code": code},
    )
    return [r.action for r in rows]


async def test_taxonomy_crud_is_audited(insights_client, insights_reference_db):
    created = await insights_client.post(
        f"{BASE}/taxonomy",
        json={
            "kind": "issue",
            "code": "SLOW_REPLY",
            "label": "Reply too slow",
            "color": "#123456",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["is_active"] is True
    assert created.json()["is_system"] is False

    duplicate = await insights_client.post(
        f"{BASE}/taxonomy",
        json={"kind": "issue", "code": "SLOW_REPLY", "label": "Again"},
    )
    assert duplicate.status_code == 409

    patched = await insights_client.patch(
        f"{BASE}/taxonomy/issue/SLOW_REPLY", json={"label": "Slow reply"}
    )
    assert patched.status_code == 200
    assert patched.json()["code"] == "SLOW_REPLY"
    assert patched.json()["label"] == "Slow reply"

    archived = await insights_client.post(f"{BASE}/taxonomy/issue/SLOW_REPLY/archive")
    assert archived.status_code == 200
    assert archived.json()["is_active"] is False

    listed = await _get(insights_client, "taxonomy", kind="issue")
    assert "SLOW_REPLY" not in {i["code"] for i in listed["items"]}
    everything = await _get(
        insights_client, "taxonomy", kind="issue", include_archived="true"
    )
    assert "SLOW_REPLY" in {i["code"] for i in everything["items"]}

    assert await _audit_actions(insights_reference_db, "SLOW_REPLY") == [
        "insights_taxonomy_created",
        "insights_taxonomy_updated",
        "insights_taxonomy_archived",
    ]

    missing = await insights_client.patch(
        f"{BASE}/taxonomy/issue/NOPE", json={"label": "x"}
    )
    assert missing.status_code == 404


async def test_rename_keeps_existing_labels(insights_client):
    before = await _get(insights_client, "topics")
    renamed = await insights_client.patch(
        f"{BASE}/taxonomy/intent/SVC", json={"label": "Our services"}
    )
    assert renamed.status_code == 200
    after = await _get(insights_client, "topics")
    svc_before = next(i for i in before["items"] if i["intent"] == "SVC")
    svc_after = next(i for i in after["items"] if i["intent"] == "SVC")
    assert svc_after["label"] == "Our services"
    assert svc_after["questions"] == svc_before["questions"] == 33

    archived = await insights_client.post(f"{BASE}/taxonomy/intent/CAR/archive")
    assert archived.status_code == 200
    detail = await _get(insights_client, f"conversations/{session_id('C03')}")
    assert "Careers" in {t["intent_label"] for t in detail["turns"]}
    still = await _get(insights_client, "topics")
    assert {i["intent"] for i in still["items"]} == {
        i["intent"] for i in before["items"]
    }


async def test_rule_codes_cannot_be_archived(insights_client):
    for kind, code in (("intent", "GRE"), ("bot_quality", "OK"), ("issue", "OTHER")):
        response = await insights_client.post(f"{BASE}/taxonomy/{kind}/{code}/archive")
        assert response.status_code == 409, (kind, code)
