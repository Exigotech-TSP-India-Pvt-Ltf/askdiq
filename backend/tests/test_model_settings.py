"""Settings > Models: runtime model / pricing overrides.

Pure tests (resolution, change detection, live verification, costing) run
anywhere; the API tests need Postgres (CI).
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import model_settings as route
from app.api.routes.admin_usage import billed_rate_card
from app.core.config import get_settings
from app.governance.costing import get_current_pricing_rates
from app.model_settings import service, verify
from app.models.db_models import AuditLog, ModelSettings
from app.schemas.model_settings import ModelSettingsUpdate

URL = "/admin/models"


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    service._state.value = None
    service._state.loaded_at = 0.0
    yield
    service._state.value = None
    service._state.loaded_at = 0.0


def _row(**overrides: Any) -> ModelSettings:
    values = dict.fromkeys(
        (
            "llm_deployment",
            "embedding_model",
            "price_prompt_per_million",
            "price_cached_per_million",
            "price_output_per_million",
            "price_embed_per_million",
        )
    )
    return ModelSettings(id=1, version=3, **{**values, **overrides})


# --- Resolution ---------------------------------------------------------------


def test_resolve_without_row_uses_env_values() -> None:
    settings = get_settings()
    resolved = service.resolve(None, settings)
    assert resolved.llm_deployment == settings.azure_deployment_name
    assert resolved.embedding_model == settings.embedding_model
    assert resolved.price_output_per_million == settings.price_output_per_million
    assert resolved.embedding_dim == settings.embedding_dim
    assert resolved.overridden == []
    assert resolved.version == 0


def test_resolve_applies_only_non_null_overrides() -> None:
    settings = get_settings()
    resolved = service.resolve(
        _row(llm_deployment="gpt-next", price_prompt_per_million=0.5), settings
    )
    assert resolved.llm_deployment == "gpt-next"
    assert resolved.price_prompt_per_million == 0.5
    assert resolved.embedding_model == settings.embedding_model
    assert resolved.defaults.llm_deployment == settings.azure_deployment_name
    assert resolved.overridden == ["llm_deployment", "price_prompt_per_million"]
    assert resolved.version == 3


def test_changed_models_ignores_unchanged_and_resolves_cleared() -> None:
    settings = get_settings()
    before = service.resolve(_row(llm_deployment="gpt-next"), settings)
    # Clearing the override goes back to the .env deployment: a change.
    assert service._changed_models({"llm_deployment": None}, before) == (
        settings.azure_deployment_name,
        None,
    )
    # Same value as today, or untouched: nothing to verify.
    assert service._changed_models({"llm_deployment": "gpt-next"}, before) == (
        None,
        None,
    )
    assert service._changed_models({"price_embed_per_million": 1.0}, before) == (
        None,
        None,
    )


def test_update_schema_validation() -> None:
    with pytest.raises(ValueError, match="No fields to update"):
        ModelSettingsUpdate(version=0)
    with pytest.raises(ValueError):
        ModelSettingsUpdate(version=0, llm_deployment="bad name/../x")
    with pytest.raises(ValueError):
        ModelSettingsUpdate(version=0, price_prompt_per_million=-1)
    with pytest.raises(ValueError):
        ModelSettingsUpdate(version=0, unknown="x")  # type: ignore[call-arg]
    payload = ModelSettingsUpdate(version=2, embedding_model=None)
    assert payload.changes() == {"embedding_model": None}


# --- Effective values in the hot path ------------------------------------------


def test_costing_and_rate_card_use_effective_rates() -> None:
    service._store(service.resolve(_row(price_output_per_million=9.5), get_settings()))
    assert float(get_current_pricing_rates()["output_per_m"]) == 9.5
    card = billed_rate_card({})
    assert card["price_output_per_million"] == 9.5
    # An exchange keeps the rates it was billed at.
    billed = billed_rate_card({"pricing": {"output_per_m": 2.0, "embed_per_m": 0.01}})
    assert billed["price_output_per_million"] == 2.0
    assert billed["price_embed_per_million"] == 0.01


def test_current_falls_back_to_env_outside_event_loop() -> None:
    assert (
        service.current_model_settings().llm_deployment
        == get_settings().azure_deployment_name
    )


# --- Live verification ----------------------------------------------------------


class _FakeEmbeddings:
    def __init__(self, width: int | None = None, error: Exception | None = None):
        self.width = width
        self.error = error

    async def create(self, **_kwargs: Any) -> SimpleNamespace:
        if self.error:
            raise self.error
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.0] * self.width)])


def _fake_embedding_client(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> None:
    client = SimpleNamespace(embeddings=_FakeEmbeddings(**kwargs))
    monkeypatch.setattr(verify, "get_embedding_client", lambda: client)


async def test_verify_embedding_accepts_matching_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_embedding_client(monkeypatch, width=get_settings().embedding_dim)
    await verify.verify_embedding("text-embedding-3-small")


async def test_verify_embedding_rejects_other_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_embedding_client(monkeypatch, width=3072)
    with pytest.raises(HTTPException) as caught:
        await verify.verify_embedding("text-embedding-3-large")
    assert caught.value.status_code == 422
    assert "3072" in caught.value.detail


async def test_verify_embedding_reports_unusable_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_embedding_client(monkeypatch, error=RuntimeError("DeploymentNotFound"))
    with pytest.raises(HTTPException) as caught:
        await verify.verify_embedding("missing")
    assert caught.value.status_code == 422
    assert "missing" in caught.value.detail


async def test_verify_models_skips_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _llm(name: str) -> None:
        calls.append(f"llm:{name}")

    async def _embed(name: str) -> None:
        calls.append(f"embed:{name}")

    monkeypatch.setattr(verify, "verify_llm", _llm)
    monkeypatch.setattr(verify, "verify_embedding", _embed)
    await verify.verify_models("gpt-next", None)
    assert calls == ["llm:gpt-next"]


# --- API (Postgres) -------------------------------------------------------------


@pytest.fixture
def verified(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str | None, str | None]]:
    calls: list[tuple[str | None, str | None]] = []

    async def _verify(llm: str | None, embedding: str | None) -> None:
        calls.append((llm, embedding))
        if llm == "broken":
            raise HTTPException(422, detail="The LLM deployment 'broken' failed")

    monkeypatch.setattr(route, "verify_models", _verify)
    return calls


async def test_get_returns_env_defaults_before_any_save(client: AsyncClient) -> None:
    response = await client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 0
    assert body["overridden"] == []
    assert body["llm_deployment"] == get_settings().azure_deployment_name


async def test_update_prices_and_model_is_verified_and_audited(
    client: AsyncClient,
    db_session: AsyncSession,
    verified: list[tuple[str | None, str | None]],
) -> None:
    response = await client.put(
        URL,
        json={
            "version": 0,
            "llm_deployment": "gpt-next",
            "price_output_per_million": 4.0,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == 1
    assert body["llm_deployment"] == "gpt-next"
    assert body["price_output_per_million"] == 4.0
    assert set(body["overridden"]) == {"llm_deployment", "price_output_per_million"}
    assert verified == [("gpt-next", None)]
    # This process now uses the new values.
    assert service.current_model_settings().llm_deployment == "gpt-next"

    audit = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == service.AUDIT_UPDATED)
        )
    ).scalar_one()
    assert audit.detail["changes"]["price_output_per_million"]["to"] == 4.0

    # Clearing an override restores the .env value; prices are not re-verified.
    response = await client.put(
        URL, json={"version": 1, "price_output_per_million": None}
    )
    assert response.status_code == 200
    assert (
        response.json()["price_output_per_million"]
        == get_settings().price_output_per_million
    )
    assert verified == [("gpt-next", None)]


async def test_stale_version_conflicts(
    client: AsyncClient, verified: list[tuple[str | None, str | None]]
) -> None:
    assert (
        await client.put(URL, json={"version": 0, "price_embed_per_million": 0.03})
    ).status_code == 200
    response = await client.put(
        URL, json={"version": 0, "price_embed_per_million": 0.04}
    )
    assert response.status_code == 409


async def test_unusable_model_is_rejected_and_not_saved(
    client: AsyncClient, verified: list[tuple[str | None, str | None]]
) -> None:
    response = await client.put(URL, json={"version": 0, "llm_deployment": "broken"})
    assert response.status_code == 422
    assert (await client.get(URL)).json()["version"] == 0
