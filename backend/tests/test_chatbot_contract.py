"""Chatbot quality contract: visitor-facing behaviour that must never regress.

Runs in CI on every PR: deterministic, no DB, no network, no LLM (the doubles
are the ones test_orchestrator.py and test_clarify_trigger.py use). Each
assertion message names the contract and the file to look at. The cases live
in tests/chatbot_contract_cases.py; the rules are in
docs/technical/11-chatbot-quality-contract.md.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.guardrails.post_checks import check_groundedness
from app.guardrails.pre_checks import run_pre_checks
from app.harness import prompts
from app.harness.ambiguity import AmbiguityKind, assess, slot_clarification
from app.harness.answer_shape import AnswerShape, refine_shape
from app.harness.citations import citation_instruction
from app.harness.clarify import NOT_SURE_LABEL, clarify_eligible, plan_clarification
from app.harness.orchestrator import _fallback_response
from app.harness.style import ResponseStyle, style_instructions
from app.schemas.query import SourceChunk
from tests import chatbot_contract_cases as cases
from tests.test_clarify import _spread_sources
from tests.test_clarify_trigger import _no_decision_call, _run, _sources
from tests.test_orchestrator import _run as _orchestrator_run
from tests.test_orchestrator import _source

MAX_QUERY_LENGTH = 1000
ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"
_SHAPES = ("quick_fact", "partial", "out_of_scope", "general", "how_to", "definition")


def _contract(name: str, where: str, detail: str) -> str:
    return (
        f"\nCHATBOT CONTRACT BROKEN: {name}\n  {detail}\n  Look at: {where}\n"
        "  Do not weaken the case: fix the code, see "
        "docs/technical/11-chatbot-quality-contract.md."
    )


def _fallback(flag: str):
    return _fallback_response(request_id="r", flags=[flag], latency_ms=1.0)


def _internal_words_in(text: str) -> list[str]:
    lowered = text.lower()
    return [w for w in cases.BANNED_INTERNAL_WORDS if w in lowered]


def _unquoted_internal_words_in(text: str) -> list[str]:
    """Prompts may quote the words inside a "never write ..." prohibition."""
    lowered = re.sub(r'"[^"]*"', "", text.lower())
    return [w for w in cases.BANNED_INTERNAL_WORDS if w in lowered]


# ---------------------------------------------------------------------------
# 1. Fallback wording
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag", cases.ALL_FALLBACK_FLAGS)
def test_fallback_never_uses_internal_wording(flag: str) -> None:
    found = _internal_words_in(_fallback(flag).answer)
    assert not found, _contract(
        "fallback-no-internal-words",
        "app/harness/orchestrator.py (_fallback_response), app/harness/style.py",
        f"flag {flag!r} leaks {found} to the visitor",
    )


@pytest.mark.parametrize("flag", cases.SPECIALIST_FLAGS)
def test_unanswered_fallback_offers_a_specialist(flag: str) -> None:
    assert "specialist" in _fallback(flag).answer.lower(), _contract(
        "fallback-offers-human-next-step",
        "app/harness/orchestrator.py (_fallback_response)",
        f"flag {flag!r} does not mention a specialist",
    )


@pytest.mark.parametrize("flag", cases.NO_SOURCE_FLAGS)
def test_blocked_fallback_carries_no_sources(flag: str) -> None:
    response = _fallback(flag)
    assert response.sources == [], _contract(
        "blocked-fallback-no-sources",
        "app/harness/orchestrator.py (_fallback_response)",
        f"flag {flag!r} returned {len(response.sources)} source(s)",
    )
    assert response.citations == []


# ---------------------------------------------------------------------------
# 2. Prompt wording
# ---------------------------------------------------------------------------


def _prompt_texts() -> dict[str, str]:
    base = prompts.get_system_prompt().replace(citation_instruction(), "")
    texts = {"system prompt": base}
    for shape in _SHAPES:
        suffix = style_instructions(ResponseStyle.ADAPTIVE, shape)
        # The suffix lists the banned phrases on purpose, after this marker.
        texts[f"adaptive suffix [{shape}]"] = suffix.partition(
            "Never use these phrases:"
        )[0]
        full = prompts.get_system_prompt(ResponseStyle.ADAPTIVE, shape)
        texts[f"adaptive prompt [{shape}]"] = full.replace(
            citation_instruction(), ""
        ).partition("Never use these phrases:")[0]
    return texts


@pytest.mark.parametrize("label", list(_prompt_texts()))
def test_prompts_never_name_internal_words(label: str) -> None:
    found = _unquoted_internal_words_in(_prompt_texts()[label])
    assert not found, _contract(
        "prompt-no-internal-words",
        "app/harness/prompts.py, app/harness/style.py",
        f"{label} contains {found}; the model parrots whatever the prompt says",
    )


def test_system_prompt_carries_the_assurance_guard() -> None:
    prompt = prompts.get_system_prompt().lower()
    for needle in ("never state or imply", "safe to purchase"):
        assert needle in prompt, _contract(
            "prompt-assurance-guard",
            "app/harness/prompts.py",
            f"system prompt lost {needle!r}",
        )


def test_adaptive_suffix_keeps_the_specialist_next_step() -> None:
    block = style_instructions(ResponseStyle.ADAPTIVE, "partial")
    assert "specialist" in block.partition("Never use these phrases:")[0], _contract(
        "style-human-next-step", "app/harness/style.py", "no specialist hand-over"
    )


# ---------------------------------------------------------------------------
# 3. Pre-check corpus
# ---------------------------------------------------------------------------


def test_corpus_sizes_are_not_shrunk() -> None:
    assert len(cases.VALID_QUESTIONS) >= 60  # noqa: PLR2004
    assert len(cases.ATTACK_QUERIES) >= 40  # noqa: PLR2004


@pytest.mark.parametrize("query", cases.VALID_QUESTIONS)
def test_canonical_visitor_question_passes_untouched(query: str) -> None:
    flags = run_pre_checks(query, MAX_QUERY_LENGTH)
    assert flags == [], _contract(
        "valid-question-never-flagged",
        "app/guardrails/pre_checks.py",
        f"{query!r} was flagged {flags}; a real visitor would be blocked",
    )


@pytest.mark.parametrize("query", cases.ATTACK_QUERIES)
def test_attack_or_junk_is_flagged(query: str) -> None:
    flags = run_pre_checks(query, MAX_QUERY_LENGTH)
    assert flags, _contract(
        "attack-always-flagged",
        "app/guardrails/pre_checks.py",
        f"{query!r} passed pre-checks with no flag",
    )


@pytest.mark.parametrize("name", list(cases.INCIDENT_FLAGGED))
def test_production_incident_stays_flagged(name: str) -> None:
    query = cases.INCIDENT_FLAGGED[name]
    assert run_pre_checks(query, MAX_QUERY_LENGTH), _contract(
        f"incident-{name}", "app/guardrails/pre_checks.py", f"{query!r} not flagged"
    )


@pytest.mark.parametrize("name", list(cases.INCIDENT_VALID))
def test_production_false_positive_stays_valid(name: str) -> None:
    query = cases.INCIDENT_VALID[name]
    assert run_pre_checks(query, MAX_QUERY_LENGTH) == [], _contract(
        f"incident-{name}", "app/guardrails/pre_checks.py", f"{query!r} was flagged"
    )


# ---------------------------------------------------------------------------
# 4. Clarifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", cases.NEVER_CLARIFY_QUERIES)
def test_clarifier_never_asks_about_facts_or_general_questions(query: str) -> None:
    where = "app/harness/clarify.py, app/harness/ambiguity.py"
    assert assess(query, None).kind is AmbiguityKind.NONE, _contract(
        "clarifier-never-on-facts", where, f"assess() asked back on {query!r}"
    )


@pytest.mark.parametrize("query", cases.NEVER_CLARIFY_QUERIES)
async def test_clarifier_never_clarifies_end_to_end(monkeypatch, query: str) -> None:
    _no_decision_call(monkeypatch)
    response, calls, _ = await _run(
        monkeypatch, query, ["An answer."], sources=_sources(0.7)
    )
    assert response.outcome == "answered", _contract(
        "clarifier-never-on-facts",
        "app/harness/orchestrator.py, app/harness/clarify.py",
        f"{query!r} produced outcome {response.outcome!r}",
    )
    assert response.clarification is None
    assert "clarify_block" not in calls[0]


@pytest.mark.parametrize("query", cases.VAGUE_COMPLIANCE_QUERIES)
def test_vague_compliance_asks_the_framework_then_maturity_series(query: str) -> None:
    where = "app/harness/ambiguity.py"
    result = assess(query, None)
    assert result.kind is AmbiguityKind.SLOTS, _contract(
        "clarifier-vague-compliance-series", where, f"{query!r} -> {result.kind}"
    )
    assert result.missing_slots[:2] == ("framework", "maturity"), _contract(
        "clarifier-vague-compliance-series", where, f"slots {result.missing_slots}"
    )
    clarification = slot_clarification(query, result.missing_slots, _sources())
    assert clarification is not None
    headers = [q.header for q in clarification.questions]
    assert headers[:2] == ["Framework", "Maturity"], _contract(
        "clarifier-vague-compliance-series", where, f"headers {headers}"
    )
    labels = [o.label for o in clarification.questions[0].options]
    assert NOT_SURE_LABEL in labels, _contract(
        "clarifier-not-sure-option", where, f"framework options {labels}"
    )


async def test_i_need_compliance_end_to_end_has_not_sure(monkeypatch) -> None:
    _no_decision_call(monkeypatch)
    response, _, _ = await _run(monkeypatch, "i need compliance")
    assert response.clarification is not None, _contract(
        "clarifier-vague-compliance-series",
        "app/harness/orchestrator.py",
        f"no clarification, outcome {response.outcome!r}",
    )
    first = response.clarification.questions[0]
    assert first.header == "Framework"
    assert NOT_SURE_LABEL in [o.label for o in first.options], _contract(
        "clarifier-not-sure-option", "app/harness/ambiguity.py", "no Not sure option"
    )


def test_named_entities_are_not_clarify_eligible() -> None:
    query = "Where is Exigo Tech headquartered?"
    assert not clarify_eligible(query, None, follow_up=False), _contract(
        "clarifier-never-on-facts", "app/harness/clarify.py", f"{query!r} eligible"
    )
    assert plan_clarification(query, _spread_sources(), None, follow_up=False) == []


def test_folded_reply_is_never_the_partial_shape() -> None:
    shape = refine_shape(
        AnswerShape.HOW_TO,
        cases.FOLDED_REPLY_QUERY,
        [
            SourceChunk(
                chunk_id="c1",
                document_id="d1",
                content="ISO 27001 controls for Microsoft 365 tenants.",
                score=0.5,
                metadata={"source_name": "iso-27001.md"},
            ),
            SourceChunk(
                chunk_id="c2",
                document_id="d2",
                content="Microsoft 365 security configuration guidance.",
                score=0.4,
                metadata={"source_name": "m365.md"},
            ),
        ],
    )
    assert shape is not AnswerShape.PARTIAL, _contract(
        "folded-reply-not-partial",
        "app/harness/answer_shape.py (strip_answers, _LABEL_WORDS)",
        "the '(Framework: ...; Maturity: ...)' suffix was read as subjects",
    )


# ---------------------------------------------------------------------------
# 5. Post checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("answer", cases.UNSUPPORTED_ASSURANCES)
async def test_unsupported_assurance_fails_groundedness(answer: str) -> None:
    sources = [_source("DeployIQ delivers Essential Eight reporting.")]
    grounded, _, flag = await check_groundedness(answer, sources, 0.45)
    assert grounded is False, _contract(
        "assurance-not-in-sources-is-ungrounded",
        "app/guardrails/post_checks.py (unsupported_claims)",
        f"{answer!r} was accepted without support in the sources (flag {flag})",
    )


@pytest.mark.parametrize("reply", cases.REFUSAL_REPLIES)
async def test_refusal_reply_has_no_sources_or_citations(monkeypatch, reply) -> None:
    response = await _orchestrator_run(
        monkeypatch,
        llm_side_effect=[f"{reply} [1]"],
        post_checks_side_effect=[(True, 0.9, [])],
    )
    assert response.sources == [], _contract(
        "refusal-has-no-sources",
        "app/harness/orchestrator.py (is_refusal branch)",
        f"{reply!r} returned {len(response.sources)} source(s)",
    )
    assert response.citations == []
    assert "[1]" not in response.answer


# ---------------------------------------------------------------------------
# 6. Settings
# ---------------------------------------------------------------------------


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    pattern = re.compile(r"^#?\s*([A-Z][A-Z0-9_]+)=(.*)$")
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line.strip())
        if match:
            values.setdefault(match.group(1), match.group(2).strip())
    return values


def _same(documented: str, default: object) -> bool:
    if isinstance(default, bool):
        return documented.lower() == str(default).lower()
    if isinstance(default, (int, float)):
        try:
            return float(documented) == pytest.approx(float(default))
        except ValueError:
            return False
    return documented == str(default)


@pytest.mark.parametrize(
    ("attr", "env_name", "default", "listed"),
    cases.SETTINGS_CONTRACT,
    ids=[row[1] for row in cases.SETTINGS_CONTRACT],
)
def test_setting_exists_with_documented_default(
    attr: str, env_name: str, default: object, listed: bool
) -> None:
    field = type(get_settings()).model_fields.get(attr)
    assert field is not None, _contract(
        "settings-exist", "app/core/config.py", f"setting {attr} was removed"
    )
    assert field.default == default, _contract(
        "settings-defaults",
        "app/core/config.py",
        f"{attr} default is {field.default!r}, contract says {default!r}",
    )
    documented = _env_example_values().get(env_name)
    if listed:
        assert documented is not None, _contract(
            "settings-in-env-example", "backend/.env.example", f"{env_name} missing"
        )
    if documented is not None:
        assert _same(documented, default), _contract(
            "settings-in-env-example",
            "backend/.env.example",
            f"{env_name}={documented} differs from the default {default!r}",
        )
