"""Natural not-covered wording: variants, recognisers, tail dropping, non-English."""

from __future__ import annotations

import pytest

from app.guardrails.post_checks import is_decline_sentence as pc_is_decline_sentence
from app.guardrails.post_checks import is_refusal
from app.harness import prompts
from app.harness.decline import clean_declines, is_decline_sentence
from app.harness.style import (
    NOT_COVERED_SENTENCE,
    NOT_COVERED_VARIANTS,
    ResponseStyle,
    not_covered_sentence,
)
from app.harness.tone import (
    drop_stock_handoff,
    drop_unrequested_caveat,
    humanise,
    polish_answer,
    remove_internal_wording,
)

OLD_PHRASES = [
    "I can't see that detail, so it's best to check with one of our specialists.",
    "I don't see that detail, so it's best to check with one of our specialists.",
    "We don't have details on that, so please talk to a specialist.",
    "I can't see any pricing here, so it's best to talk to one of our specialists.",
]
NEW_PHRASES = [
    "I don't have that detail, but one of our specialists can help.",
    "That's one for our specialists, who can give you a proper answer.",
    "I'm not sure about that one, so it's best to ask our specialists.",
    "I don't have details on that, so it's best to ask our specialists.",
]
BODY = "DeployIQ keeps a person in charge of every change."
ADAPTIVE = ResponseStyle.ADAPTIVE


def test_variants_are_natural() -> None:
    assert len(NOT_COVERED_VARIANTS) == 3
    for variant in NOT_COVERED_VARIANTS:
        assert "specialist" in variant
        assert "can't see" not in variant.lower()
        assert "\N{EM DASH}" not in variant
        assert "-" not in variant


def test_variant_is_hash_stable_and_varies() -> None:
    questions = [f"question number {i}" for i in range(40)]
    first = [not_covered_sentence(q) for q in questions]
    assert first == [not_covered_sentence(q) for q in questions]
    assert set(first) == set(NOT_COVERED_VARIANTS)
    assert not_covered_sentence("Who is the CEO?") == not_covered_sentence(
        "  who is the ceo? "
    )
    assert not_covered_sentence(None) == NOT_COVERED_SENTENCE


@pytest.mark.parametrize("sentence", OLD_PHRASES + NEW_PHRASES)
def test_decline_recognisers_accept_old_and_new(sentence: str) -> None:
    assert is_decline_sentence(sentence)
    assert is_decline_sentence(f"{sentence} [1]")
    assert pc_is_decline_sentence(sentence)


@pytest.mark.parametrize("sentence", OLD_PHRASES[:2] + NEW_PHRASES)
def test_is_refusal_accepts_old_and_new(sentence: str) -> None:
    assert is_refusal(sentence)
    assert is_refusal(f"{sentence} Talk to one of our specialists.")


def test_is_refusal_still_rejects_real_answers() -> None:
    assert not is_refusal(f"{BODY} {NEW_PHRASES[0]}")
    assert not is_refusal(BODY)


@pytest.mark.parametrize("sentence", OLD_PHRASES[:1] + NEW_PHRASES[:3])
def test_clean_declines_pure_decline(sentence: str) -> None:
    cleaned, pure = clean_declines(f"{sentence} [1]", "who is the ceo")
    assert pure
    assert cleaned == sentence


@pytest.mark.parametrize("sentence", NEW_PHRASES[:3])
def test_invented_steps_after_new_decline_are_stripped(sentence: str) -> None:
    answer = f"{sentence}\n1. Open the portal and reset your keys\n2. Restart the agent"
    cleaned, pure = clean_declines(
        answer, "how do I reset keys", "DeployIQ is a platform."
    )
    assert "portal" not in cleaned
    assert "Restart" not in cleaned
    assert pure


@pytest.mark.parametrize("sentence", OLD_PHRASES[:1] + NEW_PHRASES[:3])
def test_stock_handoff_dropped_from_complete_answer(sentence: str) -> None:
    answer = f"{BODY} {sentence}"
    assert drop_stock_handoff(answer, "what is deployiq") == BODY
    assert drop_stock_handoff(f"{BODY}\n\n{sentence}", "what is deployiq") == BODY


@pytest.mark.parametrize("sentence", NEW_PHRASES[:3])
def test_stock_handoff_kept_when_nothing_else_answered(sentence: str) -> None:
    assert drop_stock_handoff(sentence, "what is deployiq") == sentence


@pytest.mark.parametrize(
    "tail",
    [
        "I can't see who the CEO is, so it's best to check with one of our specialists.",
        "I can't see your setup, so it's best to ask one of our specialists.",
        "I don't see details about a firewall, so best to check with a specialist.",
        "I don't have the roadmap, so it's best to talk to our specialists.",
    ],
)
def test_named_handoff_tail_dropped_when_unasked(tail: str) -> None:
    answer = f"{BODY} {tail}"
    assert drop_stock_handoff(answer, "what does deployiq do") == BODY
    assert drop_unrequested_caveat(answer, "what does deployiq do") == BODY
    assert polish_answer(answer, ADAPTIVE, "what does deployiq do") == BODY


def test_named_handoff_kept_when_question_mentions_it() -> None:
    tail = (
        "I can't see who the CEO is, so it's best to check with one of our specialists."
    )
    answer = f"{BODY} {tail}"
    assert drop_stock_handoff(answer, "who is the CEO of deployiq") == answer
    assert drop_unrequested_caveat(answer, "who is the CEO of deployiq") == answer
    assert drop_stock_handoff(tail, "what does deployiq do") == tail


def test_named_handoff_only_dropped_when_final() -> None:
    tail = "I can't see your setup, so it's best to ask one of our specialists."
    middle = f"{tail} {BODY}"
    assert drop_stock_handoff(middle, "what does deployiq do") == middle


def test_internal_wording_uses_question_variant() -> None:
    question = "what is the price of deployiq"
    out = remove_internal_wording("The documents do not define pricing.", question)
    assert out == not_covered_sentence(question)
    out = polish_answer("The documents do not define pricing.", ADAPTIVE, question)
    assert out == not_covered_sentence(question)


def test_here_tell_rewritten_to_question_variant() -> None:
    question = "what is e8"
    tail = "I can't see that detail here, so it's best to check with one of our specialists."
    out = drop_unrequested_caveat(tail, question)
    assert out == not_covered_sentence(question)


def test_prompt_rules() -> None:
    prompt = prompts.get_system_prompt()
    assert "I don't have that detail" in prompt
    assert 'never write "I can\'t see"' in prompt
    assert "visitor's language" in prompt
    assert "compare it with other vendors" in prompt
    assert "can only speak for" in prompt


CHINESE = "DeployIQ 帮助您管理端点安全,每次更改都由专人批准。"
HINDI = "DeployIQ आपके एंडपॉइंट सुरक्षित रखता है और हर बदलाव को मंज़ूरी देता है।"
CHINESE_DASH = "DeployIQ 帮助您 \N{EM DASH} 管理端点安全。"
HINDI_BUZZ = "यह एक comprehensive और robust समाधान है, leverage करें।"


@pytest.mark.parametrize("text", [CHINESE, HINDI, HINDI_BUZZ])
def test_non_english_passes_through_unchanged(text: str) -> None:
    assert humanise(text) == text
    assert polish_answer(text, ADAPTIVE, "什么是DeployIQ") == text


def test_non_english_only_dashes_are_replaced() -> None:
    out = humanise(CHINESE_DASH)
    assert "\N{EM DASH}" not in out
    assert "帮助您" in out
    assert "管理端点安全" in out
    assert polish_answer(CHINESE_DASH, ADAPTIVE, None) == out


def test_english_buzzwords_still_rewritten() -> None:
    assert "comprehensive" not in humanise("We offer a comprehensive service.").lower()
