"""Hybrid classifier core: rule stage, then batched LLM calls under a budget.

No database access here; persistence, locking and the budget window live in
``app.insights.service``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from app.core.logging_config import get_logger
from app.insights.llm import (
    ClassifierLLM,
    azure_classifier_llm,
    build_messages,
    build_response_schema,
    classifier_model,
    estimate_tokens,
    max_completion_tokens,
    validate_labels,
)
from app.insights.rules import classify_by_rule
from app.insights.taxonomy import Taxonomy
from app.insights.types import LlmUsage, TurnInput, TurnLabel

logger = get_logger(__name__)


@dataclass
class ClassificationResult:
    labels: list[TurnLabel] = field(default_factory=list)
    usage: list[LlmUsage] = field(default_factory=list)
    # Turns left unclassified because the token budget ran out.
    deferred: list[TurnInput] = field(default_factory=list)

    @property
    def tokens_used(self) -> int:
        return sum(u.total_tokens for u in self.usage)


def split_by_rule(
    turns: Sequence[TurnInput], taxonomy: Taxonomy
) -> tuple[list[TurnLabel], list[TurnInput]]:
    """Return (rule labels, turns that still need the LLM)."""
    labelled: list[TurnLabel] = []
    pending: list[TurnInput] = []
    for turn in turns:
        label = classify_by_rule(turn, taxonomy)
        if label is None:
            pending.append(turn)
        else:
            labelled.append(label)
    return labelled, pending


def chunks(items: Sequence[TurnInput], size: int) -> list[list[TurnInput]]:
    size = max(1, size)
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


async def classify_turns(
    turns: Sequence[TurnInput],
    taxonomy: Taxonomy,
    *,
    batch_size: int,
    budget_remaining: int | None,
    llm: ClassifierLLM | None = None,
) -> ClassificationResult:
    """Label ``turns``: rules first, then one LLM call per ``batch_size`` turns.

    ``budget_remaining`` is the token allowance left today (None = no limit).
    A batch whose estimated cost exceeds it is not sent; it and every later
    batch are returned in ``deferred``.
    """
    call = llm or azure_classifier_llm
    rule_labels, pending = split_by_rule(turns, taxonomy)
    result = ClassificationResult(labels=rule_labels)
    remaining = budget_remaining
    batches = chunks(pending, batch_size)

    for position, batch in enumerate(batches):
        messages = build_messages(batch, taxonomy)
        estimate = await asyncio.to_thread(estimate_tokens, messages, len(batch))
        if remaining is not None and estimate > remaining:
            result.deferred = [t for b in batches[position:] for t in b]
            logger.info(
                "insights_budget_exhausted",
                deferred=len(result.deferred),
                estimate=estimate,
                remaining=remaining,
            )
            break
        content, usage = await call(
            messages,
            build_response_schema(len(batch), taxonomy),
            max_completion_tokens(len(batch)),
        )
        usage = replace(usage, turns=len(batch))
        result.usage.append(usage)
        result.labels.extend(
            validate_labels(content, batch, taxonomy, usage.model or classifier_model())
        )
        if remaining is not None:
            remaining -= max(usage.total_tokens, 0)
    return result
