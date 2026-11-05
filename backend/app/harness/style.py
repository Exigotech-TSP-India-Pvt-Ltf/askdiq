"""Response style profiles (OR-8 "Adaptive structure").

``current`` is the original prompt, unchanged. ``adaptive`` adds one shared
tone block and the format instruction for the detected answer shape only
(see app.harness.answer_shape), so the system prompt stays short.

Every block here is a fixed literal: none is built from document text or
from the question (OWASP LLM01), and the shape is chosen by rules, never by
the model. The variant is picked per session with a deterministic bucket, so
a conversation never switches style mid-thread.
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.harness.prompt_catalog import (
    BANNED_PHRASES,
    INTERNAL_WORDING,
    SHAPE_BLOCKS,
    TONE_BLOCK,
)

if TYPE_CHECKING:
    from app.harness.answer_shape import AnswerShape

_BUCKETS = 100


class ResponseStyle(StrEnum):
    CURRENT = "current"
    ADAPTIVE = "adaptive"


# Prompt text lives in app.harness.prompt_catalog (one place); re-exported
# here for existing importers. The post-filter in app.harness.tone matches
# narrower first-person source references, because bare "the documents" /
# "knowledge base" are also ordinary product words.

# What a customer is told when something is not covered: one natural line
# that hands over to a human. Three variants, picked by a stable hash of the
# question so a given question always gets the same wording.
NOT_COVERED_VARIANTS: tuple[str, ...] = (
    "I don't have that detail, but one of our specialists can help.",
    "That's one for our specialists, who can give you a proper answer.",
    "I'm not sure about that one, so it's best to ask our specialists.",
)
NOT_COVERED_SENTENCE = NOT_COVERED_VARIANTS[0]
NOT_COVERED_REPLY = NOT_COVERED_SENTENCE


def not_covered_sentence(question: str | None = None) -> str:
    """The not-covered sentence for ``question`` (deterministic, sha256 based)."""

    key = (question or "").strip().lower()
    if not key:
        return NOT_COVERED_SENTENCE
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return NOT_COVERED_VARIANTS[digest[0] % len(NOT_COVERED_VARIANTS)]


# Format and length only: grounding and refusal rules live in
# prompt_catalog.common_rules() and are never restated here.
_TONE_BLOCK = TONE_BLOCK
# One format instruction per AnswerShape value; only the detected one is sent.
_SHAPE_BLOCKS: dict[str, str] = SHAPE_BLOCKS

# Steps and tables need more room than the default word cap.
_LONG_SHAPES = frozenset({"how_to", "comparison"})


@dataclass(frozen=True, slots=True)
class StyleProfile:
    style: ResponseStyle
    tone_block: str = ""
    shape_blocks: dict[str, str] = field(default_factory=dict)

    @property
    def structured(self) -> bool:
        return bool(self.shape_blocks)


STYLE_PROFILES: dict[ResponseStyle, StyleProfile] = {
    ResponseStyle.CURRENT: StyleProfile(ResponseStyle.CURRENT),
    ResponseStyle.ADAPTIVE: StyleProfile(
        ResponseStyle.ADAPTIVE, _TONE_BLOCK, _SHAPE_BLOCKS
    ),
}


def profile_for(style: ResponseStyle | str | None) -> StyleProfile:
    try:
        return STYLE_PROFILES[ResponseStyle(style or ResponseStyle.CURRENT)]
    except ValueError:
        return STYLE_PROFILES[ResponseStyle.CURRENT]


def style_instructions(
    style: ResponseStyle | str | None, shape: AnswerShape | str | None
) -> str:
    """System-prompt suffix: the tone block plus the one format block for
    ``shape``, with a leading space ("" for ``current``). Any next step is
    always a contact action, never an internal one."""

    profile = profile_for(style)
    if not profile.structured:
        return ""
    settings = get_settings()
    tone = profile.tone_block.format(
        brand=settings.brand_name,
        # Internal wording is already banned by the shared rules.
        banned="; ".join(f'"{p}"' for p in BANNED_PHRASES if p not in INTERNAL_WORDING),
    )
    block = profile.shape_blocks.get(str(shape or "general"))
    return f" {tone} {block or profile.shape_blocks['general']}"


def word_cap(style: ResponseStyle | str | None, shape: AnswerShape | str | None) -> int:
    settings = get_settings()
    if profile_for(style).structured and str(shape) in _LONG_SHAPES:
        return settings.answer_max_words_long
    return settings.answer_max_words


def bucket(key: str) -> int:
    """Stable 0-99 bucket for a session id (sha256, not Python's salted hash)."""

    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % _BUCKETS


def resolve_style(bucket_key: str | None) -> ResponseStyle:
    """The style for this conversation: B for ``pct``% of sessions, else A."""

    settings = get_settings()
    style = ResponseStyle(settings.response_style)
    pct = settings.response_style_b_pct
    if bucket_key and pct > 0 and bucket(bucket_key) < pct:
        return ResponseStyle(settings.response_style_b)
    return style


# The style and shape of the answer being generated in this request. Set once
# by the orchestrator and read by app.harness.prompts.call_llm, so the LLM
# call signature (and every test double of it) stays unchanged.
_ACTIVE: ContextVar[tuple[ResponseStyle, str] | None] = ContextVar(
    "answer_style", default=None
)


def use_style(style: ResponseStyle, shape: AnswerShape | str) -> None:
    _ACTIVE.set((style, str(shape)))


def active_style() -> tuple[ResponseStyle | None, str | None]:
    current = _ACTIVE.get()
    return current if current is not None else (None, None)
