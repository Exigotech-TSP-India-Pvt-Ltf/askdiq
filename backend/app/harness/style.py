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

if TYPE_CHECKING:
    from app.harness.answer_shape import AnswerShape

_BUCKETS = 100


class ResponseStyle(StrEnum):
    CURRENT = "current"
    ADAPTIVE = "adaptive"


# Words that reveal how an answer was built; customers never see them.
INTERNAL_WORDING: tuple[str, ...] = (
    "supplied context",
    "provided context",
    "the context",
    "the documents",
    "knowledge base",
    "provided information",
)

# Stock AI phrases the answer must never contain (owner's banned list).
BANNED_PHRASES: tuple[str, ...] = (
    "As an AI",
    "The provided context does not state",
    "Based on the information provided",
    "According to the documents",
    "I hope this helps",
    "Feel free to reach out",
    "Great question!",
    "Delve",
    "It's important to note",
    "Comprehensive",
    "Leverage",
    "Seamless",
    "Robust",
    "In today's evolving landscape",
    "I'm sorry, but I cannot",
    "Certainly!",
    *INTERNAL_WORDING,
    "materials",
)

# What a customer is told when something is not covered: said once, plainly,
# then a human next step. Used by the tone post-filter.
NOT_COVERED_SENTENCE = "We don't have details on that here."
NOT_COVERED_REPLY = (
    f"{NOT_COVERED_SENTENCE}\n\n"
    "Next step: talk to one of our specialists, who will be happy to help."
)

_TONE_BLOCK = (
    "Style: answer in the first sentence; no warm-up and no restating the "
    "question. Use plain words; contractions are fine. Prefer specifics "
    '(numbers, names) over vague words ("eight strategies", not "various '
    'controls"). Say "we" for {brand} and "you" for the reader. Never write '
    "paragraphs: more than 2 sentences of unbroken prose must become "
    "bullets. Format with Markdown only: ## headings, - bullets, 1. steps, "
    "**bold**, and a pipe table only when the format below asks for one; no "
    "raw HTML, no links, no code fences unless the facts quote code. Keep "
    "any citation marker at the end of the claim it supports, including "
    "inside bullets and table cells. If part of the question isn't covered, "
    "say so once, plainly (for example \"We don't have details on that "
    'here"), never say where your facts come from, offer to connect the '
    "reader with one of our specialists, and never borrow a fact from a "
    "neighbouring service. Never use these phrases: {banned}."
)

# One format instruction per AnswerShape value; only the detected one is sent.
_SHAPE_BLOCKS: dict[str, str] = {
    "definition": (
        "Format: a 1-2 sentence lead that answers directly, then a ## heading "
        "with 2-4 bullets, each starting with a **bold** key term."
    ),
    "how_to": (
        "Format: one lead sentence, then numbered steps (1., 2., 3.), one "
        "action per step, in order, at most 7 steps."
    ),
    "comparison": (
        "Format: one lead sentence with the main difference, then a Markdown "
        "pipe table with a header row, a separator row (| --- |), one row per "
        "aspect and one column per option, at most 5 columns, short cells. "
        "Leave out any aspect the facts don't cover."
    ),
    "quick_fact": (
        "Format: a one-line direct answer (yes, no, or the fact) of at most "
        "25 words, only if the facts state it; otherwise state what we do "
        "and point to a specialist. Then 2-3 bullets with the supporting "
        "detail."
    ),
    "partial": (
        "Format: lead with what we do (1-2 sentences or bullets), then one "
        'short scope note if needed ("X stays with your Y"), then a last '
        'line starting "Next step:" with a contact action (talk to one of '
        "our specialists to scope this for you). Never open with what we "
        "can't confirm."
    ),
    "out_of_scope": (
        "Format: one warm line saying that is outside what we cover here, "
        'then a line starting "Next step:" to talk to one of our '
        "specialists. Nothing else."
    ),
    "general": (
        "Format: a 1-2 sentence lead, then a ## heading with 2-4 bullets; "
        "never more than 2 sentences of unbroken prose."
    ),
}

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
    ``shape``, with a leading space ("" for ``current``). A Next step is
    always a contact action, never an internal one."""

    profile = profile_for(style)
    if not profile.structured:
        return ""
    settings = get_settings()
    tone = profile.tone_block.format(
        brand=settings.brand_name,
        banned="; ".join(f'"{p}"' for p in BANNED_PHRASES),
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
