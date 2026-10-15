"""Unified Multi-Dimension Filter Request Schemas & Validation Models.

Provides Pydantic models for validated, bounds-checked filter parameters across
all table listing endpoints in the AskDIQ Admin console (OWASP A03/A04 compliant).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator


class PaginationParams(BaseModel):
    """Bounds-checked pagination parameters protecting against unbounded memory consumption."""

    limit: int = Field(
        default=10,
        ge=1,
        le=200,
        description="Number of records to return per page (max 200).",
    )
    offset: int = Field(
        default=0,
        ge=0,
        le=10000,
        description="Zero-indexed offset for pagination (max 10,000).",
    )


class DateRangeFilter(BaseModel):
    """Optional date range filtering with validated timestamp bounds."""

    start_date: datetime | None = Field(
        default=None, description="ISO-8601 timestamp for start of range."
    )
    end_date: datetime | None = Field(
        default=None, description="ISO-8601 timestamp for end of range."
    )

    @field_validator("end_date")
    @classmethod
    def validate_date_range(cls, v: datetime | None, info) -> datetime | None:
        start = info.data.get("start_date")
        if start and v and v < start:
            raise ValueError("end_date cannot be earlier than start_date")
        return v


class BaseFilterParams(PaginationParams, DateRangeFilter):
    """Base query parameters accepted across analytical table endpoints."""

    q: str | None = Field(
        default=None,
        max_length=200,
        description="Search term to filter across indexed text columns.",
    )
    sort: str | None = Field(
        default=None,
        max_length=64,
        description="Allowed sort key with _asc or _desc suffix.",
    )

    @field_validator("q")
    @classmethod
    def sanitize_search_query(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned if cleaned else None


# ---------------------------------------------------------------------------
# Endpoint-Specific Filter Schemas
# ---------------------------------------------------------------------------


class UserFilterParams(BaseFilterParams):
    """Filters supported by the Users analytical table."""

    status: Literal["all", "active", "inactive"] = Field(
        default="all", description="Filter by user active status."
    )
    user_type: Literal["all", "production", "test"] = Field(
        default="all", description="Filter by production vs test IP classification."
    )


class SessionFilterParams(BaseFilterParams):
    """Filters supported by the Sessions telemetry table."""

    ip: str | None = Field(
        default=None,
        max_length=45,
        description="Filter sessions by specific client IP address.",
    )


class QueryFilterParams(BaseFilterParams):
    """Filters supported by the All Queries and Turn Economics table."""

    status: Literal["all", "success", "error", "timeout"] = Field(
        default="all", description="Filter by execution outcome status."
    )
    outcome: str | None = Field(
        default=None,
        max_length=32,
        description="Granular evaluation outcome tag (e.g. grounded, abstained, hallucinated).",
    )
    min_cost: float | None = Field(
        default=None,
        ge=0.0,
        description="Minimum total turn cost in USD.",
    )
    max_cost: float | None = Field(
        default=None,
        ge=0.0,
        description="Maximum total turn cost in USD.",
    )

    @field_validator("max_cost")
    @classmethod
    def validate_cost_range(cls, v: float | None, info) -> float | None:
        min_c = info.data.get("min_cost")
        if min_c is not None and v is not None and v < min_c:
            raise ValueError("max_cost cannot be less than min_cost")
        return v


class FeedbackFilterParams(BaseFilterParams):
    """Filters supported by the User Feedback table."""

    rating: Literal["all", "1", "2", "3", "4", "5", "positive", "negative"] = Field(
        default="all",
        description="Filter by star rating or sentiment bucket.",
    )


class GovernanceFilterParams(BaseFilterParams):
    """Filters supported by the Governance Traces and Logs table."""

    level: Literal[
        "all", "critical", "alert", "error", "warning", "warn", "info", "debug"
    ] = Field(default="all", description="Log level filter.")
    service: str | None = Field(
        default=None,
        max_length=64,
        description="Service or component name.",
    )


class DocumentFilterParams(BaseFilterParams):
    """Filters supported by the Knowledge Base Documents table."""

    origin_format: str | None = Field(
        default=None,
        max_length=32,
        description="Filter by file origin extension/format (e.g. pdf, docx, txt).",
    )
    quarantine_status: Literal["all", "clean", "quarantined", "pending"] = Field(
        default="all",
        description="Filter by quarantine verification status.",
    )
    health_status: Literal["all", "healthy", "warnings", "critical"] = Field(
        default="all",
        description="Filter by health and ingest integrity status.",
    )
