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


# Words that reveal how an answer was built; customers never see them. This
# is the "never use" list shown to the model only: the post-filter in
# app.harness.tone matches narrower first-person source references, because
# bare "the documents" / "knowledge base" are also ordinary product words.
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

# What a customer is told when something is not covered: one natural line
# that hands over to a human. Used by the tone post-filter.
NOT_COVERED_SENTENCE = (
    "I can't see that detail, so it's best to check with one of our specialists."
)
NOT_COVERED_REPLY = NOT_COVERED_SENTENCE

_TONE_BLOCK = (
    "Style: write like a helpful colleague. Answer in the first sentence, in "
    'plain words; never open with "We" plus a corporate verb (deliver, '
    "implement, provide). Use contractions and short sentences. A simple "
    "fact gets 1-2 sentences, no headings or bullets; bullets only for 3+ "
    "parallel items. Never write paragraphs of more than 2 sentences, and "
    'never write "Based on", "Next step:", "Key points" or any form-style '
    "label, or repeat a sentence or a closing. Offer at most one next step, "
    'once, only when it helps. Say "we" for {brand} and "you" for the '
    "reader, never the product name. Use commas or full stops, never "
    'dashes; Australian spelling (programme); no jargon (say "a person '
    'approves it"); no raw HTML, no links, no code fences '
    "unless the facts quote code. Keep any citation marker at the end of the "
    "claim it supports, including inside bullets and table cells. If "
    "something isn't covered, say so once, naturally, and offer a "
    "specialist. Never use these phrases: {banned}."
)

# One format instruction per AnswerShape value; only the detected one is sent.
_SHAPE_BLOCKS: dict[str, str] = {
    "definition": (
        "Format: 1-3 short, plain sentences that answer directly. Add 3 or "
        "more bullets only if the facts are really a list."
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
        "Format: the direct answer in one sentence (yes, no, or the fact), "
        "only if the facts state it, then at most one supporting sentence. "
        "If the facts don't state it, say what we do and point to a "
        "specialist."
    ),
    "partial": (
        "Format: say what we do in 1-2 sentences, then one short natural "
        "sentence that the rest is best checked with a specialist. Don't "
        "open with what we can't confirm and don't use a label."
    ),
    "out_of_scope": (
        "Format: one warm line saying that is outside what we cover here, "
        "then offer a chat with one of our specialists in natural words. "
        "Nothing else."
    ),
    "general": (
        "Format: 1-3 short, plain sentences; add 3 or more bullets only if "
        "the facts are really a list."
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
    ``shape``, with a leading space ("" for ``current``). Any next step is
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
