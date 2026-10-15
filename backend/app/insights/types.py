"""Value objects passed between the insights classifier stages."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.insights.taxonomy import ClassifiedBy


@dataclass(frozen=True, slots=True)
class TurnInput:
    """One exchange: the user message and the assistant reply that answered it.

    ``message_id`` is the assistant message (labels live on it).
    """

    message_id: uuid.UUID
    session_id: uuid.UUID
    user_text: str
    answer_text: str
    outcome: str | None
    grounded: bool | None
    guardrail_flags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class TurnLabel:
    message_id: uuid.UUID
    session_id: uuid.UUID
    intent: str
    bot_quality: str
    issue: str | None
    substantive: bool
    handled: bool
    friction: str | None
    confidence: float
    classified_by: ClassifiedBy
    model: str | None = None


@dataclass(frozen=True, slots=True)
class LlmUsage:
    model: str
    turns: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    reasoning_tokens: int = 0
    cached_tokens: int = 0
