"""Schemas for the runtime model and pricing overrides (Settings > Models)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Azure OpenAI deployment names: letters, digits, '.', '_' and '-'.
DEPLOYMENT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
# Upper bound on a per-million-token rate (USD); catches unit typos.
MAX_PRICE_PER_MILLION = 1000.0

DeploymentName = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=DEPLOYMENT_PATTERN)
]
PricePerMillion = Annotated[float, Field(ge=0, le=MAX_PRICE_PER_MILLION)]

# Overridable fields, in display order. ``None`` in the row = use the .env value.
MODEL_FIELDS = ("llm_deployment", "embedding_model")
PRICE_FIELDS = (
    "price_prompt_per_million",
    "price_cached_per_million",
    "price_output_per_million",
    "price_embed_per_million",
)
OVERRIDE_FIELDS = (*MODEL_FIELDS, *PRICE_FIELDS)
PriceSource = Literal["manual", "azure_retail"]


class ModelConfigValues(BaseModel):
    """One complete set of model names and USD rates per 1M tokens."""

    model_config = ConfigDict(frozen=True)

    llm_deployment: str
    embedding_model: str
    price_prompt_per_million: float
    price_cached_per_million: float
    price_output_per_million: float
    price_embed_per_million: float


class ModelSettingsRead(ModelConfigValues):
    """Effective values (overrides over .env), plus the .env defaults."""

    model_config = ConfigDict(frozen=True)

    embedding_dim: int = Field(
        description="Vector width of the knowledge base (EMBEDDING_DIM, read-only)."
    )
    defaults: ModelConfigValues = Field(description="The .env values.")
    overridden: list[str] = Field(
        default_factory=list, description="Fields currently overridden."
    )
    version: int = 0
    updated_at: datetime | None = None
    updated_by: str | None = None
    price_source: PriceSource | None = Field(
        default=None,
        description="Where the saved prices came from (null = .env / never saved).",
    )
    prices_synced_at: datetime | None = Field(
        default=None, description="When the confirmed Azure retail prices were fetched."
    )
    prices_effective_date: date | None = Field(
        default=None, description="Effective date of the confirmed Azure retail prices."
    )


class ModelSettingsUpdate(BaseModel):
    """Partial update; ``null`` clears an override (back to the .env value).

    Changing a model is verified with one live Azure call before it is saved.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0, description="Current version (0 before any save).")
    llm_deployment: DeploymentName | None = None
    embedding_model: DeploymentName | None = None
    price_prompt_per_million: PricePerMillion | None = None
    price_cached_per_million: PricePerMillion | None = None
    price_output_per_million: PricePerMillion | None = None
    price_embed_per_million: PricePerMillion | None = None
    price_source: PriceSource = Field(
        default="manual",
        description=(
            "`azure_retail` when the admin confirmed the official Azure retail "
            "prices (each changed price must equal the stored official price); "
            "records the effective and sync dates. Otherwise `manual`."
        ),
    )

    @model_validator(mode="after")
    def _has_changes(self) -> Self:
        if not self.model_fields_set - _META_FIELDS:
            raise ValueError("No fields to update")
        return self

    def changes(self) -> dict[str, Any]:
        return self.model_dump(include=self.model_fields_set - _META_FIELDS)


_META_FIELDS = frozenset({"version", "price_source"})
