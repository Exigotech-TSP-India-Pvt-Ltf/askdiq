"""Outbound timeouts and the LLM circuit breaker (planchanges4 B1.2.3).

The Azure client is mocked; no network or database is used.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest

from app.core.circuit import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    get_llm_breaker,
    is_transient_failure,
)
from app.core.config import get_settings
from app.core.quota import quota_monitor
from app.evaluation import judge
from app.harness import orchestrator, prompts
from app.retrieval import embeddings
from app.schemas.query import SourceChunk

settings = get_settings()
_REQUEST = httpx.Request("POST", "https://ci-test-resource.openai.azure.com/")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=_REQUEST)


def _bad_request() -> openai.BadRequestError:
    response = httpx.Response(400, request=_REQUEST)
    return openai.BadRequestError("content filtered", response=response, body=None)


def _breaker(clock: FakeClock, threshold: int = 3) -> CircuitBreaker:
    return CircuitBreaker(
        "test",
        failure_threshold=threshold,
        window_seconds=60.0,
        reset_timeout_seconds=30.0,
        clock=clock,
    )


async def _fail(breaker: CircuitBreaker, exc: Exception) -> None:
    with pytest.raises(type(exc)):
        async with breaker.guard():
            raise exc


@pytest.fixture(autouse=True)
def _reset_shared_breaker():
    get_llm_breaker().reset()
    yield
    get_llm_breaker().reset()


# --- Breaker state machine ---------------------------------------------------


async def test_breaker_opens_after_n_failures_in_window() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)
    for _ in range(2):
        await _fail(breaker, _timeout_error())
    assert breaker.state is CircuitState.CLOSED
    await _fail(breaker, _timeout_error())
    assert breaker.state is CircuitState.OPEN

    called = False
    with pytest.raises(CircuitOpenError) as info:
        async with breaker.guard():
            called = True
    assert not called
    assert info.value.retry_after == pytest.approx(30.0)


async def test_failures_outside_window_do_not_open() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)
    await _fail(breaker, _timeout_error())
    await _fail(breaker, _timeout_error())
    clock.advance(61)
    await _fail(breaker, _timeout_error())
    assert breaker.state is CircuitState.CLOSED


async def test_client_errors_do_not_count_and_success_resets() -> None:
    clock = FakeClock()
    breaker = _breaker(clock, threshold=2)
    await _fail(breaker, _timeout_error())
    await _fail(breaker, _bad_request())  # dependency answered: resets
    await _fail(breaker, _timeout_error())
    assert breaker.state is CircuitState.CLOSED


async def test_breaker_half_opens_then_closes_on_successful_probe() -> None:
    clock = FakeClock()
    breaker = _breaker(clock, threshold=1)
    await _fail(breaker, _timeout_error())
    assert breaker.state is CircuitState.OPEN

    clock.advance(30)
    assert breaker.state is CircuitState.HALF_OPEN
    async with breaker.guard():
        # Only one probe at a time while half-open.
        with pytest.raises(CircuitOpenError):
            async with breaker.guard():
                pass
    assert breaker.state is CircuitState.CLOSED


async def test_failed_probe_reopens_breaker() -> None:
    clock = FakeClock()
    breaker = _breaker(clock, threshold=1)
    await _fail(breaker, _timeout_error())
    clock.advance(30)
    assert breaker.state is CircuitState.HALF_OPEN
    await _fail(breaker, _timeout_error())
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        async with breaker.guard():
            pass


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_timeout_error(), True),
        (openai.APIConnectionError(request=_REQUEST), True),
        (httpx.ReadTimeout("slow"), True),
        (TimeoutError(), True),
        (
            openai.RateLimitError(
                "429", response=httpx.Response(429, request=_REQUEST), body=None
            ),
            True,
        ),
        (
            openai.InternalServerError(
                "503", response=httpx.Response(503, request=_REQUEST), body=None
            ),
            True,
        ),
        (_bad_request(), False),
        (ValueError("parse"), False),
    ],
)
def test_transient_failure_classification(exc: Exception, expected: bool) -> None:
    assert is_transient_failure(exc) is expected


# --- Timeouts on outbound clients --------------------------------------------


def test_outbound_clients_have_explicit_timeouts() -> None:
    assert prompts._client.timeout == settings.azure_openai_request_timeout
    assert embeddings._embedding_client.timeout == settings.outbound_timeout_seconds
    judge_client = judge._client_for(settings.azure_deployment_name)
    assert judge_client.timeout == settings.azure_openai_request_timeout
    for client in (prompts._client, embeddings._embedding_client, judge_client):
        assert client.max_retries == settings.azure_openai_max_retries


# --- call_llm integration -----------------------------------------------------


async def test_call_llm_timeout_trips_breaker_then_fails_fast(monkeypatch) -> None:
    clock = FakeClock()
    breaker = _breaker(clock, threshold=2)
    monkeypatch.setattr(prompts, "get_llm_breaker", lambda: breaker)
    # Keep the process-wide quota telemetry untouched by this test.
    monkeypatch.setattr(quota_monitor, "record_chat", MagicMock())
    create = AsyncMock(side_effect=_timeout_error())
    monkeypatch.setattr(prompts._client.chat.completions, "create", create)

    for _ in range(2):
        with pytest.raises(openai.APITimeoutError):
            await prompts.call_llm("What is DeployIQ?", ["DeployIQ is a platform."])
    assert breaker.state is CircuitState.OPEN

    with pytest.raises(CircuitOpenError):
        await prompts.call_llm("What is DeployIQ?", ["DeployIQ is a platform."])
    assert create.await_count == 2  # open breaker never reached Azure


# --- Graceful llm_failed outcome ----------------------------------------------


def _source() -> SourceChunk:
    return SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        content="Deploy IQ content",
        score=0.8,
        metadata={"source_name": "about-us.md"},
    )


async def _run_query_with_llm(monkeypatch, fake_call_llm) -> object:
    monkeypatch.setattr(orchestrator, "run_pre_checks", lambda *_a, **_k: [])

    async def fake_retrieve(*_a, **_k):
        return [_source()]

    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    monkeypatch.setattr(orchestrator, "call_llm", fake_call_llm)
    return await orchestrator.run_query(
        query="What is Deploy IQ?",
        top_k=6,
        filters=None,
        vector_store=None,
        request_id="test-request",
        stage_timings={},
    )


async def test_mocked_llm_timeout_yields_llm_failed_outcome(monkeypatch) -> None:
    calls = 0

    async def timing_out(*_a, **_k):
        nonlocal calls
        calls += 1
        raise _timeout_error()

    response = await _run_query_with_llm(monkeypatch, timing_out)
    assert response.outcome == "llm_failed"
    assert "llm_call_failed" in response.guardrail_flags
    assert response.grounded is False
    assert calls == settings.max_llm_retries + 1


async def test_open_breaker_yields_llm_failed_without_retry(monkeypatch) -> None:
    calls = 0

    async def breaker_open(*_a, **_k):
        nonlocal calls
        calls += 1
        raise CircuitOpenError("azure_openai_chat", retry_after=10.0)

    response = await _run_query_with_llm(monkeypatch, breaker_open)
    assert response.outcome == "llm_failed"
    assert "llm_call_failed" in response.guardrail_flags
    assert calls == 1
