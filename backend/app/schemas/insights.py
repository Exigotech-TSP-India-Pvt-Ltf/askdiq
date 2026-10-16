"""Request and response models for ``/admin/insights/*`` (newplan 6.3, 6.4).

``InsightsFilters`` is the one shared filter model; every value is validated
against an allow-list (enums, code patterns, bounded text) before it reaches
SQL, and every value is passed to SQL as a bind parameter.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.insights.taxonomy import TaxonomyKind

CODE_PATTERN = r"^[A-Z][A-Z0-9_]{0,31}$"
PERSON_PATTERN = r"^[0-9a-f]{16}$"
TZ_PATTERN = r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$"
COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"
MAX_CUSTOM_RANGE_DAYS = 731
MAX_SEARCH_CHARS = 200
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
MAX_OFFSET = 10_000


class Period(StrEnum):
    ALL = "all"
    LAST_7_DAYS = "7d"
    LAST_30_DAYS = "30d"
    CUSTOM = "custom"


class ConversationOutcome(StrEnum):
    SERVED = "Served"
    PARTLY_SERVED = "Partly served"
    NOT_SERVED = "Not served"
    GREETING_ONLY = "Greeting only"


class ExperienceRating(StrEnum):
    GREAT = "Great"
    GOOD = "Good"
    SERVED_WITH_FRICTION = "Served with friction"
    PARTLY_SERVED = "Partly served"
    DROPPED = "Dropped"
    NO_QUESTION = "No question asked"


class Audience(StrEnum):
    OVERVIEW = "overview"
    USERS = "users"
    TESTING = "testing"


class ConversationSort(StrEnum):
    CONV_ID = "conv_id"
    PERSON = "person"
    START = "start"
    USER_TYPE = "user_type"
    OUTCOME = "outcome"
    EXPERIENCE_RATING = "experience_rating"
    ISSUE = "issue"
    EXCHANGES = "exchanges"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


@lru_cache(maxsize=1)
def _known_zones() -> frozenset[str]:
    from zoneinfo import available_timezones  # noqa: PLC0415 - loads the tz database once

    return frozenset(available_timezones())


def validate_timezone(value: str) -> str:
    """IANA zone name; checked against the tz database when one is installed."""
    zones = _known_zones()
    if zones and value not in zones:
        raise ValueError("Unknown time zone.")
    return value


class InsightsFilters(BaseModel):
    """Shared filters (``passConv()`` and ``scope()`` of the reference HTML)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    period: Period = Period.ALL
    date_from: date | None = None
    date_to: date | None = None
    user_type: str | None = Field(default=None, min_length=1, max_length=64)
    outcome: ConversationOutcome | None = None
    rating: ExperienceRating | None = None
    issue: str | None = Field(default=None, pattern=CODE_PATTERN)
    intent: str | None = Field(default=None, pattern=CODE_PATTERN)
    quality: str | None = Field(default=None, pattern=CODE_PATTERN)
    person: str | None = Field(default=None, pattern=PERSON_PATTERN)
    q: str | None = Field(default=None, max_length=MAX_SEARCH_CHARS)
    audience: Audience = Audience.OVERVIEW
    tz: str = Field(min_length=1, max_length=64, pattern=TZ_PATTERN)

    @field_validator(
        "user_type", "issue", "intent", "quality", "person", "q", mode="before"
    )
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("q")
    @classmethod
    def _no_control_chars(cls, value: str | None) -> str | None:
        if value is not None and any(ord(ch) < 32 for ch in value):  # noqa: PLR2004 - ASCII control range
            raise ValueError("Search text contains control characters.")
        return value

    @field_validator("tz")
    @classmethod
    def _known_tz(cls, value: str) -> str:
        return validate_timezone(value)

    @model_validator(mode="after")
    def _check_range(self) -> InsightsFilters:
        if self.period is Period.CUSTOM:
            if self.date_from is None or self.date_to is None:
                raise ValueError("A custom period needs both 'from' and 'to'.")
            if self.date_from > self.date_to:
                raise ValueError("'from' must not be after 'to'.")
            if (self.date_to - self.date_from).days >= MAX_CUSTOM_RANGE_DAYS:
                raise ValueError(
                    f"A custom period spans at most {MAX_CUSTOM_RANGE_DAYS} days."
                )
        elif self.date_from is not None or self.date_to is not None:
            raise ValueError("'from' and 'to' are only allowed with period=custom.")
        return self

    @property
    def has_conversation_filters(self) -> bool:
        """Filters that need conversation-level fields (not in the rollup)."""
        return any(
            value is not None
            for value in (
                self.user_type,
                self.outcome,
                self.rating,
                self.issue,
                self.person,
                self.q,
            )
        )


class ConversationPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sort: ConversationSort = ConversationSort.START
    direction: SortDirection = SortDirection.DESC
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0, le=MAX_OFFSET)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class DateRange(BaseModel):
    date_from: date | None = Field(serialization_alias="from")
    date_to: date | None = Field(serialization_alias="to")


class InsightsSummary(BaseModel):
    """KPI strip (``metrics()``)."""

    range: DateRange
    conversations: int
    exchanges: int = Field(description="All exchanges in scope, greetings included.")
    handled_exchanges: int
    substantive_questions: int
    handled_questions: int
    handled_pct: float | None = Field(description="Handled / substantive x 100.")
    served_conversations: int
    active_conversations: int = Field(description="Conversations not Greeting only.")
    served_pct: float | None = Field(description="Served / active x 100.")
    people: int = Field(description="Distinct probable people.")
    avg_questions_per_conversation: float | None
    pending_classification: int
    source: Literal["rollup", "live"] = Field(
        description="Where the exchange counts came from."
    )


class TrendDay(BaseModel):
    day: date
    questions: int
    conversations: int


class InsightsTrend(BaseModel):
    range: DateRange
    days: list[TrendDay]
    source: Literal["rollup", "live"]


class InsightsHourly(BaseModel):
    tz: str
    hours: list[int] = Field(min_length=24, max_length=24)
    total: int
    peak_hour: int | None


class FlowLink(BaseModel):
    user_type: str
    outcome: ConversationOutcome
    conversations: int


class FlowNode(BaseModel):
    name: str
    conversations: int


class InsightsFlow(BaseModel):
    total: int
    user_types: list[FlowNode]
    outcomes: list[FlowNode]
    links: list[FlowLink]


class TopicItem(BaseModel):
    intent: str
    label: str
    group: str | None
    questions: int
    handled: int
    handled_pct: float


class InsightsTopics(BaseModel):
    total: int
    items: list[TopicItem]
    source: Literal["rollup", "live"]


class QualityItem(BaseModel):
    quality: str
    label: str
    color: str | None
    questions: int
    pct: float


class InsightsQuality(BaseModel):
    total: int
    items: list[QualityItem]
    source: Literal["rollup", "live"]


class DropoffItem(BaseModel):
    issue: str
    label: str
    conversations: int


class InsightsDropoff(BaseModel):
    total: int
    items: list[DropoffItem]


class PersonItem(BaseModel):
    person: str = Field(description="Display label, P01... per result set.")
    person_id: str = Field(description="Stable pseudonym (salted hash), no PII.")
    conversations: int
    dominant_outcome: ConversationOutcome
    outcomes: dict[str, int]


class InsightsPeople(BaseModel):
    total: int
    items: list[PersonItem]


class ConversationRow(BaseModel):
    conv_id: uuid.UUID
    person: str | None
    person_id: str
    start: datetime
    end: datetime
    minutes: float
    user_type: str
    outcome: ConversationOutcome
    experience_rating: ExperienceRating
    issue: str | None
    issue_label: str | None
    failing_question: str | None
    exchanges: int
    substantive_exchanges: int


class ConversationList(BaseModel):
    items: list[ConversationRow]
    total: int
    limit: int
    offset: int
    sort: ConversationSort
    direction: SortDirection


class TurnDetail(BaseModel):
    turn: int
    timestamp: datetime
    user_message: str
    intent: str | None
    intent_label: str | None
    substantive: bool | None
    handled: bool | None
    quality: str | None
    quality_label: str | None
    issue: str | None
    issue_label: str | None
    friction: str | None
    classified_by: str | None
    pending: bool


class ConversationDetail(BaseModel):
    conversation: ConversationRow | None
    turns: list[TurnDetail]


class PendingClassification(BaseModel):
    pending: int
    labelled: int
    total: int
    progress_pct: float | None


# ---------------------------------------------------------------------------
# Taxonomy (F7)
# ---------------------------------------------------------------------------


class TaxonomyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: TaxonomyKind
    code: str
    label: str
    description: str | None
    color: str | None
    group: str | None
    substantive: bool
    handled: bool
    sort_order: int
    is_active: bool
    is_system: bool
    created_at: datetime
    updated_at: datetime


class TaxonomyList(BaseModel):
    items: list[TaxonomyRead]


class _TaxonomyFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    color: str | None = Field(default=None, pattern=COLOR_PATTERN)
    group: str | None = Field(default=None, min_length=1, max_length=64)
    sort_order: int = Field(default=0, ge=0, le=10_000)


class TaxonomyCreate(_TaxonomyFields):
    kind: TaxonomyKind
    code: str = Field(pattern=CODE_PATTERN, description="Immutable once created.")
    substantive: bool = True
    handled: bool = False


class TaxonomyUpdate(BaseModel):
    """Editable fields only: the code and kind are immutable (stable labels)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    color: str | None = Field(default=None, pattern=COLOR_PATTERN)
    group: str | None = Field(default=None, min_length=1, max_length=64)
    sort_order: int | None = Field(default=None, ge=0, le=10_000)
    substantive: bool | None = Field(
        default=None, description="Intent counts as a substantive question."
    )
    handled: bool | None = Field(
        default=None, description="Bot-quality value counts as handled."
    )

    @model_validator(mode="after")
    def _not_empty(self) -> TaxonomyUpdate:
        if not self.model_fields_set:
            raise ValueError("Nothing to update.")
        for name in ("label", "sort_order", "substantive", "handled"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null.")
        return self
