"""Centralized outcome resolution for chat message exchanges (Task 1.1).

Defines the canonical Outcome enum, generation outcome subset, and the
deterministic derive_outcome(...) function to map runtime signals and legacy
data into a single source of truth.
"""

from typing import Any, Literal

from app.guardrails.conversational import detect_conversational_intent

Outcome = Literal[
    "answered", "ungrounded", "blocked", "conversational", "llm_failed", "clarify"
]

ALL_OUTCOMES: tuple[Outcome, ...] = (
    "answered",
    "ungrounded",
    "blocked",
    "conversational",
    "llm_failed",
    # The assistant asked a clarifying question with options instead of
    # answering (app.harness.clarify). Not a generation outcome: it is not an
    # answer, so it stays out of the groundedness denominator.
    "clarify",
)

# Outcomes that reached the generation phase (used for groundedness denominator)
GENERATION_OUTCOMES: tuple[Outcome, ...] = ("answered", "ungrounded", "llm_failed")

# Legacy mapping for backwards-compatibility
LEGACY_OUTCOME_MAP: dict[str, Outcome] = {
    "fallback": "ungrounded",
    "empty_llm_output": "llm_failed",
    "answered": "answered",
    "ungrounded": "ungrounded",
    "blocked": "blocked",
    "conversational": "conversational",
    "llm_failed": "llm_failed",
    "clarify": "clarify",
}


def derive_outcome(
    *,
    outcome_hint: str | None = None,
    guardrail_flags: list[str] | None = None,
    grounded: bool | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,  # noqa: ARG001 - public kwarg, callers pass it
    query: str | None = None,
    answer: str | None = None,
    token_details: dict[str, Any] | None = None,
) -> Outcome:
    """Derive the canonical turn Outcome from runtime signals and database columns.

    Priority:
    1. Conversational intent / canned smalltalk detection -> 'conversational'
    2. Explicit failure / empty output -> 'llm_failed'
       Explicit clarifying question hint -> 'clarify'
    3. Blocked pre-retrieval / guardrails with no generation -> 'blocked'
    4. Explicit ungrounded or grounded=False -> 'ungrounded'
    5. Reached generation and grounded -> 'answered'
    """
    flags = guardrail_flags or []
    normalized_hint = None
    if outcome_hint:
        normalized_hint = LEGACY_OUTCOME_MAP.get(
            outcome_hint.lower(), outcome_hint.lower()
        )
    elif token_details and isinstance(token_details, dict):
        hint_in_details = token_details.get("outcome")
        if hint_in_details:
            normalized_hint = LEGACY_OUTCOME_MAP.get(
                str(hint_in_details).lower(), str(hint_in_details).lower()
            )

    # 1. Conversational small-talk check
    if normalized_hint == "conversational":
        return "conversational"

    if query and detect_conversational_intent(query) is not None:
        return "conversational"

    if answer:
        ans_lower = answer.strip().lower()
        if (
            ans_lower.startswith("hello! i'm the")
            or ans_lower.startswith("you're welcome! let me know")
            or ans_lower.startswith("goodbye! feel free to come back")
            or "ask me anything about" in ans_lower
        ):
            return "conversational"

    # 2. LLM failure / empty output
    if normalized_hint in ("llm_failed", "empty_llm_output"):
        return "llm_failed"

    # 2b. Clarifying question with options (only ever set explicitly)
    if normalized_hint == "clarify":
        return "clarify"

    # 3. Guardrail blocked pre-generation or no generation tokens
    has_zero_prompt = prompt_tokens is None or prompt_tokens == 0
    if normalized_hint == "blocked":
        return "blocked"

    if has_zero_prompt:
        if flags or grounded is False:
            return "blocked"
        if answer and ("off-topic" in answer.lower() or "blocked" in answer.lower()):
            return "blocked"

    # 4. Ungrounded check
    if normalized_hint in {"ungrounded", "fallback"}:
        return "ungrounded"

    if grounded is False:
        return "ungrounded"

    # 5. Default answered if tokens exist or outcome reached generation
    if normalized_hint == "answered":
        return "answered"

    return "answered"
