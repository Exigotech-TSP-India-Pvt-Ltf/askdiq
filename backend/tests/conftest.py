"""Global pytest fixtures for AskDIQ backend test suite."""

import hashlib
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.database import get_db
from main import app

settings = get_settings()

test_engine = create_async_engine(
    settings.database_url,
    poolclass=NullPool,
)

TestingSessionLocal = async_sessionmaker(
    test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provides a transactional database session rolled back after each test."""
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Test client with database dependency override and valid default auth."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"X-API-Key": settings.api_key_name or "ci-test-key"},
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def fake_embed():
    """Deterministic embedding fake generating reproducible 1536-dim vectors."""
    calls: list[str] = []

    def _make_vector(text: str) -> list[float]:
        calls.append(text)
        h = hashlib.sha256(text.encode("utf-8")).digest()
        base = [float(b) / 255.0 for b in h]
        return (base * 48)[:1536]

    async def _embed_text(
        text: str, task_type: str | None = None, **kwargs
    ) -> list[float]:
        return _make_vector(text)

    async def _embed_texts(
        texts: list[str], task_type: str | None = None, **kwargs
    ) -> list[list[float]]:
        from app.retrieval.embeddings import (
            current_embedding_stage,
            embedding_tokens_sink,
        )

        sink = embedding_tokens_sink.get()
        if sink is not None:
            stage = kwargs.get("stage") or current_embedding_stage.get()
            sink.append((stage, sum(len(t.split()) for t in texts) * 2 or 4))
        return [_make_vector(t) for t in texts]

    with (
        patch("app.retrieval.embeddings.embed_text", side_effect=_embed_text),
        patch("app.retrieval.embeddings.embed_texts", side_effect=_embed_texts),
        patch("app.guardrails.post_checks.embed_texts", side_effect=_embed_texts),
        patch("app.ingestion.service.embed_texts", side_effect=_embed_texts),
    ):
        yield {"calls": calls, "embed": _embed_text, "embed_many": _embed_texts}


@pytest.fixture
def fake_llm():
    """Configurable LLM response mock with token usage tracking."""
    calls: list[dict] = []
    response_holder = {
        "answer": "This is a deterministic fake answer for tests.",
        "usage": {
            "prompt_tokens": 15,
            "completion_tokens": 25,
            "total_tokens": 40,
            "reasoning_tokens": 0,
        },
    }

    async def _call_llm(
        prompt: str,
        context: list[str] | None = None,
        history: list | None = None,
        **kwargs,
    ):
        calls.append(
            {"prompt": prompt, "context": context, "history": history, "kwargs": kwargs}
        )
        return response_holder["answer"], response_holder["usage"]

    with (
        patch("app.harness.prompts.call_llm", side_effect=_call_llm),
        patch("app.harness.orchestrator.call_llm", side_effect=_call_llm),
    ):
        yield {
            "calls": calls,
            "set_response": lambda a, u: response_holder.update(
                {"answer": a, "usage": u}
            ),
        }


class FrozenTime:
    def __init__(self, start_timestamp: float = 1700000000.0):
        self._current = start_timestamp

    def time(self) -> float:
        return self._current

    def monotonic(self) -> float:
        return self._current

    def advance(self, seconds: float) -> None:
        self._current += seconds


@pytest.fixture
def frozen_time():
    """Allows freezing and advancing monotonic and wall time for rate limit testing."""
    ft = FrozenTime()
    with (
        patch("time.time", side_effect=ft.time),
        patch("time.monotonic", side_effect=ft.monotonic),
    ):
        yield ft
