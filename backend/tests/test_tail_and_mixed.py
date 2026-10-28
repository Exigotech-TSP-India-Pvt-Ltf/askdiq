"""Unrequested not-covered tails, mixed-fact citation sentences, prompt rules."""

from __future__ import annotations

import time

from app.harness import prompts
from app.harness.citations import verify_citations
from app.harness.style import (
    NOT_COVERED_SENTENCE,
    NOT_COVERED_VARIANTS,
    not_covered_sentence,
)
from app.harness.tone import drop_unrequested_caveat
from app.schemas.query import SourceChunk

BODY = "Our platform keeps a person in charge of every change. [1]"
TAIL = "I can't see any pricing or timeline details here, so it's best to talk to one of our specialists."


def _chunk(content: str) -> SourceChunk:
    return SourceChunk(
        chunk_id="c1",
        document_id="00000000-0000-0000-0000-000000000001",
        content=content,
        score=0.8,
        metadata={"source_name": "doc1.md"},
    )


def test_drop_current_tail_when_not_asked() -> None:
    out = drop_unrequested_caveat(f"{BODY} {TAIL}", "What is human-in-the-loop?")
    assert out == BODY


def test_drop_tail_variants() -> None:
    for tail in (
        "I don't see any pricing details here.",
        "There's no timeline mentioned here.",
        "Pricing isn't mentioned here.",
        "I couldn't find cost or SLA details here, so talk to a specialist.",
        "I can't see that pricing, so it's best to check with one of our specialists.",
    ):
        assert drop_unrequested_caveat(f"{BODY} {tail}", "Tell me about E8") == BODY


def test_drop_tail_on_own_line_and_with_separate_handoff() -> None:
    out = drop_unrequested_caveat(
        f"{BODY}\n\nI can't see any pricing here. Talk to one of our specialists.",
        "What is responsible AI?",
    )
    assert out == BODY


def test_kept_when_question_mentions_topic() -> None:
    text = f"{BODY} {TAIL}"
    kept = drop_unrequested_caveat(
        text, "How much does it cost and what is the timeline?"
    )
    assert "specialists" in kept
    assert kept.endswith(
        not_covered_sentence("How much does it cost and what is the timeline?")
    )
    assert drop_unrequested_caveat(text, "What are the pricing options?").endswith(
        not_covered_sentence("What are the pricing options?")
    )


def test_here_tell_rewritten_without_question() -> None:
    assert drop_unrequested_caveat(f"{BODY} {TAIL}", None).endswith(
        NOT_COVERED_SENTENCE
    )


def test_one_sentence_answer_never_emptied() -> None:
    assert drop_unrequested_caveat(TAIL, "What is E8?") in NOT_COVERED_VARIANTS
    plain = "I don't see any pricing here."
    assert drop_unrequested_caveat(plain, "What is E8?") == plain


def test_factual_last_sentence_kept() -> None:
    text = f"{BODY} There's no cost for the Free plan."
    assert drop_unrequested_caveat(text, "What is E8?") == text


def test_caveat_removal_linear_time() -> None:
    text = ("word " * 4000) + "I can't see pricing here. " + "x" * 20000
    start = time.perf_counter()
    drop_unrequested_caveat(text, "what is it")
    drop_unrequested_caveat("[" * 20000 + " I can't see pricing here.", "what")
    assert time.perf_counter() - start < 2


CHUNK = _chunk(
    "The Zero Trust Starter plan is offered as Free. It includes device checks."
)


def test_mixed_sentence_keeps_marker() -> None:
    answer = "Pricing isn't published for the Zero Trust Starter plan, it's offered as Free [1]."
    out, cites = verify_citations(answer, [CHUNK])
    assert "[1]" in out
    assert len(cites) == 1


def test_mixed_sentence_marker_moves_after_factual_clause() -> None:
    answer = "The Zero Trust Starter plan is offered as Free, but pricing isn't published [1]."
    out, _ = verify_citations(answer, [CHUNK])
    assert "offered as Free[1], but pricing isn't published" in out


def test_pure_absence_loses_marker() -> None:
    out, cites = verify_citations("Pricing isn't published for the plan [1].", [CHUNK])
    assert "[1]" not in out
    assert cites == []


def test_mixed_sentence_unsupported_fact_loses_marker() -> None:
    out, _ = verify_citations(
        "Pricing isn't published, but it runs on quantum submarines [1].", [CHUNK]
    )
    assert "[1]" not in out


def test_mixed_verification_linear_time() -> None:
    answer = ("Pricing isn't published, " + "it's offered as Free, " * 2000) + "[1]."
    start = time.perf_counter()
    verify_citations(answer, [CHUNK])
    assert time.perf_counter() - start < 3


def test_prompt_rules_present() -> None:
    prompt = prompts.SYSTEM_PROMPT
    assert "Don't mention pricing, timelines or what you don't have" in prompt
    assert "short bullets" in prompt
