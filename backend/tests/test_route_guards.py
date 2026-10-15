"""Route-walk guard tests (planchanges4 B2.3.4, OWASP A01: deny by default).

Walks ``app.routes`` and fails the build when a non-allow-listed route has no
guard dependency, then calls every guarded route to prove the guards answer
401 / 403 before any handler runs. None of these tests need a database.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Callable, Iterator

import httpx
import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.auth.deps import (
    PERMISSION_ATTR,
    authenticate_session,
    current_user,
    is_guard,
    require_active_user,
    require_role_or_chat,
)
from app.auth.permissions import Permission, Role, UserStatus
from app.auth.principal import AuthUser
from app.core.config import get_settings
from main import app

pytestmark = pytest.mark.real_auth

PUBLIC_PATHS = frozenset({"/health", "/branding"})
PUBLIC_PREFIXES = ("/branding/assets/", "/auth/sso/", "/mock-idp/")
DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})
CHAT = "chat"

# Every state-changing route and the (admin-level) permission it needs.
EXPECTED_WRITES: dict[tuple[str, str], str] = {
    ("POST", "/ingest"): Permission.DOCUMENTS_WRITE,
    ("POST", "/ingest/file"): Permission.DOCUMENTS_WRITE,
    ("DELETE", "/documents/{document_id}"): Permission.DOCUMENTS_WRITE,
    ("POST", "/admin/usage/test-ips"): Permission.TESTIP_WRITE,
    ("DELETE", "/admin/usage/test-ips/{ip_address:path}"): Permission.TESTIP_WRITE,
    ("POST", "/admin/feedback/{message_id}/promote-to-test"): (
        Permission.FEEDBACK_TRIAGE
    ),
    ("POST", "/admin/insights/taxonomy"): Permission.TAXONOMY_WRITE,
    ("PATCH", "/admin/insights/taxonomy/{kind}/{code}"): Permission.TAXONOMY_WRITE,
    ("POST", "/admin/insights/taxonomy/{kind}/{code}/archive"): (
        Permission.TAXONOMY_WRITE
    ),
    ("PUT", "/admin/branding"): Permission.BRANDING_WRITE,
    ("POST", "/admin/branding/assets"): Permission.BRANDING_WRITE,
    ("POST", "/admin/feedback/bulk"): Permission.FEEDBACK_TRIAGE,
    ("PATCH", "/admin/feedback/{feedback_id}"): Permission.FEEDBACK_TRIAGE,
    ("POST", "/admin/feedback/golden/{case_id}/run"): Permission.FEEDBACK_TRIAGE,
    ("POST", "/query"): CHAT,
    ("POST", "/sessions"): CHAT,
    ("PATCH", "/sessions/{session_id}"): CHAT,
    ("DELETE", "/sessions/{session_id}"): CHAT,
    ("DELETE", "/sessions/{session_id}/messages/{message_id}"): CHAT,
    ("POST", "/sessions/{session_id}/messages/{message_id}/feedback"): CHAT,
}
# GET routes needing more than dashboard:read.
EXPECTED_SPECIAL_READS: dict[str, str] = {
    "/admin/usage/export": Permission.USAGE_EXPORT,
    "/admin/feedback/export": Permission.FEEDBACK_EXPORT,
}


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def dependency_calls(dependant: Dependant) -> Iterator[Callable[..., object]]:
    for sub in dependant.dependencies:
        if sub.call is not None:
            yield sub.call
        yield from dependency_calls(sub)


def requirement(route: APIRoute) -> str | None:
    """The strongest guard on ``route``: a permission, "chat", or None."""
    calls = list(dependency_calls(route.dependant))
    for call in calls:
        permission = getattr(call, PERMISSION_ATTR, None)
        if permission is not None:
            return str(permission)
    if require_role_or_chat in calls:
        return CHAT
    if require_active_user in calls:
        return "active"
    if current_user in calls:
        return "signed-in"
    return None


def api_routes() -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute)]


def guarded_endpoints() -> list[tuple[str, str, str]]:
    """(method, path, requirement) for every guarded route."""
    out = []
    for route in api_routes():
        if is_public(route.path):
            continue
        req = requirement(route)
        out.extend((m, route.path, str(req)) for m in sorted(route.methods))
    return out


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", str(uuid.UUID(int=1)), path)


# ---------------------------------------------------------------------------
# Static walk
# ---------------------------------------------------------------------------


def test_every_non_public_route_has_a_guard() -> None:
    unguarded = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            assert route.path in DOCS_PATHS, f"unexpected non-API route {route.path}"
            continue
        if is_public(route.path):
            continue
        if not any(is_guard(c) for c in dependency_calls(route.dependant)):
            unguarded.append(f"{sorted(route.methods)} {route.path}")
    assert not unguarded, f"routes without an auth guard: {unguarded}"


def test_public_allow_list_matches_reality() -> None:
    public = {r.path for r in api_routes() if is_public(r.path)}
    assert {"/health", "/branding"} <= public
    for route in api_routes():
        if route.path in PUBLIC_PATHS:
            assert requirement(route) is None


def test_public_prefixes_present_are_assets_and_dev_mock_idp() -> None:
    prefixes = {
        p for r in api_routes() for p in PUBLIC_PREFIXES if r.path.startswith(p)
    }
    assert prefixes <= {"/branding/assets/", "/mock-idp/"}
    assert "/branding/assets/" in prefixes
    assert ("/mock-idp/" in prefixes) is get_settings().auth_dev_mock
    # The only public asset route is the read-only GET.
    asset_routes = [r for r in api_routes() if r.path.startswith("/branding/assets/")]
    assert [sorted(r.methods) for r in asset_routes] == [["GET"]]


def test_write_routes_require_admin_level_permissions() -> None:
    seen = {}
    for method, path, req in guarded_endpoints():
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            seen[(method, path)] = req
    assert seen == {k: str(v) for k, v in EXPECTED_WRITES.items()}
    for req in seen.values():
        assert req != Permission.DASHBOARD_READ


def test_read_routes_are_operator_level_and_chat_is_open() -> None:
    for method, path, req in guarded_endpoints():
        if method != "GET":
            continue
        if path.startswith("/sessions"):
            assert req == CHAT, path
        else:
            expected = EXPECTED_SPECIAL_READS.get(path, Permission.DASHBOARD_READ)
            assert req == str(expected), path


# ---------------------------------------------------------------------------
# Live sweep: every guarded route rejects before its handler runs
# ---------------------------------------------------------------------------


@pytest.fixture
async def raw_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def as_user(role: Role | None, status: UserStatus) -> Iterator[None]:
    async def _user() -> AuthUser:
        return AuthUser(
            id=uuid.uuid4(),
            email="sweep@example.com",
            name="Sweep",
            role=role,
            status=status,
            version=1,
        )

    app.dependency_overrides[authenticate_session] = _user
    try:
        yield
    finally:
        app.dependency_overrides.pop(authenticate_session, None)


@pytest.fixture
def pending_user() -> Iterator[None]:
    yield from as_user(None, UserStatus.PENDING)


@pytest.fixture
def suspended_user() -> Iterator[None]:
    yield from as_user(Role.SUPERADMIN, UserStatus.SUSPENDED)


@pytest.fixture
def operator_user() -> Iterator[None]:
    yield from as_user(Role.OPERATOR, UserStatus.ACTIVE)


@pytest.fixture
def admin_user() -> Iterator[None]:
    yield from as_user(Role.ADMIN, UserStatus.ACTIVE)


async def _call(
    client: httpx.AsyncClient, method: str, path: str, headers: dict[str, str]
) -> httpx.Response:
    return await client.request(method, concrete(path), headers=headers)


def _key() -> dict[str, str]:
    return {"X-API-Key": get_settings().api_key_name}


async def test_no_session_gets_401_everywhere(raw_client: httpx.AsyncClient) -> None:
    for method, path, _ in guarded_endpoints():
        response = await _call(raw_client, method, path, _key())
        assert response.status_code == 401, (method, path, response.text)
        assert response.json()["detail"] == "Not authenticated."


async def test_missing_service_key_gets_401_even_with_session(
    raw_client: httpx.AsyncClient, admin_user: None
) -> None:
    """B2.5: a valid session alone never reaches the API (proxy-only)."""
    for headers in ({}, {"X-API-Key": "wrong-key"}, {"X-API-Key": ""}):
        for method, path, _ in guarded_endpoints():
            response = await _call(
                raw_client, method, path, {**headers, "X-Session": "any"}
            )
            assert response.status_code == 401, (method, path)


async def test_pending_user_gets_403_on_every_dashboard_route(
    raw_client: httpx.AsyncClient, pending_user: None
) -> None:
    for method, path, req in guarded_endpoints():
        if req == CHAT:
            continue
        response = await _call(raw_client, method, path, _key())
        assert response.status_code == 403, (method, path)
        assert response.json()["detail"] == "Dashboard access requires approval."


async def test_suspended_user_gets_403_everywhere(
    raw_client: httpx.AsyncClient, suspended_user: None
) -> None:
    for method, path, _ in guarded_endpoints():
        response = await _call(raw_client, method, path, _key())
        assert response.status_code == 403, (method, path)
        assert response.json()["detail"] == "Account suspended."


async def test_operator_gets_403_on_admin_level_routes(
    raw_client: httpx.AsyncClient, operator_user: None
) -> None:
    admin_level = [
        (m, p)
        for m, p, req in guarded_endpoints()
        if req not in {CHAT, Permission.DASHBOARD_READ}
    ]
    assert admin_level
    for method, path in admin_level:
        response = await _call(raw_client, method, path, _key())
        assert response.status_code == 403, (method, path)
        assert response.json()["detail"] == "Insufficient permissions."
