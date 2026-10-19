"""Clarifying questions for genuinely ambiguous queries (outcome ``clarify``).

Two cheap gates decide whether the model may ask instead of answer, and the
model makes the final call in the same generation request (no extra LLM call):

1. ``plan_clarification`` (rules, no I/O): only a short, underspecified,
   standalone question whose retrieved evidence spreads over at least
   ``clarify_min_options`` distinct topics (document + section) with similar
   scores qualifies, and never more than ``clarify_max_consecutive`` times in a
   row in one session. Greetings, blocked and off-topic queries never get here
   (they are handled before retrieval / by the relevance gate).
2. The answer prompt then lists those topics; the model answers normally
   unless the question really is ambiguous, in which case it replies with one
   ``CLARIFY: {...}`` line naming 2-4 of the listed topic ids.

Options are always the retrieved topics (document title / section heading),
never invented: the model may only shorten a label for an id it was given.
The reply to a clarification (a picked option, its number, its text, or a
short free-text refinement) is folded back into the original question by
``resolve_clarification_reply`` so the next answer uses both.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.core.config import get_settings
from app.guardrails.conversational import detect_conversational_intent
from app.retrieval.retriever import decompose_query
from app.schemas.query import (
    ChatTurn,
    Clarification,
    ClarificationChoice,
    ClarificationOption,
    SourceChunk,
)

CLARIFY_OUTCOME = "clarify"
CLARIFY_MARKER = "CLARIFY:"
DEFAULT_QUESTION = "Which of these do you mean?"
REPLY_HINT = (
    "Reply with the number or name of an option, or tell me more about "
    "what you're looking for."
)

_MAX_LABEL_CHARS = 80
_MAX_QUESTION_CHARS = 300
_MAX_QUERY_CHARS = 2000
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'&+-]*")
_MARKER_RE = re.compile(r"^\W*CLARIFY\s*:", re.IGNORECASE)
_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "can",
        "do",
        "does",
        "for",
        "how",
        "i",
        "in",
        "is",
        "me",
        "my",
        "of",
        "on",
        "or",
        "please",
        # compose_query's own connective, so a resolved reply is not re-counted
        "specifically",
        "tell",
        "the",
        "to",
        "we",
        "what",
        "whats",
        "what's",
        "which",
        "who",
        "why",
        "with",
        "you",
        "your",
    }
)


@dataclass(frozen=True, slots=True)
class ClarifyCandidate:
    """A distinct retrieved topic the question could be about."""

    id: str
    label: str
    document_id: str
    source_name: str | None
    section: str | None
    score: float

    def to_option(self, label: str | None = None) -> ClarificationOption:
        return ClarificationOption(
            id=self.id,
            label=label or self.label,
            document_id=self.document_id,
            source_name=self.source_name,
            section=self.section,
        )


@dataclass(frozen=True, slots=True)
class ResolvedReply:
    """A reply to a clarification, folded into a standalone question."""

    query: str
    original_query: str
    label: str
    option_id: str | None
    free_text: bool

    def as_details(self) -> dict[str, object]:
        return {
            "original_query": self.original_query,
            "option_id": self.option_id,
            "label": self.label,
            "free_text": self.free_text,
        }


def content_word_count(query: str) -> int:
    """Words that carry meaning (stopwords such as "what is the" excluded)."""
    words = (w.lower() for w in _WORD_RE.findall(query))
    return sum(1 for w in words if w not in _STOPWORDS)


def clarification_streak(history: list[ChatTurn] | None) -> int:
    """How many of the latest assistant turns in a row were clarifications."""
    streak = 0
    for turn in reversed(history or []):
        if turn.role != "assistant":
            continue
        if turn.outcome != CLARIFY_OUTCOME:
            break
        streak += 1
    return streak


def _humanize(name: str) -> str:
    stem = PurePosixPath(name.replace("\\", "/")).name
    stem = re.sub(r"\.(md|markdown|txt|pdf|docx?|html?)$", "", stem, flags=re.I)
    text = re.sub(r"[_\-]+", " ", stem).strip()
    if text and text == text.lower():
        text = text[:1].upper() + text[1:]
    return text or name


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _metadata_text(metadata: dict, *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def select_candidates(sources: list[SourceChunk]) -> list[ClarifyCandidate]:
    """Distinct topics (document + section) scoring close to the best one."""
    settings = get_settings()
    if not sources:
        return []
    ranked = sorted(sources, key=lambda s: s.score, reverse=True)
    floor = max(ranked[0].score - settings.clarify_score_margin, 0.0)

    picked: list[ClarifyCandidate] = []
    seen_topics: set[tuple[str, str]] = set()
    seen_labels: set[str] = set()
    for source in ranked:
        if source.score < floor or len(picked) >= settings.clarify_max_options:
            break
        metadata = source.metadata or {}
        source_name = _metadata_text(metadata, "source_name", "file_name", "title")
        section = _metadata_text(metadata, "section_heading", "heading")
        topic = (source.document_id, (section or "").lower())
        if topic in seen_topics:
            continue
        seen_topics.add(topic)
        doc_label = _humanize(source_name) if source_name else None
        label = section or doc_label
        if not label:
            continue
        if label.lower() in seen_labels and doc_label and section:
            label = f"{section} ({doc_label})"
        label = _clip(label, _MAX_LABEL_CHARS)
        if label.lower() in seen_labels:
            continue
        seen_labels.add(label.lower())
        picked.append(
            ClarifyCandidate(
                id=f"T{len(picked) + 1}",
                label=label,
                document_id=source.document_id,
                source_name=source_name,
                section=section,
                score=source.score,
            )
        )
    return picked


def plan_clarification(
    query: str,
    sources: list[SourceChunk],
    history: list[ChatTurn] | None,
    *,
    follow_up: bool,
) -> list[ClarifyCandidate]:
    """Topics to offer the model, or ``[]`` when the query must just be answered."""
    settings = get_settings()
    if not settings.clarify_enabled:
        return []
    if clarification_streak(history) >= settings.clarify_max_consecutive:
        return []
    # A follow-up is resolved by the conversation, not by asking again.
    if follow_up and history:
        return []
    if content_word_count(query) > settings.clarify_max_query_words:
        return []
    if len(decompose_query(query)) > 1:
        return []
    candidates = select_candidates(sources)
    return candidates if len(candidates) >= settings.clarify_min_options else []


def clarify_instruction(candidates: list[ClarifyCandidate]) -> str:
    """Prompt block letting the model ask instead of answer (see module doc)."""
    topics = "\n".join(f"{c.id}: {c.label}" for c in candidates)
    return (
        "The question is short and the context covers several distinct topics:\n"
        f"{topics}\n"
        "If the question clearly fits one topic, or one short answer covers "
        "them, answer normally. Only if it is genuinely ambiguous between "
        "these topics, do not answer; reply with exactly one line:\n"
        'CLARIFY: {"question": "<one short question to the visitor>", '
        '"options": [{"id": "<topic id>", "label": "<2-6 word label>"}]}\n'
        "using 2-4 of the topic ids above.\n\n"
    )


def is_clarification_reply(answer: str) -> bool:
    return bool(_MARKER_RE.match(answer.strip()))


def _parse_payload(answer: str) -> dict:
    body = _MARKER_RE.sub("", answer.strip(), count=1)
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        payload = json.loads(body[start : end + 1])
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _option_refs(raw: object) -> list[tuple[str, str | None]]:
    refs: list[tuple[str, str | None]] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            refs.append((item.strip(), None))
        elif isinstance(item, dict) and isinstance(item.get("id"), str):
            label = item.get("label")
            refs.append((item["id"].strip(), label if isinstance(label, str) else None))
    return refs


def parse_clarification(
    answer: str, candidates: list[ClarifyCandidate], original_query: str
) -> Clarification | None:
    """Turn a ``CLARIFY:`` reply into a validated clarification.

    Unknown ids are dropped and labels can only shorten a retrieved topic. A
    malformed payload falls back to every candidate topic (the model did say
    the question is ambiguous). ``None`` when the reply is a normal answer.
    """
    if not is_clarification_reply(answer):
        return None
    settings = get_settings()
    by_id = {c.id: c for c in candidates}
    payload = _parse_payload(answer)

    options: list[ClarificationOption] = []
    for option_id, label in _option_refs(payload.get("options")):
        candidate = by_id.get(option_id.upper())
        if candidate is None or any(o.id == candidate.id for o in options):
            continue
        clean = _clip(label, _MAX_LABEL_CHARS) if label and label.strip() else None
        options.append(candidate.to_option(clean))
    if len(options) < settings.clarify_min_options:
        options = [c.to_option() for c in candidates]
    options = options[: settings.clarify_max_options]
    if len(options) < settings.clarify_min_options:
        return None

    question = payload.get("question")
    question = (
        _clip(question, _MAX_QUESTION_CHARS)
        if isinstance(question, str) and question.strip()
        else DEFAULT_QUESTION
    )
    return Clarification(
        question=question,
        options=options,
        original_query=original_query[:_MAX_QUERY_CHARS],
    )


def render_clarification_text(clarification: Clarification) -> str:
    """Plain-text reply for clients that ignore the structured options."""
    lines = [
        f"{i}. {option.label}" for i, option in enumerate(clarification.options, 1)
    ]
    return f"{clarification.question}\n\n" + "\n".join(lines) + f"\n\n{REPLY_HINT}"


def compose_query(original_query: str, refinement: str) -> str:
    """Standalone question from the original one plus the chosen meaning."""
    suffix = f" (specifically: {refinement.strip()})"
    room = max(_MAX_QUERY_CHARS - len(suffix), 0)
    return original_query.strip()[:room] + suffix


def _last_clarification(history: list[ChatTurn] | None) -> Clarification | None:
    for turn in reversed(history or []):
        if turn.role == "assistant":
            if turn.outcome == CLARIFY_OUTCOME:
                return turn.clarification
            return None
    return None


def _match_option(
    text: str, clarification: Clarification
) -> ClarificationOption | None:
    cleaned = text.strip().strip(".!?)").strip().lower()
    if cleaned.isdigit():
        index = int(cleaned) - 1
        if 0 <= index < len(clarification.options):
            return clarification.options[index]
        return None
    for option in clarification.options:
        if cleaned in (option.label.lower(), option.id.lower()):
            return option
    return None


def resolve_clarification_reply(
    query: str,
    history: list[ChatTurn] | None,
    choice: ClarificationChoice | None = None,
) -> ResolvedReply | None:
    """Fold a reply to the last clarifying question into a standalone question.

    ``choice`` (sent by clients that render the options) wins; otherwise the
    plain ``query`` is matched by option number, label or id. A short
    free-text reply refines the original question too. ``None`` when the
    previous turn was not a clarification and no choice was sent.
    """
    last = _last_clarification(history)
    if choice is not None:
        stored = next(
            (o for o in (last.options if last else []) if o.id == choice.option_id),
            None,
        )
        original = last.original_query if stored and last else choice.original_query
        label = stored.label if stored else choice.option_label
        return ResolvedReply(
            query=compose_query(original, label),
            original_query=original,
            label=label,
            option_id=stored.id if stored else choice.option_id,
            free_text=False,
        )
    if last is None:
        return None
    option = _match_option(query, last)
    if option is not None:
        return ResolvedReply(
            query=compose_query(last.original_query, option.label),
            original_query=last.original_query,
            label=option.label,
            option_id=option.id,
            free_text=False,
        )
    settings = get_settings()
    # A new question ("what is E8?") is answered as such, not folded in.
    if (
        not query.strip().endswith("?")
        and detect_conversational_intent(query) is None
        and 0 < content_word_count(query) <= settings.clarify_max_query_words
    ):
        return ResolvedReply(
            query=compose_query(last.original_query, query),
            original_query=last.original_query,
            label=query.strip()[:_MAX_LABEL_CHARS],
            option_id=None,
            free_text=True,
        )
    return None
