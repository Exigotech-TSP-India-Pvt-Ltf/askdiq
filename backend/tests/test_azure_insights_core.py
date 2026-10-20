"""Azure Insights core: config, token cache, HTTP client, status, scheduler.

Unit tests use recorded-shape responses (tests/fixtures/azure) through
``httpx.MockTransport``; nothing calls Azure. The DB tests run in CI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import text

from app.azure_insights import scheduler, status
from app.azure_insights.auth import ClientCredentialsTokenProvider
from app.azure_insights.client import (
    QPU_RETRY_AFTER,
    REMAINING_READS,
    AzureClient,
    parse_retry_after,
)
from app.azure_insights.errors import (
    AzureApiError,
    AzureAuthError,
    AzureBudgetExhaustedError,
    AzureThrottledError,
    sanitize,
)
from app.azure_insights.scheduler import (
    STATUS_KIND,
    AzureSource,
    SourceResult,
    dedupe_key,
    next_slot,
    slot_start,
)
from app.core.config import Settings, get_settings
from app.core.events import TOPIC_CONSOLE_CHANGED, TOPIC_SNAPSHOT_UPDATED
from app.dashboard import snapshots
from app.jobs.queue import JobDeferredError
from tests.conftest import shared_session_factory, shared_session_runner
from tests.pg_helpers import new_engine, require_postgres

FIXTURES = Path(__file__).parent / "fixtures" / "azure"
SECRET = "fixture-secret-value-0123456789"  # noqa: S105 - test-only value
TENANT = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"
SUB = "33333333-3333-4333-8333-333333333333"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class _Capture(logging.Handler):
    def __init__(self, sink: list[str]) -> None:
        super().__init__(logging.DEBUG)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.append(f"{record.msg} {record.args}")


@pytest.fixture
def logs() -> Iterator[list[str]]:
    """Every log record (any logger) while the test runs."""
    sink: list[str] = []
    root = logging.getLogger()
    handler, level = _Capture(sink), root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield sink
    root.removeHandler(handler)
    root.setLevel(level)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_BASE_ENV = {
    "database_url": "postgresql+asyncpg://u:p@h/db",
    "azure_openai_api_key": "k",
    "azure_openai_endpoint": "https://x",
    "azure_openai_api_version": "v",
    "azure_deployment_name": "gpt-5-mini",
    "session_secret": "s" * 40,
    "auth_dev_mock": True,
    "env": "development",
}
_AZURE_IDS = {
    "azure_tenant_id": TENANT,
    "azure_client_id": CLIENT,
    "azure_client_secret": SECRET,
    "azure_subscription_id": SUB,
    "azure_resource_group": "rg-askdiq",
    "azure_openai_resource_name": "askdiq-foundry",
    "azure_openai_region": "australiaeast",
}


def make_settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **{**_BASE_ENV, **overrides})


def test_disabled_by_default_needs_no_azure_ids() -> None:
    cfg = make_settings()
    assert cfg.azure_insights_enabled is False
    assert cfg.azure_insights_configured is False
    assert cfg.azure_account_resource_id is None
    assert cfg.azure_retail_region == "eastus2"
    assert cfg.azure_pricing_currency == "AUD"
    assert cfg.azure_metrics_monthly_call_cap == 100_000


def test_enabled_with_missing_ids_fails_fast() -> None:
    with pytest.raises(ValidationError) as exc:
        make_settings(azure_insights_enabled=True, azure_tenant_id=TENANT)
    message = str(exc.value)
    assert "AZURE_CLIENT_SECRET" in message
    assert "AZURE_SUBSCRIPTION_ID" in message


def test_enabled_rejects_unsafe_ids() -> None:
    with pytest.raises(ValidationError, match="AZURE_RESOURCE_GROUP"):
        make_settings(
            azure_insights_enabled=True,
            **{**_AZURE_IDS, "azure_resource_group": "rg/../../x"},
        )


def test_enabled_and_configured_builds_the_account_id_and_hides_the_secret() -> None:
    cfg = make_settings(azure_insights_enabled=True, **_AZURE_IDS)
    assert cfg.azure_insights_configured
    assert cfg.azure_account_resource_id == (
        f"/subscriptions/{SUB}/resourceGroups/rg-askdiq/providers/"
        "Microsoft.CognitiveServices/accounts/askdiq-foundry"
    )
    assert cfg.azure_retail_region == "australiaeast"
    assert SECRET not in repr(cfg)
    assert SECRET not in str(cfg.model_dump())


def test_metrics_interval_cannot_go_below_five_minutes() -> None:
    with pytest.raises(ValidationError):
        make_settings(azure_metrics_interval_seconds=60)


# ---------------------------------------------------------------------------
# Token provider
# ---------------------------------------------------------------------------


def token_transport(
    responses: list[tuple[int, Any]], seen: list[httpx.Request]
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        code, body = responses[min(len(seen) - 1, len(responses) - 1)]
        return httpx.Response(code, json=body)

    return httpx.MockTransport(handler)


def provider(
    transport: httpx.MockTransport, clock: Clock
) -> ClientCredentialsTokenProvider:
    return ClientCredentialsTokenProvider(
        TENANT, CLIENT, SecretStr(SECRET), transport=transport, clock=clock
    )


async def test_token_is_cached_until_shortly_before_expiry() -> None:
    seen: list[httpx.Request] = []
    clock = Clock()
    tokens = provider(token_transport([(200, fixture("token_ok.json"))], seen), clock)
    first = await tokens.token()
    assert await tokens.token() == first
    assert len(seen) == 1
    form = dict(httpx.QueryParams(seen[0].content.decode()))
    assert form["grant_type"] == "client_credentials"
    assert form["scope"] == "https://management.azure.com/.default"
    assert str(seen[0].url) == (
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
    )
    clock.t += 3599 - 300 - 1  # still inside the margin
    await tokens.token()
    assert len(seen) == 1
    clock.t += 2  # within 300 s of expiry: refreshed
    await tokens.token()
    assert len(seen) == 2


async def test_concurrent_callers_share_one_token_request() -> None:
    seen: list[httpx.Request] = []
    tokens = provider(token_transport([(200, fixture("token_ok.json"))], seen), Clock())
    results = await asyncio.gather(*(tokens.token() for _ in range(5)))
    assert len(set(results)) == 1
    assert len(seen) == 1


async def test_token_error_never_exposes_the_secret(logs: list[str]) -> None:
    seen: list[httpx.Request] = []
    body = fixture("token_invalid_client.json")
    body["error_description"] += f" echo {SECRET}"
    tokens = provider(token_transport([(401, body)], seen), Clock())
    with pytest.raises(AzureAuthError) as exc:
        await tokens.token()
    assert exc.value.code == "invalid_client"
    assert "AADSTS7000215" in exc.value.message
    assert SECRET not in str(exc.value)
    assert SECRET not in " ".join(logs)
    assert SECRET not in repr(tokens)


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class Script:
    """A MockTransport answering from a list of (status, json, headers)."""

    def __init__(self, *steps: tuple[int, Any, dict[str, str] | None]) -> None:
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            code, body, headers = self.steps[
                min(len(self.requests) - 1, len(self.steps) - 1)
            ]
            if isinstance(body, Exception):
                raise body
            return httpx.Response(code, json=body, headers=headers or {})

        return httpx.MockTransport(handler)


class FakeTokens:
    def __init__(self) -> None:
        self.n = 0
        self.invalidated = 0

    async def token(self) -> str:
        self.n += 1
        return f"tok{self.n}"

    def invalidate(self) -> None:
        self.invalidated += 1


def client(script: Script, **kwargs: Any) -> AzureClient:
    sleeps: list[float] = kwargs.pop("sleeps", [])

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return AzureClient(
        transport=script.transport(),
        sleep=sleep,
        rng=random.Random(1),  # noqa: S311 - deterministic jitter
        **kwargs,
    )


async def test_short_retry_after_is_slept_then_retried() -> None:
    script = Script(
        (429, fixture("arm_throttled.json"), {"Retry-After": "3"}),
        (200, {"value": [1]}, None),
    )
    sleeps: list[float] = []
    async with client(script, sleeps=sleeps) as api:
        assert await api.get_json("/x") == {"value": [1]}
    assert sleeps == [3.0]
    assert len(script.requests) == 2


async def test_long_retry_after_defers_instead_of_sleeping() -> None:
    script = Script((429, fixture("arm_throttled.json"), {"Retry-After": "120"}))
    sleeps: list[float] = []
    async with client(script, sleeps=sleeps) as api:
        with pytest.raises(AzureThrottledError) as exc:
            await api.get_json("/x")
    assert exc.value.retry_after == 120
    assert exc.value.code == "TooManyRequests"
    assert sleeps == []


async def test_cost_management_qpu_retry_after_wins() -> None:
    headers = httpx.Headers({QPU_RETRY_AFTER: "45", "Retry-After": "2"})
    assert parse_retry_after(headers) == 45
    assert parse_retry_after(httpx.Headers({})) is None
    assert parse_retry_after(httpx.Headers({"Retry-After": "soon"})) is None


async def test_server_errors_back_off_with_jitter_then_succeed() -> None:
    script = Script((503, {}, None), (500, {}, None), (200, {"ok": True}, None))
    sleeps: list[float] = []
    async with client(script, sleeps=sleeps) as api:
        assert await api.get_json("/x") == {"ok": True}
    assert len(sleeps) == 2
    assert 0 <= sleeps[0] <= 1.0
    assert 0 <= sleeps[1] <= 2.0


async def test_server_errors_give_up_after_max_retries() -> None:
    script = Script((502, {}, None))
    async with client(script, max_retries=2) as api:
        with pytest.raises(AzureApiError) as exc:
            await api.get_json("/x")
    assert exc.value.status_code == 502
    assert len(script.requests) == 3


async def test_connection_errors_are_retried() -> None:
    script = Script((0, httpx.ConnectError("boom"), None), (200, {"ok": 1}, None))
    async with client(script) as api:
        assert await api.get_json("/x") == {"ok": 1}


async def test_401_refreshes_the_token_once() -> None:
    tokens = FakeTokens()
    script = Script((401, {"error": {"code": "InvalidAuthenticationToken"}}, None))
    async with client(script, token_source=tokens) as api:
        with pytest.raises(AzureApiError) as exc:
            await api.get_json("/x")
    assert exc.value.code == "InvalidAuthenticationToken"
    assert tokens.invalidated == 1
    assert len(script.requests) == 2
    assert script.requests[0].headers["Authorization"] == "Bearer tok1"
    assert script.requests[1].headers["Authorization"] == "Bearer tok2"


async def test_forbidden_is_a_sanitised_non_retryable_error(logs: list[str]) -> None:
    tokens = FakeTokens()
    script = Script((403, fixture("arm_forbidden.json"), None))
    async with client(script, token_source=tokens) as api:
        with pytest.raises(AzureApiError) as exc:
            await api.get_json("/x")
    assert exc.value.code == "AuthorizationFailed"
    assert exc.value.status_code == 403
    assert len(exc.value.message) <= 300
    assert "azure_request_failed" in " ".join(logs)
    assert "tok1" not in " ".join(logs)
    assert len(script.requests) == 1


async def test_low_remaining_reads_stops_the_run() -> None:
    script = Script((200, {"value": []}, {REMAINING_READS: "5"}))
    async with client(script) as api:
        await api.get_json("/a")
        with pytest.raises(AzureThrottledError) as exc:
            await api.get_json("/b")
    assert exc.value.code == "low_remaining_reads"
    assert len(script.requests) == 1


async def test_pagination_follows_next_links_up_to_the_cap() -> None:
    script = Script(
        (
            200,
            {"value": [{"i": 1}], "nextLink": "https://management.azure.com/p2"},
            None,
        ),
        (200, {"Items": [], "value": [{"i": 2}], "NextPageLink": "https://h/p3"}, None),
        (200, {"value": [{"i": 3}], "nextLink": "https://h/p4"}, None),
    )
    async with client(script) as api:
        items = [i async for i in api.paged("/p1", {"api-version": "1"}, max_pages=3)]
    assert [i["i"] for i in items] == [1, 2, 3]
    assert len(script.requests) == 3
    assert script.requests[0].url.params["api-version"] == "1"
    assert str(script.requests[1].url) == "https://management.azure.com/p2"


async def test_call_budget_is_consulted_before_every_call() -> None:
    used: list[int] = []

    async def budget() -> None:
        if len(used) >= 2:
            raise AzureBudgetExhaustedError("metrics", 2)
        used.append(1)

    script = Script((200, {"ok": 1}, None))
    async with client(script, budget=budget) as api:
        await api.get_json("/a")
        await api.get_json("/b")
        with pytest.raises(AzureBudgetExhaustedError):
            await api.get_json("/c")
    assert len(script.requests) == 2


def test_sanitize_masks_tokens_and_secrets() -> None:
    raw = (
        "failed Authorization: Bearer abc.def.ghi client_secret=xyz "
        f"token eyJhbGciOi.eyJzdWIiOiIx.c2ln and {SECRET}\n" + "x" * 500
    )
    clean = sanitize(raw, (SECRET,))
    for leaked in ("abc.def.ghi", "xyz", "eyJhbGciOi", SECRET, "\n"):
        assert leaked not in clean
    assert len(clean) <= 300


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------


def test_slots_are_epoch_aligned_and_keyed() -> None:
    now = datetime(2026, 10, 20, 10, 7, 30, tzinfo=UTC)
    assert slot_start(now, 900) == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    assert next_slot(now, 900) == datetime(2026, 10, 20, 10, 15, tzinfo=UTC)
    source = AzureSource("demo", "Demo", lambda _s: 900, _noop)
    assert dedupe_key(source, next_slot(now, 900)) == "azure.demo:20261020T101500"


async def _noop(_ctx: scheduler.SourceContext) -> SourceResult:
    return SourceResult()


# ---------------------------------------------------------------------------
# Status table + scheduler (Postgres, CI)
# ---------------------------------------------------------------------------


async def test_call_cap_is_enforced_atomically_and_resets_monthly(db_session) -> None:
    source = "test-cap"
    now = datetime(2026, 10, 20, tzinfo=UTC)
    assert await status.consume_call(db_session, source, 2, now=now) == 1
    assert await status.consume_call(db_session, source, 2, now=now) == 2
    with pytest.raises(AzureBudgetExhaustedError):
        await status.consume_call(db_session, source, 2, now=now)
    nxt = datetime(2026, 11, 1, tzinfo=UTC)
    assert await status.consume_call(db_session, source, 2, now=nxt) == 1
    with pytest.raises(AzureBudgetExhaustedError):
        await status.consume_call(db_session, source, 0, now=nxt)
    rows = await status.read_all(db_session)
    assert status.calls_this_month(rows[source], date(2026, 11, 5)) == 1
    assert status.calls_this_month(rows[source], date(2026, 12, 1)) == 0


async def test_status_upserts_are_idempotent(db_session) -> None:
    now = datetime(2026, 10, 20, tzinfo=UTC)
    await status.record_failure(
        db_session, "test-st", AzureApiError("AuthorizationFailed", "no"), now=now
    )
    await status.record_failure(
        db_session, "test-st", AzureApiError("AuthorizationFailed", "no"), now=now
    )
    row = (await status.read_all(db_session))["test-st"]
    assert (row.state, row.consecutive_failures) == ("error", 2)
    await status.record_failure(db_session, "test-st", AzureThrottledError(30), now=now)
    await db_session.refresh(row)
    assert (row.state, row.consecutive_failures) == ("throttled", 2)
    await status.record_success(db_session, "test-st", now=now, detail={"a": 1})
    await status.record_success(db_session, "test-st", now=now, detail={"a": 1})
    await db_session.refresh(row)
    assert (row.state, row.consecutive_failures, row.detail) == ("ok", 0, {"a": 1})
    assert row.last_error_code == "throttled"  # the last error stays on the card
    rows = (
        await db_session.execute(
            text("SELECT count(*) FROM azure_sync_status WHERE source = 'test-st'")
        )
    ).scalar_one()
    assert rows == 1


@pytest.fixture
def demo_source() -> Iterator[dict[str, Any]]:
    """A public demo source with its own snapshot kind."""
    state: dict[str, Any] = {"runs": 0, "error": None}
    kind = "azure.test-demo"

    async def run(ctx: scheduler.SourceContext) -> SourceResult:
        state["runs"] += 1
        if state["error"] is not None:
            raise state["error"]
        return SourceResult(
            rows=3, detail={"run": state["runs"], "at": ctx.now.isoformat()}
        )

    async def build(_gather: Any, _params: dict) -> dict:
        return {"runs": state["runs"]}

    snapshots.register(kind, build, [{}])
    scheduler.register_source(
        AzureSource(
            "test-demo",
            "Demo",
            lambda _s: 900,
            run,
            needs_credentials=False,
            snapshot_kinds=(kind,),
            live_scopes=("azure.test-scope",),
        )
    )
    state["kind"] = kind
    yield state
    scheduler.unregister_source("test-demo")
    snapshots._KINDS.pop(kind, None)  # noqa: SLF001


@pytest.fixture
def published(monkeypatch) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []

    async def fake_publish(topic: str, item_id: str) -> None:
        seen.append((topic, str(item_id)))

    monkeypatch.setattr(snapshots, "publish_event", fake_publish)
    monkeypatch.setattr(scheduler, "publish_event", fake_publish)
    return seen


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_successful_poll_updates_snapshots_and_announces_live(
    db_session, demo_source, published, monkeypatch
) -> None:
    """The contract: poll -> persist -> refresh snapshot -> announce live."""
    monkeypatch.setattr(get_settings(), "dashboard_snapshots_persist", True)
    runner = shared_session_runner(db_session)
    kind = demo_source["kind"]
    before = await snapshots.get_snapshot(kind, {}, runner.gather)
    assert before == {"runs": 0}

    result = await scheduler.run_source(
        "test-demo",
        session_factory=shared_session_factory(db_session),
        gather=runner.gather,
    )
    await _settle()
    assert result is not None and result.rows == 3
    # The memory copy and the stored row hold the new payload.
    assert await snapshots.get_snapshot(kind, {}, runner.gather) == {"runs": 1}
    stored = (
        await db_session.execute(
            text("SELECT payload FROM dashboard_snapshots WHERE kind = :k"), {"k": kind}
        )
    ).scalar_one()
    assert stored == {"runs": 1}
    # Open consoles are told: the source's kind, the status card, extra scopes.
    changed = {item for topic, item in published if topic == TOPIC_CONSOLE_CHANGED}
    assert {kind, STATUS_KIND, "azure.test-scope"} <= changed
    assert any(
        topic == TOPIC_SNAPSHOT_UPDATED and item.startswith(f"{kind}|")
        for topic, item in published
    )
    status_payload = await snapshots.get_snapshot(STATUS_KIND, {}, runner.gather)
    demo = next(s for s in status_payload["sources"] if s["source"] == "test-demo")
    assert demo["state"] == "ok"
    assert demo["last_success_at"] is not None
    assert status_payload["connected"] is False
    assert status_payload["reason"] == "not_configured"


async def test_permission_error_is_recorded_and_not_retried(
    db_session, demo_source, published
) -> None:
    demo_source["error"] = AzureApiError("AuthorizationFailed", "no", status_code=403)
    result = await scheduler.run_source(
        "test-demo",
        session_factory=shared_session_factory(db_session),
        gather=shared_session_runner(db_session).gather,
    )
    await _settle()
    assert result is None
    row = (await status.read_all(db_session))["test-demo"]
    assert (row.state, row.last_error_code, row.consecutive_failures) == (
        "error",
        "AuthorizationFailed",
        1,
    )
    assert (TOPIC_CONSOLE_CHANGED, STATUS_KIND) in published


async def test_throttled_run_defers_the_job_without_an_attempt(
    db_session, demo_source, published
) -> None:
    demo_source["error"] = AzureThrottledError(90, "slow down")
    with pytest.raises(JobDeferredError) as exc:
        await scheduler.run_source(
            "test-demo",
            session_factory=shared_session_factory(db_session),
            gather=shared_session_runner(db_session).gather,
        )
    assert exc.value.run_after > datetime.now(UTC) + timedelta(seconds=60)
    row = (await status.read_all(db_session))["test-demo"]
    assert row.state == "throttled"


async def test_transient_error_is_re_raised_for_the_queue_backoff(
    db_session, demo_source, published
) -> None:
    demo_source["error"] = AzureApiError("ServerError", "x", status_code=503)
    with pytest.raises(AzureApiError):
        await scheduler.run_source(
            "test-demo",
            session_factory=shared_session_factory(db_session),
            gather=shared_session_runner(db_session).gather,
        )


async def test_status_endpoint_not_connected_shape(client, demo_source) -> None:
    response = await client.get("/admin/azure/status")
    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is False
    assert body["reason"] == "not_configured"
    assert body["metrics_call_cap"] == get_settings().azure_metrics_monthly_call_cap
    demo = next(s for s in body["sources"] if s["source"] == "test-demo")
    assert demo["scheduled"] is False
    assert demo["state"] == "never_run"
    etag = response.headers["etag"]
    again = await client.get("/admin/azure/status", headers={"If-None-Match": etag})
    assert again.status_code == 304


def test_status_endpoint_is_documented() -> None:
    from main import app  # noqa: PLC0415

    schema = app.openapi()
    op = schema["paths"]["/admin/azure/status"]["get"]
    assert op["summary"] == "Azure Insights connection status"
    assert {"401", "403"} <= set(op["responses"])


def _enable(monkeypatch) -> None:
    cfg = get_settings()
    for name, value in {**_AZURE_IDS, "azure_insights_enabled": True}.items():
        monkeypatch.setattr(
            cfg, name, SecretStr(value) if name == "azure_client_secret" else value
        )


async def test_nothing_is_scheduled_while_disabled(demo_source) -> None:
    assert await scheduler.ensure_azure_scheduled() == {}


async def test_ensure_scheduled_dedupes_per_slot(monkeypatch, demo_source) -> None:
    engine = new_engine()
    await require_postgres(engine)
    _enable(monkeypatch)
    from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: PLC0415

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def cleanup() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM jobs WHERE type = 'azure.test-demo'"))
            await conn.execute(
                text("DELETE FROM azure_sync_status WHERE source = 'test-demo'")
            )

    await cleanup()
    try:
        now = datetime(2026, 10, 20, 10, 7, tzinfo=UTC)
        first = await scheduler.ensure_azure_scheduled(
            now=now, engine=engine, session_factory=factory
        )
        # Never run: queued at once (catch-up) for the current slot.
        assert first["test-demo"] == now
        again = await scheduler.ensure_azure_scheduled(
            now=now + timedelta(seconds=30), engine=engine, session_factory=factory
        )
        assert again["test-demo"] == now + timedelta(seconds=30)
        async with factory() as session:
            await status.mark_started(session, "test-demo", now)
            await session.commit()
        later = await scheduler.ensure_azure_scheduled(
            now=now + timedelta(minutes=1), engine=engine, session_factory=factory
        )
        assert later["test-demo"] == datetime(2026, 10, 20, 10, 15, tzinfo=UTC)
        await scheduler.ensure_azure_scheduled(
            now=now + timedelta(minutes=2), engine=engine, session_factory=factory
        )
        async with engine.connect() as conn:
            keys = (
                (
                    await conn.execute(
                        text(
                            "SELECT dedupe_key FROM jobs WHERE type = 'azure.test-demo' "
                            "ORDER BY dedupe_key"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert keys == [
            "azure.test-demo:20261020T100000",
            "azure.test-demo:20261020T101500",
        ]
        async with factory() as session:
            row = (await status.read_all(session))["test-demo"]
        assert row.next_run_at == datetime(2026, 10, 20, 10, 15, tzinfo=UTC)
    finally:
        await cleanup()
        await engine.dispose()
