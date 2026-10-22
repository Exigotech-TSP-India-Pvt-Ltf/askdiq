"""Error envelope (``code``), dependency-failure mapping, get_db close safety,
CSRF same-origin handling and the single Swagger security scheme."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
import openai
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IllegalStateChangeError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import deps
from app.core import database
from app.core.circuit import CircuitOpenError
from app.core.config import get_settings
from app.core.errors import ErrorCode, code_for_status
from app.core.exceptions import ConflictError, register_exception_handlers
from main import app as main_app
from tests.pg_helpers import new_engine, require_postgres

# --- envelope + dependency mapping (isolated app) --------------------------------


class _OrigError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("db said no")
        self.sqlstate = sqlstate


def _openai_error(cls: type[openai.APIStatusError], status: int, **headers: str):
    request = httpx.Request("POST", "https://example.openai.azure.com/x")
    response = httpx.Response(status, headers=headers, request=request)
    return cls(
        "upstream failure for jane.citizen@example.com", response=response, body=None
    )


ERRORS: dict[str, BaseException] = {
    "statement_timeout": DBAPIError("SELECT 1", {}, _OrigError("57014")),
    "pool_timeout": PoolTimeoutError("QueuePool limit reached"),
    "rate_limited": _openai_error(openai.RateLimitError, 429, **{"retry-after": "7"}),
    "upstream_5xx": _openai_error(openai.InternalServerError, 500),
    "upstream_400": _openai_error(openai.BadRequestError, 400),
    "upstream_timeout": openai.APITimeoutError(
        request=httpx.Request("POST", "https://example.openai.azure.com/x")
    ),
    "circuit_open": CircuitOpenError("azure_openai_chat", 12.2),
    "conflict": ConflictError("Version conflict."),
    "other_db": DBAPIError("SELECT 1", {}, _OrigError("23505")),
}


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom/{name}")
    async def boom(name: str) -> None:
        raise ERRORS[name]

    @app.get("/items/{item_id}")
    async def item(item_id: int) -> dict:
        return {"id": item_id}

    return app


@pytest.fixture
async def isolated() -> AsyncClient:
    transport = ASGITransport(app=_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.mark.parametrize(
    ("name", "status", "code", "retry_after"),
    [
        ("statement_timeout", 503, "db_timeout", "5"),
        ("pool_timeout", 503, "db_unavailable", "5"),
        ("rate_limited", 503, "upstream_rate_limited", "7"),
        ("upstream_5xx", 503, "upstream_unavailable", "5"),
        ("upstream_400", 502, "upstream_error", None),
        ("upstream_timeout", 504, "upstream_timeout", None),
        ("circuit_open", 503, "upstream_circuit_open", "13"),
        ("conflict", 409, "conflict", None),
    ],
)
async def test_dependency_errors_map_to_coded_problems(
    isolated: AsyncClient, name: str, status: int, code: str, retry_after: str | None
) -> None:
    res = await isolated.get(f"/boom/{name}")
    assert res.status_code == status
    assert res.headers["content-type"].startswith("application/problem+json")
    body = res.json()
    assert body["code"] == code
    assert body["status"] == status
    assert body["request_id"]
    assert set(body) >= {"type", "title", "status", "detail", "code", "request_id"}
    assert res.headers.get("retry-after") == retry_after
    for leaked in ("jane.citizen", "SELECT", "Traceback", "QueuePool", "db said no"):
        assert leaked not in res.text


async def test_unrecognised_db_error_is_a_plain_500(isolated: AsyncClient) -> None:
    res = await isolated.get("/boom/other_db")
    assert res.status_code == 500
    body = res.json()
    assert body["code"] == "internal_error"
    assert body["type"] == "urn:askdiq:problem:internal-error"
    assert "23505" not in res.text


async def test_validation_and_404_carry_codes(isolated: AsyncClient) -> None:
    res = await isolated.get("/items/not-a-number")
    assert res.status_code == 422
    assert res.json()["code"] == "validation_error"
    assert res.json()["errors"]
    res = await isolated.get("/no/such/route")
    assert res.json()["code"] == "not_found"


def test_code_for_status_defaults() -> None:
    assert code_for_status(429) == "rate_limited"
    assert code_for_status(418) == "http_error"
    assert ErrorCode.CSRF_REJECTED == "csrf_rejected"


# --- auth error codes on the real app ---------------------------------------------


@pytest.fixture
async def real_app_client() -> AsyncClient:
    transport = ASGITransport(app=main_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def test_service_key_error_keeps_legacy_detail_and_adds_code(
    real_app_client: AsyncClient,
) -> None:
    res = await real_app_client.get(
        "/admin/alerts", headers={"X-API-Key": "definitely-wrong"}
    )
    assert res.status_code == 401
    body = res.json()
    assert body["detail"] == "Access Denied!"
    assert body["code"] == "invalid_service_key"


# --- CSRF: Swagger (same-origin) vs real cross-site --------------------------------


def _request(
    headers: dict[str, str], *, scheme: str = "http", host: str = "localhost:8000"
) -> Request:
    raw = [
        (k.lower().encode(), v.encode()) for k, v in {"host": host, **headers}.items()
    ]
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": scheme,
        "path": "/admin/alerts/x/resolve",
        "raw_path": b"/admin/alerts/x/resolve",
        "query_string": b"",
        "headers": raw,
        "server": (host.split(":", maxsplit=1)[0], 8000),
        "client": ("127.0.0.1", 50000),
    }
    return Request(scope)


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000"])
def test_swagger_same_origin_request_is_allowed(host: str) -> None:
    request = _request(
        {"origin": f"http://{host}", "sec-fetch-site": "same-origin"}, host=host
    )
    deps.enforce_csrf(request)  # no exception


def test_real_cross_site_session_request_is_rejected() -> None:
    for headers in (
        {"origin": "https://evil.example.com", "sec-fetch-site": "cross-site"},
        {"origin": "https://evil.example.com", "x-requested-with": "askdiq"},
        {},  # no Origin at all
        # Matching origin but the browser says cross-site: refuse.
        {"origin": "http://localhost:8000", "sec-fetch-site": "cross-site"},
    ):
        with pytest.raises(Exception) as caught:
            deps.enforce_csrf(_request(headers))
        assert getattr(caught.value, "status_code", None) == 403
        assert getattr(caught.value, "code", None) == "csrf_rejected"


def test_frontend_proxy_request_still_allowed() -> None:
    deps.enforce_csrf(
        _request(
            {"origin": get_settings().app_origin, "x-requested-with": "askdiq"},
            host="backend:8000",
        )
    )


def test_proxied_https_docs_origin_is_allowed_behind_nginx() -> None:
    cfg = get_settings().model_copy(update={"trust_proxy_headers": True})
    headers = {
        "origin": "https://api.askdiq.example.com",
        "x-forwarded-proto": "https",
        "x-forwarded-host": "api.askdiq.example.com",
        "sec-fetch-site": "same-origin",
    }
    with patch.object(deps, "get_settings", return_value=cfg):
        deps.enforce_csrf(_request(headers, host="backend:8000"))
    # Without TRUST_PROXY_HEADERS the forwarded values are ignored.
    untrusted = cfg.model_copy(update={"trust_proxy_headers": False})
    with patch.object(deps, "get_settings", return_value=untrusted):
        with pytest.raises(Exception) as caught:
            deps.enforce_csrf(_request(headers, host="backend:8000"))
        assert getattr(caught.value, "status_code", None) == 403


def test_request_origin_drops_default_ports() -> None:
    assert deps.request_origin(_request({}, host="api.example.com:80")) == (
        "http://api.example.com"
    )


# --- Swagger shows one security scheme ----------------------------------------------


def test_openapi_has_only_the_service_key_scheme() -> None:
    schema = main_app.openapi()
    schemes = schema["components"]["securitySchemes"]
    assert list(schemes) == ["ServiceKey"]
    assert schemes["ServiceKey"]["name"] == "X-API-Key"
    params = {
        p["name"].lower()
        for path in schema["paths"].values()
        for op in path.values()
        for p in op.get("parameters", [])
    }
    assert "x-session" not in params
    assert "x-client-id" not in params


# --- get_db: close is safe under cancellation / concurrent operations ---------------


class _FlakySession:
    """``close()`` fails while "another operation" runs, then succeeds."""

    def __init__(self, busy_closes: int) -> None:
        self.busy_closes = busy_closes
        self.close_calls = 0
        self.closed = False

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_calls <= self.busy_closes:
            raise IllegalStateChangeError(
                "Method 'close()' can't be called here; method "
                "'_connection_for_bind()' is already in progress"
            )
        self.closed = True


async def test_get_db_retries_close_while_operation_in_progress() -> None:
    session = _FlakySession(busy_closes=3)
    with patch.object(database, "RequestSessionLocal", return_value=session):
        gen = database.get_db()
        assert await gen.__anext__() is session
        await gen.aclose()
    assert session.closed
    assert session.close_calls == 4


async def test_get_db_close_survives_request_cancellation() -> None:
    session = _FlakySession(busy_closes=5)
    with patch.object(database, "RequestSessionLocal", return_value=session):

        async def request() -> None:
            async for _db in database.get_db():
                await asyncio.sleep(10)  # cancelled here (shutdown / disconnect)

        task = asyncio.create_task(request())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(100):
            if session.closed:
                break
            await asyncio.sleep(0.02)
    assert session.closed, "the shielded close must finish after cancellation"


async def test_get_db_close_waits_for_a_running_checkout() -> None:
    """Real SQLAlchemy: another task is mid connection checkout on the
    request's session when the request ends (the single-flight pattern).
    The old ``async with`` close raised IllegalStateChangeError here."""
    engine = new_engine(pool_size=1, max_overflow=0, pool_timeout=10)
    await require_postgres(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        blocker = await engine.connect()  # holds the only pooled connection
        with patch.object(database, "RequestSessionLocal", factory):
            gen = database.get_db()
            session = await gen.__anext__()

            async def shared_work() -> int:
                return (await session.execute(text("SELECT 1"))).scalar_one()

            async def release_later() -> None:
                await asyncio.sleep(0.3)
                await blocker.close()

            checkout = asyncio.create_task(shared_work())
            await asyncio.sleep(0.2)  # now blocked inside _connection_for_bind
            releaser = asyncio.create_task(release_later())
            await gen.aclose()  # must not raise
            await releaser
            assert await checkout == 1
        assert engine.pool.checkedout() == 0
    finally:
        await engine.dispose()
