"""Schemas of Settings > Knowledge links and the document link editor."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.knowledge.links import (
    MAX_HOST_LENGTH,
    MAX_HOSTS,
    MAX_URL_LENGTH,
    LinkError,
    normalize_host_patterns,
)

HostPattern = Annotated[
    str, StringConstraints(min_length=1, max_length=MAX_HOST_LENGTH)
]
LinkStatusValue = Literal["ok", "missing", "not_allowed"]


class KnowledgeSettingsRead(BaseModel):
    """``GET /knowledge/settings``: the link policy (defaults before a save)."""

    allowed_hosts: list[str] = Field(
        default_factory=list,
        description=(
            "Hosts a source URL may use: exact host names or `*.example.com` "
            "(any sub-domain). Empty = any https host."
        ),
    )
    require_source_url: bool = Field(
        default=False,
        description="A new document must be uploaded with a valid source URL.",
    )
    version: int = Field(
        default=0, description="Send it back on save (0 before the first save)."
    )
    updated_at: datetime | None = None
    updated_by: str | None = None
    max_hosts: int = MAX_HOSTS
    max_host_length: int = MAX_HOST_LENGTH


class KnowledgeSettingsUpdate(BaseModel):
    """``PUT /knowledge/settings``: the whole policy, with the current version."""

    model_config = ConfigDict(extra="forbid")

    allowed_hosts: list[HostPattern] = Field(
        default_factory=list,
        max_length=MAX_HOSTS,
        description=(
            "Host names (lower-cased, IDNA-encoded on save) or one leading "
            "wildcard `*.example.com`. Empty = any https host."
        ),
    )
    require_source_url: bool
    version: int = Field(ge=0, description="The version that was read (409 if stale).")

    @field_validator("allowed_hosts")
    @classmethod
    def _normalize(cls, value: list[str]) -> list[str]:
        try:
            return normalize_host_patterns(value)
        except LinkError as exc:
            raise ValueError(exc.message) from exc


class DocumentLinkUpdate(BaseModel):
    """``PATCH /documents/{id}``: the document's source link."""

    model_config = ConfigDict(extra="forbid")

    source_url: str | None = Field(
        default=None,
        max_length=MAX_URL_LENGTH,
        description="An https URL on the allow-list; null or empty removes the link.",
    )
    section_links: bool = Field(
        default=False,
        description="Cite `source_url#<section anchor>` for each chunk.",
    )


class DocumentLinkRead(BaseModel):
    """The document's link after ``PATCH /documents/{id}``."""

    document_id: str
    source_url: str | None = None
    section_links: bool = False
    has_link: bool = False
    link_status: LinkStatusValue = "missing"
    link_host: str | None = None
