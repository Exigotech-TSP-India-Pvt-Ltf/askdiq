"""Website (API-key) callers on the chat routes, next to dashboard SSO sessions.

Contract: docs/technical/10-website-integration.md. The public website calls
``/query`` and ``/sessions`` server-to-server with ``X-API-Key`` and an
optional ``X-Client-Id`` (no session); the dashboard proxy sends a session.
The route-level tests stub the session/query services, so they need no
database; the ownership tests at the end use the real database (CI).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.api.routes import query as query_route
from app.auth import deps as auth_deps
from app.auth.deps import api_key_actor, authenticate_session
from app.auth.permissions import Role, UserStatus
from app.auth.principal import AuthUser
from app.core import rate_limit
from app.core import sessions as session_service
from app.core.config import get_settings
from app.core.database import get_db
from app.governance.auth import DEFAULT_ACTOR
from app.schemas.query import QueryResponse
from app.schemas.session import ChatSessionDetail, ChatSessionSummary
from main import app

pytestmark = pytest.mark.real_auth

VISITOR = "3f6c2a9e-8d41-4c7b-9a55-0f1e2d3c4b5a"
OTHER_VISITOR = "9b1d7c3e-2a45-4f6b-8c71-1d2e3f4a5b6c"
VISITOR_IP = "203.0.113.10"
SSO_EMAIL = "alice@example.com"
SID = str(uuid.UUID(int=7))
MID = str(uuid.UUID(int=8))


def website_headers(client_id: str | None = VISITOR) -> dict[str, str]:
    headers = {"X-API-Key": get_settings().api_key_name}
    if client_id is not None:
        headers["X-Client-Id"] = client_id
    return headers


class _FakeDb:
    async def commit(self) -> None:
        return None


class _Calls:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any]]] = []

    def record(self, name: str, **kwargs: Any) -> None:
        self.items.append((name, kwargs))

    def last(self, name: str) -> dict[str, Any]:
        return next(kw for n, kw in reversed(self.items) if n == name)


def _summary(title: str = "Chat") -> ChatSessionSummary:
    now = datetime.now(UTC)
    return ChatSessionSummary(
        id=SID,
        title=title,
        created_at=now,
        last_activity_at=now,
        expires_at=now + timedelta(hours=1),
        is_expired=False,
        message_count=0,
    )


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Calls]:
    """Stub the DB and the session/query services; record who called them."""
    recorded = _Calls()

    async def fake_db() -> AsyncIterator[_FakeDb]:
        yield _FakeDb()

    async def list_sessions(_db: Any, actor: str) -> list[ChatSessionSummary]:
        recorded.record("list", actor=actor)
        return [_summary()]

    async def create_session(
        _db: Any, actor: str, title: str | None, client_ip: str | None = None
    ) -> ChatSessionSummary:
        recorded.record("create", actor=actor, client_ip=client_ip)
        return _summary(title or "New chat")

    async def get_session_detail(
        _db: Any, session_id: str, actor: str
    ) -> ChatSessionDetail:
        recorded.record("get", actor=actor, session_id=session_id)
        return ChatSessionDetail(**_summary().model_dump(), messages=[])

    async def record_feedback(_db: Any, **kwargs: Any) -> dict[str, Any]:
        recorded.record("feedback", **kwargs)
        now = datetime.now(UTC)
        return {
            "message_id": kwargs["message_id"],
            "session_id": kwargs["session_id"],
            "rating": kwargs["rating"],
            "created_at": now,
            "updated_at": now,
        }

    async def resolve_session(
        _db: Any, actor: str, session_id: str | None, client_ip: str | None
    ) -> Any:
        recorded.record("query", actor=actor, session_id=session_id, ip=client_ip)
        return type("S", (), {"id": uuid.UUID(SID)})()

    async def no_history(_db: Any, _session: Any) -> list[Any]:
        return []

    async def answer(**kwargs: Any) -> QueryResponse:
        recorded.record("run_query", filters=kwargs["filters"])
        return QueryResponse(
            answer="ok",
            sources=[],
            grounded=True,
            confidence=1.0,
            request_id="r",
            latency_ms=1.0,
        )

    def write_exchange(**kwargs: Any) -> tuple[str, str, str]:
        recorded.record("write", user_email=kwargs["user_email"])
        return "u1", "a1", "Chat"

    monkeypatch.setattr(session_service, "list_sessions", list_sessions)
    monkeypatch.setattr(session_service, "create_session", create_session)
    monkeypatch.setattr(session_service, "get_session_detail", get_session_detail)
    monkeypatch.setattr(session_service, "record_feedback", record_feedback)
    monkeypatch.setattr(query_route, "resolve_session_for_query", resolve_session)
    monkeypatch.setattr(query_route, "load_recent_history", no_history)
    monkeypatch.setattr(query_route, "run_query", answer)
    monkeypatch.setattr(query_route, "VectorStore", lambda _db: None)
    monkeypatch.setattr(query_route, "schedule_exchange_write", write_exchange)
    monkeypatch.setattr(
        query_route, "schedule_online_evaluation", lambda **_kwargs: None
    )
    rate_limit._user_request_log.clear()
    rate_limit._ip_request_log.clear()
    rate_limit._feedback_user_log.clear()
    rate_limit._feedback_ip_log.clear()
    app.dependency_overrides[get_db] = fake_db
    yield recorded
    app.dependency_overrides.pop(get_db, None)
    rate_limit._user_request_log.clear()
    rate_limit._ip_request_log.clear()


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def _sso_user(status: UserStatus = UserStatus.ACTIVE) -> AuthUser:
    return AuthUser(
        id=uuid.uuid4(),
        email=SSO_EMAIL,
        name="Alice",
        role=Role.OPERATOR,
        status=status,
        version=1,
    )


# ---------------------------------------------------------------------------
# Actor derivation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("client_id", "expected"),
    [
        (VISITOR, VISITOR),
        ("visitor_42.web", "visitor_42.web"),
        (None, DEFAULT_ACTOR),
        ("", DEFAULT_ACTOR),
        ("bad id with spaces", DEFAULT_ACTOR),
        ("x" * 65, DEFAULT_ACTOR),
        (SSO_EMAIL, DEFAULT_ACTOR),  # never collides with an SSO actor
        (DEFAULT_ACTOR, DEFAULT_ACTOR),
    ],
)
def test_api_key_actor(client_id: str | None, expected: str) -> None:
    assert api_key_actor(client_id) == expected


# ---------------------------------------------------------------------------
# API-key happy path
# ---------------------------------------------------------------------------


async def test_query_with_api_key_client_id_and_ip(
    http: httpx.AsyncClient, calls: _Calls
) -> None:
    response = await http.post(
        "/query",
        headers=website_headers(),
        json={"query": "What is the Trust Center?", "ip_address": VISITOR_IP},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"] == SID
    assert body["ip_address"] == VISITOR_IP
    assert calls.last("query") == {
        "actor": VISITOR,
        "session_id": None,
        "ip": VISITOR_IP,
    }
    assert calls.last("write") == {"user_email": VISITOR}


async def test_query_keeps_allow_listed_filters_and_session_id(
    http: httpx.AsyncClient, calls: _Calls
) -> None:
    response = await http.post(
        "/query",
        headers=website_headers(),
        json={
            "query": "q",
            "session_id": SID,
            "ip_address": VISITOR_IP,
            "filters": {"corpus": "public"},
        },
    )
    assert response.status_code == 200, response.text
    assert calls.last("query")["session_id"] == SID
    assert calls.last("run_query") == {"filters": {"corpus": "public"}}


async def test_session_routes_with_api_key(
    http: httpx.AsyncClient, calls: _Calls
) -> None:
    headers = website_headers()
    created = await http.post(
        "/sessions", headers=headers, json={"title": "Hi", "ip_address": VISITOR_IP}
    )
    assert created.status_code == 201, created.text
    assert calls.last("create") == {"actor": VISITOR, "client_ip": VISITOR_IP}

    listed = await http.get("/sessions", headers=headers)
    assert listed.status_code == 200
    assert calls.last("list") == {"actor": VISITOR}

    detail = await http.get(f"/sessions/{SID}", headers=headers)
    assert detail.status_code == 200
    assert calls.last("get")["actor"] == VISITOR


async def test_feedback_with_api_key_and_forwarded_ip(
    http: httpx.AsyncClient, calls: _Calls
) -> None:
    response = await http.post(
        f"/sessions/{SID}/messages/{MID}/feedback",
        headers=website_headers(),
        json={"rating": 5, "ip_address": VISITOR_IP},
    )
    assert response.status_code == 200, response.text
    recorded = calls.last("feedback")
    assert recorded["actor"] == VISITOR
    assert recorded["client_ip"] == VISITOR_IP


async def test_missing_client_id_uses_the_shared_default_actor(
    http: httpx.AsyncClient, calls: _Calls
) -> None:
    response = await http.get("/sessions", headers=website_headers(client_id=None))
    assert response.status_code == 200
    assert calls.last("list") == {"actor": DEFAULT_ACTOR}


async def test_email_shaped_client_id_cannot_act_as_an_sso_user(
    http: httpx.AsyncClient, calls: _Calls
) -> None:
    response = await http.get("/sessions", headers=website_headers(SSO_EMAIL))
    assert response.status_code == 200
    assert calls.last("list") == {"actor": DEFAULT_ACTOR}


# ---------------------------------------------------------------------------
# Rate limits and capacity on the API-key path
# ---------------------------------------------------------------------------


async def test_per_actor_limit_keys_on_client_id(
    http: httpx.AsyncClient, calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rate_limit.settings, "rate_limit_per_minute", 1)
    monkeypatch.setattr(rate_limit.settings, "rate_limit_per_minute_per_ip", 100)
    body = {"query": "q", "ip_address": VISITOR_IP}
    assert (
        await http.post("/query", headers=website_headers(), json=body)
    ).status_code == 200
    second = await http.post("/query", headers=website_headers(), json=body)
    assert second.status_code == 429
    assert "per user" in second.json()["detail"]
    other = await http.post("/query", headers=website_headers(OTHER_VISITOR), json=body)
    assert other.status_code == 200


async def test_per_ip_limit_keys_on_forwarded_ip(
    http: httpx.AsyncClient, calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rate_limit.settings, "rate_limit_per_minute", 100)
    monkeypatch.setattr(rate_limit.settings, "rate_limit_per_minute_per_ip", 1)
    body = {"query": "q", "ip_address": VISITOR_IP}
    assert (
        await http.post("/query", headers=website_headers(), json=body)
    ).status_code == 200
    second = await http.post(
        "/query", headers=website_headers(OTHER_VISITOR), json=body
    )
    assert second.status_code == 429
    assert "per IP" in second.json()["detail"]
    elsewhere = await http.post(
        "/query",
        headers=website_headers(OTHER_VISITOR),
        json={"query": "q", "ip_address": "198.51.100.7"},
    )
    assert elsewhere.status_code == 200


async def test_llm_capacity_gate_applies_to_api_key_callers(
    http: httpx.AsyncClient, calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FullGate:
        def try_acquire(self) -> bool:
            return False

    monkeypatch.setattr("app.core.concurrency.get_llm_gate", _FullGate)
    response = await http.post("/query", headers=website_headers(), json={"query": "q"})
    assert response.status_code == 429
    assert "capacity" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------

WEBSITE_ROUTES = [
    ("POST", "/query", {"query": "q"}),
    ("GET", "/sessions", None),
    ("POST", "/sessions", {}),
    ("GET", f"/sessions/{SID}", None),
    ("PATCH", f"/sessions/{SID}", {"title": "t"}),
    ("DELETE", f"/sessions/{SID}", None),
    ("DELETE", f"/sessions/{SID}/messages/{MID}", None),
    ("POST", f"/sessions/{SID}/messages/{MID}/feedback", {"rating": 5}),
]


@pytest.mark.parametrize(
    "key_headers",
    [{}, {"X-API-Key": "wrong-key"}, {"X-API-Key": ""}],
    ids=["missing", "wrong", "empty"],
)
async def test_wrong_or_missing_key_gets_401(
    http: httpx.AsyncClient, calls: _Calls, key_headers: dict[str, str]
) -> None:
    for method, path, body in WEBSITE_ROUTES:
        response = await http.request(
            method, path, headers={**key_headers, "X-Client-Id": VISITOR}, json=body
        )
        assert response.status_code == 401, (method, path)
        assert response.json()["detail"] == "Access Denied!"
    assert calls.items == []


async def test_empty_configured_key_disables_the_api_key_path(
    http: httpx.AsyncClient, calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "api_key_name", "")
    for sent in ("", "anything", "ci-test-key"):
        for method, path, body in WEBSITE_ROUTES:
            response = await http.request(
                method,
                path,
                headers={"X-API-Key": sent, "X-Client-Id": VISITOR},
                json=body,
            )
            assert response.status_code == 401, (sent, method, path)
    assert calls.items == []


async def test_proxy_marker_without_session_gets_401(
    http: httpx.AsyncClient, calls: _Calls
) -> None:
    """An anonymous browser behind the dashboard proxy never gets the key path."""
    for method, path, body in WEBSITE_ROUTES:
        response = await http.request(
            method,
            path,
            headers={**website_headers(), "X-AskDIQ-Proxy": "1"},
            json=body,
        )
        assert response.status_code == 401, (method, path)
        assert response.json()["detail"] == "Not authenticated."
    assert calls.items == []


async def test_empty_session_header_does_not_fall_back_to_the_key_path(
    http: httpx.AsyncClient, calls: _Calls
) -> None:
    response = await http.get(
        "/sessions", headers={**website_headers(), "X-Session": ""}
    )
    assert response.status_code == 401
    assert calls.items == []


# ---------------------------------------------------------------------------
# SSO path and CSRF
# ---------------------------------------------------------------------------


@pytest.fixture
def resolved_session(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Real ``authenticate_session`` (CSRF included); only the lookup is stubbed."""
    tokens: list[str] = []

    class _Sessions:
        def __init__(self, _db: Any) -> None:
            pass

        async def resolve(self, token: str) -> AuthUser:
            tokens.append(token)
            return _sso_user()

    monkeypatch.setattr(auth_deps, "SessionService", _Sessions)
    yield tokens


def _sso_headers(client_id: str | None = VISITOR) -> dict[str, str]:
    """What the dashboard proxy sends for a state change."""
    return {
        **website_headers(client_id),
        "X-AskDIQ-Proxy": "1",
        "X-Session": "sess-1",
        "X-Requested-With": "askdiq",
        "Origin": get_settings().app_origin,
    }


async def test_sso_session_still_works_and_owns_by_email(
    http: httpx.AsyncClient, calls: _Calls, resolved_session: list[str]
) -> None:
    created = await http.post(
        "/sessions", headers=_sso_headers(), json={"ip_address": VISITOR_IP}
    )
    assert created.status_code == 201, created.text
    assert calls.last("create")["actor"] == SSO_EMAIL  # X-Client-Id is ignored

    asked = await http.post("/query", headers=_sso_headers(), json={"query": "q"})
    assert asked.status_code == 200, asked.text
    assert calls.last("query")["actor"] == SSO_EMAIL

    listed = await http.get(
        "/sessions",
        headers={**website_headers(), "X-AskDIQ-Proxy": "1", "X-Session": "sess-1"},
    )
    assert listed.status_code == 200
    assert calls.last("list") == {"actor": SSO_EMAIL}
    assert resolved_session == ["sess-1", "sess-1", "sess-1"]


async def test_suspended_sso_user_gets_403(
    http: httpx.AsyncClient, calls: _Calls
) -> None:
    async def suspended() -> AuthUser:
        return _sso_user(UserStatus.SUSPENDED)

    app.dependency_overrides[authenticate_session] = suspended
    try:
        response = await http.get("/sessions", headers=_sso_headers())
    finally:
        app.dependency_overrides.pop(authenticate_session, None)
    assert response.status_code == 403
    assert calls.items == []


@pytest.mark.parametrize(
    "drop",
    ["X-Requested-With", "Origin"],
)
async def test_csrf_still_enforced_for_cookie_sessions(
    http: httpx.AsyncClient,
    calls: _Calls,
    resolved_session: list[str],
    drop: str,
) -> None:
    for method, path, body in WEBSITE_ROUTES:
        if method == "GET":
            continue
        headers = {k: v for k, v in _sso_headers().items() if k != drop}
        response = await http.request(method, path, headers=headers, json=body)
        assert response.status_code == 403, (method, path)
        assert response.json()["detail"] == "Cross-site request rejected."
    foreign = {**_sso_headers(), "Origin": "https://evil.example"}
    response = await http.post("/query", headers=foreign, json={"query": "q"})
    assert response.status_code == 403
    assert resolved_session == []  # rejected before the session lookup
    assert calls.items == []


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------


def test_swagger_query_example_and_security_schemes() -> None:
    spec = app.openapi()
    ref = spec["paths"]["/query"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    schema = spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]
    (example,) = schema["examples"]
    assert set(example) == {"query", "session_id", "ip_address"}
    assert example["session_id"] == ""  # null would be dropped from the spec
    assert "filters" in schema["properties"]

    schemes = {
        name
        for requirement in spec["paths"]["/query"]["post"]["security"]
        for name in requirement
    }
    # Swagger's Authorize dialog offers only the service key (API_KEY_NAME);
    # X-Session / X-Client-Id are proxy and website headers, read but hidden.
    assert schemes == {"ServiceKey"}
    assert list(spec["components"]["securitySchemes"]) == ["ServiceKey"]


# ---------------------------------------------------------------------------
# Ownership against the real database (CI)
# ---------------------------------------------------------------------------


async def test_api_key_visitors_cannot_reach_each_others_sessions(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post(
        "/sessions",
        headers={"X-Client-Id": VISITOR},
        json={"title": "mine", "ip_address": VISITOR_IP},
    )
    assert created.status_code == 201, created.text
    sid = created.json()["id"]

    other = {"X-Client-Id": OTHER_VISITOR}
    assert (await client.get(f"/sessions/{sid}", headers=other)).status_code == 404
    renamed = await client.patch(
        f"/sessions/{sid}", headers=other, json={"title": "stolen"}
    )
    assert renamed.status_code == 404
    assert (await client.delete(f"/sessions/{sid}", headers=other)).status_code == 404
    listed = await client.get("/sessions", headers=other)
    assert sid not in {s["id"] for s in listed.json()}

    mine = await client.get(f"/sessions/{sid}", headers={"X-Client-Id": VISITOR})
    assert mine.status_code == 200
    assert mine.json()["title"] == "mine"


async def test_sso_and_api_key_sessions_are_isolated(
    client: httpx.AsyncClient,
) -> None:
    async def alice() -> AuthUser:
        return _sso_user()

    app.dependency_overrides[authenticate_session] = alice
    try:
        created = await client.post(
            "/sessions",
            headers={
                "X-Session": "sess-1",
                "X-Requested-With": "askdiq",
                "Origin": get_settings().app_origin,
            },
            json={"title": "alice"},
        )
    finally:
        app.dependency_overrides.pop(authenticate_session, None)
    assert created.status_code == 201, created.text
    sid = created.json()["id"]

    # A website caller naming Alice's email gets the shared default actor.
    for client_id in (SSO_EMAIL, VISITOR):
        response = await client.get(
            f"/sessions/{sid}", headers={"X-Client-Id": client_id}
        )
        assert response.status_code == 404, client_id
