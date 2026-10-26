"""OR-8 response style: shape detection, style profiles, A/B bucketing,
the tone post-filter and the orchestrator wiring."""

from __future__ import annotations

import time
from collections import Counter

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.harness import orchestrator, prompts, style
from app.harness.ambiguity import AmbiguityKind, Assessment
from app.harness.answer_shape import AnswerShape, detect_shape, refine_shape
from app.harness.citations import MARKER_RE, citation_instruction
from app.harness.style import (
    BANNED_PHRASES,
    STYLE_PROFILES,
    ResponseStyle,
    bucket,
    resolve_style,
    style_instructions,
    word_cap,
)
from app.harness.tone import (
    longest_prose_run,
    paragraph_guard,
    polish_answer,
    strip_stock_phrases,
)
from app.schemas.query import ChatTurn, SourceChunk

BUDGET_SECONDS = 0.5
MIN_ACCURACY = 0.9

# Labelled set (>= 10 per pre-retrieval shape); the eval gate needs >= 90%.
LABELLED: dict[AnswerShape, list[str]] = {
    AnswerShape.DEFINITION: [
        "What is the Essential Eight?",
        "what's DeployIQ",
        "What are the Annex A controls?",
        "Define application control",
        "What does MFA mean?",
        "What does ACSC stand for?",
        "Explain Zero Trust",
        "Tell me about the Security Baseline assessment",
        "Can you tell me what is the CIS Benchmark?",
        "Who is Exigo Tech?",
        "Describe the DeployIQ platform",
        "Meaning of maturity level two",
    ],
    AnswerShape.HOW_TO: [
        "How do I start an Essential Eight assessment?",
        "How can we onboard our tenant?",
        "How to enable MFA for admins",
        "Steps to get ISO 27001 ready",
        "Set up a pilot group for application control",
        "Configure conditional access with DeployIQ",
        "How does DeployIQ roll out a control?",
        "What are the steps to onboard?",
        "Walk me through the assessment process",
        "How should we prepare for an audit?",
        "Please tell me how do I book a consultant",
    ],
    AnswerShape.COMPARISON: [
        "Essential Eight vs ISO 27001",
        "What is the difference between Zero Trust and the Security Baseline?",
        "Compare the CIS Benchmark and the Essential Eight",
        "Is ISO 27001 better than the Essential Eight?",
        "Essential Eight versus CIS Benchmark for a small business",
        "How is Zero Trust different from the Security Baseline?",
        "Which is better for us, ISO 27001 or Essential Eight?",
        "Pros and cons of Zero Trust",
        "Essential Eight compared to ISO 27001",
        "Comparison of the DeployIQ platform and a manual audit",
    ],
    AnswerShape.QUICK_FACT: [
        "Is DeployIQ available in Australia?",
        "Does DeployIQ support Microsoft 365?",
        "Can you do ISO 27001 work?",
        "How long does an Essential Eight assessment take?",
        "How much does the Security Baseline cost?",
        "How many strategies are in the Essential Eight?",
        "Are backups part of the Essential Eight?",
        "Will my team approve every change?",
        "Do you offer drift checks?",
        "Should we start with a pilot?",
        "What does the assessment cost?",
        "When can we start?",
    ],
    AnswerShape.GENERAL: [
        "DeployIQ pricing",
        "Essential Eight maturity",
        "I need help with compliance",
        "Our audit is next month",
        "Microsoft 365 security",
        "backup strategy",
        "We use Azure AD",
        "Tenant onboarding",
        "Patch management in DeployIQ",
        "ISO 27001",
    ],
}


def _source(content: str, name: str = "about-us.md", score: float = 0.8):
    return SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        content=content,
        score=score,
        metadata={"source_name": name},
    )


# --------------------------------------------------------------------------
# Shape detection
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shape", list(LABELLED))
def test_detection_accuracy_per_shape(shape: AnswerShape) -> None:
    questions = LABELLED[shape]
    misses = [q for q in questions if detect_shape(q) is not shape]
    assert len(questions) >= 10
    assert 1 - len(misses) / len(questions) >= MIN_ACCURACY, misses


def test_detection_accuracy_overall() -> None:
    pairs = [(q, s) for s, qs in LABELLED.items() for q in qs]
    correct = sum(1 for q, s in pairs if detect_shape(q) is s)
    assert correct / len(pairs) >= MIN_ACCURACY


def test_ambiguous_comparison_reuses_assess() -> None:
    assessment = Assessment(AmbiguityKind.COMPARISON)
    assert detect_shape("Is it any good?", assessment) is AnswerShape.COMPARISON
    assert detect_shape("Is it any good?", Assessment()) is AnswerShape.QUICK_FACT


def test_zero_trust_starter_free_prompt_uses_explicit_source_label() -> None:
    prompt = prompts.build_prompt(
        "Is the Zero Trust Starter plan really 100% free?",
        ["Zero Trust - Starter (Free)"],
    )

    assert "the context labels it Free" in prompt
    assert "State that it is free" in prompt


def test_zero_trust_starter_instruction_is_query_scoped() -> None:
    prompt = prompts.build_prompt("What is DeployIQ?", ["DeployIQ platform details"])

    assert "the context labels it Free" not in prompt


def test_starter_scope_followup_uses_referent_from_relevant_history() -> None:
    prompt = prompts.build_prompt(
        "What does that include?",
        ["Starter (Free): Microsoft Zero Trust Assessment tool and guidance."],
        [
            ChatTurn(role="user", content="Is there a free Zero Trust plan?"),
            ChatTurn(role="assistant", content="The Zero Trust Starter plan is free."),
        ],
    )

    assert "This follow-up refers to the Zero Trust Starter (Free) plan" in prompt
    assert "assessment tool and guidance" in prompt


def test_starter_scope_instruction_is_not_added_without_matching_history() -> None:
    prompt = prompts.build_prompt(
        "What does that include?",
        ["DeployIQ platform details."],
        [ChatTurn(role="user", content="What does the platform do?")],
    )

    assert "This follow-up refers to the Zero Trust Starter (Free) plan" not in prompt


def test_zero_trust_stages_prompt_uses_documented_delivery_stages() -> None:
    prompt = prompts.build_prompt(
        "What stages does DeployIQ follow to deliver Zero Trust?",
        ["Assess. Prioritise. Design. Implement. Assure."],
    )

    assert "Include assessing posture, prioritising controls" in prompt
    assert "Do not invent a complete stage sequence" in prompt


def test_zero_trust_outcomes_prompt_includes_supported_attack_surface_fact() -> None:
    prompt = prompts.build_prompt(
        "What outcomes can I expect from DeployIQ's Zero Trust service?",
        ["Reduce attack surface; stronger identity controls."],
    )

    assert "explicitly include reducing the attack surface" in prompt


def test_soc2_prompt_scopes_certification_claim_to_trust_center_context() -> None:
    prompt = prompts.build_prompt(
        "Is DeployIQ SOC 2 certified?",
        ["Formal certifications and third-party attestations are not yet claimed."],
    )

    assert "state the Trust Center's exact certification status" in prompt


def test_comparison_pattern_needs_two_named_subjects() -> None:
    two = "Is Essential Eight stronger than ISO 27001 or is that one easier?"
    assert detect_shape(two) is AnswerShape.COMPARISON
    assert detect_shape("Which one should I pick?") is not AnswerShape.COMPARISON


def test_empty_question_is_general() -> None:
    assert detect_shape("   ") is AnswerShape.GENERAL


def test_no_usable_context_is_out_of_scope() -> None:
    assert refine_shape(AnswerShape.DEFINITION, "What is X?", []) is (
        AnswerShape.OUT_OF_SCOPE
    )
    weak = [_source("anything", score=0.1)]
    assert refine_shape(AnswerShape.GENERAL, "q", weak, min_score=0.3) is (
        AnswerShape.OUT_OF_SCOPE
    )


def test_partial_coverage_overrides_shape() -> None:
    query = "Compare the Essential Eight and ISO 27001"
    only_e8 = [_source("The Essential Eight has eight strategies.", "e8.md")]
    both = [*only_e8, _source("ISO 27001 Annex A controls.", "iso.md")]
    assert refine_shape(AnswerShape.COMPARISON, query, only_e8) is (AnswerShape.PARTIAL)
    assert refine_shape(AnswerShape.COMPARISON, query, both) is (AnswerShape.COMPARISON)


def test_single_subject_keeps_shape() -> None:
    sources = [_source("Unrelated text.")]
    assert refine_shape(AnswerShape.HOW_TO, "How do I enable MFA?", sources) is (
        AnswerShape.HOW_TO
    )


HOSTILE = [
    "what is " + " " * 100_000,
    "a" * 100_000,
    "How do I " + "\t" * 100_000,
    "vs " * 35_000,
    "Aa " * 35_000 + "than that",
]


@pytest.mark.parametrize("text", HOSTILE, ids=lambda t: f"{t[:6]!r}x{len(t)}")
def test_detection_stays_fast_on_100kb(text: str) -> None:
    sources = [_source(text)]
    started = time.perf_counter()
    refine_shape(detect_shape(text), text, sources)
    assert time.perf_counter() - started < BUDGET_SECONDS


# --------------------------------------------------------------------------
# Style profiles and prompt blocks
# --------------------------------------------------------------------------


def test_current_style_keeps_the_original_prompt() -> None:
    assert prompts.get_system_prompt() == prompts.SYSTEM_PROMPT
    assert prompts.get_system_prompt(ResponseStyle.CURRENT, "comparison") == (
        prompts.SYSTEM_PROMPT
    )
    assert style_instructions(ResponseStyle.CURRENT, "definition") == ""
    assert f"{get_settings().answer_max_words} words." in prompts.SYSTEM_PROMPT


def test_system_prompt_preserves_canonical_process_and_outcome_terms() -> None:
    prompt = prompts.get_system_prompt()
    assert "Preserve named stages, outcome labels, and canonical terms" in prompt
    assert "do not replace them with generic paraphrases" in prompt


def test_every_shape_has_one_format_block() -> None:
    blocks = STYLE_PROFILES[ResponseStyle.ADAPTIVE].shape_blocks
    assert set(blocks) == {s.value for s in AnswerShape}


@pytest.mark.parametrize("shape", list(AnswerShape))
def test_only_the_detected_shape_block_is_injected(shape: AnswerShape) -> None:
    blocks = STYLE_PROFILES[ResponseStyle.ADAPTIVE].shape_blocks
    prompt = prompts.get_system_prompt(ResponseStyle.ADAPTIVE, shape)
    assert prompt.startswith(prompts.SYSTEM_PROMPT.rsplit(" at most ", 1)[0])
    assert blocks[shape.value] in prompt
    others = [b for k, b in blocks.items() if k != shape.value and b != blocks[shape]]
    assert not any(b in prompt for b in others)


def test_adaptive_prompt_carries_tone_rules_and_banned_list() -> None:
    prompt = prompts.get_system_prompt(ResponseStyle.ADAPTIVE, AnswerShape.GENERAL)
    assert "Never write paragraphs" in prompt
    assert "no raw HTML, no links" in prompt
    assert all(f'"{phrase}"' in prompt for phrase in BANNED_PHRASES)
    assert get_settings().brand_name in prompt


def test_prompt_size_impact_is_small() -> None:
    base = prompts.count_tokens(prompts.SYSTEM_PROMPT)
    for shape in AnswerShape:
        adaptive = prompts.count_tokens(
            prompts.get_system_prompt(ResponseStyle.ADAPTIVE, shape)
        )
        assert adaptive - base < 400


def test_unknown_style_or_shape_falls_back() -> None:
    assert style_instructions("nope", "definition") == ""
    general = STYLE_PROFILES[ResponseStyle.ADAPTIVE].shape_blocks["general"]
    assert general in style_instructions(ResponseStyle.ADAPTIVE, "unknown")


def test_word_caps_per_shape() -> None:
    settings = get_settings()
    assert word_cap(ResponseStyle.ADAPTIVE, "comparison") == (
        settings.answer_max_words_long
    )
    assert word_cap(ResponseStyle.ADAPTIVE, "how_to") == settings.answer_max_words_long
    assert word_cap(ResponseStyle.ADAPTIVE, "definition") == settings.answer_max_words
    assert word_cap(ResponseStyle.CURRENT, "comparison") == settings.answer_max_words


# --------------------------------------------------------------------------
# Settings and sticky A/B bucketing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pct", [-1, 101])
def test_pct_validator_rejects_out_of_range(pct: int) -> None:
    with pytest.raises(ValidationError):
        Settings(response_style_b_pct=pct)


def test_bucket_is_stable_and_in_range() -> None:
    assert bucket("session-1") == bucket("session-1")
    assert all(0 <= bucket(f"s{i}") < 100 for i in range(500))


def _patch_settings(monkeypatch, **values) -> None:
    patched = get_settings().model_copy(update=values)
    monkeypatch.setattr(style, "get_settings", lambda: patched)


def test_default_ships_adaptive_to_everyone(monkeypatch) -> None:
    _patch_settings(monkeypatch, response_style="adaptive", response_style_b_pct=0)
    assert {resolve_style(f"s{i}") for i in range(200)} == {ResponseStyle.ADAPTIVE}
    assert resolve_style(None) is ResponseStyle.ADAPTIVE


def test_off_switch_is_one_setting(monkeypatch) -> None:
    _patch_settings(monkeypatch, response_style="current", response_style_b_pct=0)
    assert resolve_style("s1") is ResponseStyle.CURRENT


def test_split_matches_pct_and_is_sticky(monkeypatch) -> None:
    _patch_settings(
        monkeypatch,
        response_style="current",
        response_style_b="adaptive",
        response_style_b_pct=50,
    )
    keys = [f"00000000-0000-4000-8000-{i:012d}" for i in range(1000)]
    counts = Counter(resolve_style(k) for k in keys)
    share = counts[ResponseStyle.ADAPTIVE] / len(keys) * 100
    assert abs(share - 50) <= 4
    assert [resolve_style(k) for k in keys[:50]] == [
        resolve_style(k) for k in keys[:50]
    ]
    assert resolve_style(None) is ResponseStyle.CURRENT


# --------------------------------------------------------------------------
# Tone post-filter
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Great question! We cover eight strategies.", "We cover eight strategies."),
        ("Certainly! It takes days.", "It takes days."),
        (
            "Based on the information provided, we apply controls in phases.",
            "We apply controls in phases.",
        ),
        (
            "We apply controls in phases.\n\nI hope this helps!",
            "We apply controls in phases.",
        ),
        (
            "We pilot first. Feel free to reach out if you have questions.",
            "We pilot first.",
        ),
        (
            "Certainly! We pilot first.\n- **Pilot** first\n\nLet me know if you need anything else.",
            "We pilot first.\n- **Pilot** first",
        ),
    ],
)
def test_stock_openers_and_closers_are_removed(raw: str, expected: str) -> None:
    assert strip_stock_phrases(raw) == expected


@pytest.mark.parametrize(
    "answer",
    [
        "We pilot first. Great question! is not an opener here.",
        "It's important to note that we pilot first. Then we scale.",
        "We pilot first. Feel free to reach out at sales@example.com.",
        "We pilot first. Feel free to reach out [1].",
        "We pilot first. Feel free to reach out on 1300 000 000.",
        "Next step: book a consultant.",
        "I hope this helps",
    ],
)
def test_facts_citations_and_middles_are_never_altered(answer: str) -> None:
    assert strip_stock_phrases(answer) == answer


def test_citation_markers_survive_opener_cut() -> None:
    raw = "Certainly! We cover eight strategies [1][2].\n- MFA [3]"
    assert strip_stock_phrases(raw) == "We cover eight strategies [1][2].\n- MFA [3]"


@pytest.mark.parametrize(
    "answer",
    [
        "Great question! We cover eight strategies [1][2].",
        "Certainly! [1] We pilot first.",
        "We pilot first [1]. I hope this helps [2].",
        "We pilot first.[1] Feel free to reach out.",
        "We pilot first. [1] I hope this helps!",
        "Based on the context, we pilot first [3].\n\nLet me know if you need [1].",
        "## Plan\n- **Pilot** first [1]\n- Scale [2][3]\n\n| A | B |\n| --- | --- |\n| x [1] | y [2] |",
        "1. Connect [1]\n2. Run [2]\n\nI hope this helps!",
    ],
)
def test_tone_filter_never_removes_or_alters_markers(answer: str) -> None:
    polished = polish_answer(answer, ResponseStyle.ADAPTIVE)
    assert [m.group(0) for m in MARKER_RE.finditer(polished)] == [
        m.group(0) for m in MARKER_RE.finditer(answer)
    ]
    # Only a whole opener or closer is cut: the rest is a verbatim slice.
    body = polished[1:]
    assert body in answer


def test_prompt_composes_style_block_and_citation_instruction() -> None:
    blocks = STYLE_PROFILES[ResponseStyle.ADAPTIVE].shape_blocks
    for shape in AnswerShape:
        prompt = prompts.get_system_prompt(ResponseStyle.ADAPTIVE, shape)
        assert citation_instruction() in prompt
        assert blocks[shape.value] in prompt
        assert "Keep any citation marker at the end of the claim" in prompt
        # Grounding, then markers, then the style: one instruction each.
        assert prompt.count(citation_instruction()) == 1
        assert prompt.index(citation_instruction()) < prompt.index("Style:")
    current = prompts.get_system_prompt(ResponseStyle.CURRENT, "comparison")
    assert citation_instruction() in current
    assert "Style:" not in current


def test_polish_only_under_adaptive() -> None:
    raw = "Great question! We pilot first."
    assert polish_answer(raw, ResponseStyle.CURRENT) == raw
    assert polish_answer(raw, ResponseStyle.ADAPTIVE) == "We pilot first."


def test_paragraph_guard_counts_prose_only() -> None:
    dump = "One. Two. Three. Four."
    structured = (
        "One lead. Two lead.\n\n## Key points\n- A. B. C.\n- D\n\nNext step: x."
    )
    assert paragraph_guard(dump)
    assert not paragraph_guard(structured)
    assert longest_prose_run(structured) == 2


@pytest.mark.parametrize(
    "text",
    [
        "great question! " + ". " * 50_000,
        "x" * 100_000 + " I hope this helps",
        "! " * 50_000,
        "\n" * 100_000,
    ],
    ids=lambda t: f"{t[:6]!r}x{len(t)}",
)
def test_tone_filter_stays_fast_on_100kb(text: str) -> None:
    started = time.perf_counter()
    strip_stock_phrases(text)
    longest_prose_run(text)
    assert time.perf_counter() - started < BUDGET_SECONDS


# --------------------------------------------------------------------------
# Orchestrator wiring
# --------------------------------------------------------------------------


async def test_run_query_uses_style_shape_and_records_them(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(orchestrator, "run_pre_checks", lambda *_a, **_k: [])

    async def fake_retrieve(*_a, **_k):
        return [_source("The Essential Eight is eight mitigation strategies.")]

    async def fake_call_llm(_query, _chunks, _history=None, **_kw):
        seen.append(prompts.get_system_prompt(*style.active_style()))
        answer = "Great question! We cover eight strategies.\n- **MFA**\n- **Backups**"
        return answer, {"prompt_tokens": 10, "completion_tokens": 5}

    async def fake_post_checks(*_a, **_k):
        return True, 0.9, []

    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    monkeypatch.setattr(orchestrator, "call_llm", fake_call_llm)
    monkeypatch.setattr(orchestrator, "run_post_checks", fake_post_checks)
    _patch_settings(monkeypatch, response_style="adaptive", response_style_b_pct=0)

    response = await orchestrator.run_query(
        query="What is the Essential Eight?",
        top_k=5,
        filters=None,
        vector_store=None,
        request_id="style-test",
        stage_timings={},
        style_key="session-1",
    )

    blocks = STYLE_PROFILES[ResponseStyle.ADAPTIVE].shape_blocks
    assert blocks["definition"] in seen[0]
    assert response.answer.startswith("We cover eight strategies.")
    assert response.token_details["style"] == "adaptive"
    assert response.token_details["shape"] == "definition"


async def test_run_query_with_style_and_citations(monkeypatch) -> None:
    checked: list[str] = []
    monkeypatch.setattr(orchestrator, "run_pre_checks", lambda *_a, **_k: [])
    sources = [
        SourceChunk(
            chunk_id=f"00000000-0000-0000-0000-00000000001{i}",
            document_id=f"00000000-0000-0000-0000-00000000002{i}",
            content="The Essential Eight is eight mitigation strategies.",
            score=0.8,
            metadata={"source_name": f"doc-{i}.md"},
            url=f"https://docs.deployiq.com.au/doc-{i}",
        )
        for i in (1, 2)
    ]

    async def fake_retrieve(*_a, **_k):
        return sources

    async def fake_call_llm(_query, _chunks, _history=None, **_kw):
        answer = (
            "Great question! We cover eight strategies [1].\n\n"
            "## Key controls\n- **MFA** [1][2]\n- **Backups** [2][9]\n\n"
            "I hope this helps!"
        )
        return answer, {"prompt_tokens": 10, "completion_tokens": 5}

    async def fake_post_checks(answer, *_a, **_k):
        checked.append(answer)
        return True, 0.9, []

    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    monkeypatch.setattr(orchestrator, "call_llm", fake_call_llm)
    monkeypatch.setattr(orchestrator, "run_post_checks", fake_post_checks)
    _patch_settings(monkeypatch, response_style="adaptive", response_style_b_pct=0)

    response = await orchestrator.run_query(
        query="What is the Essential Eight?",
        top_k=5,
        filters=None,
        vector_store=None,
        request_id="style-citations-test",
        stage_timings={},
        style_key="session-1",
    )

    # Markers are stripped before the groundedness check.
    assert "[" not in checked[0]
    # Tone filter ran (opener and closer gone), then markers were resolved:
    # the unknown [9] is dropped, the rest stay inside the Markdown blocks.
    assert response.answer == (
        "We cover eight strategies [1].\n\n"
        "## Key controls\n- **MFA** [1][2]\n- **Backups** [2]"
    )
    assert [c.n for c in response.citations] == [1, 2]
    assert response.citations[0].url == "https://docs.deployiq.com.au/doc-1"
    details = response.token_details
    assert details["style"] == "adaptive"
    assert details["shape"] == "definition"
    assert [c["n"] for c in details["citations"]] == [1, 2]
