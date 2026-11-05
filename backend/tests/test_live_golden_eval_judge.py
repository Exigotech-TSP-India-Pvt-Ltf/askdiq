"""Pure parts of scripts/live_golden_eval.py: fixed lines and persona templates."""

from __future__ import annotations

from app.evaluation import judge_extra as je
from app.harness import prompt_catalog as cat
from scripts import live_golden_eval as lge


def _texts() -> lge.ExpectedTexts:
    return lge.ExpectedTexts()


def test_judge_does_not_see_the_code_added_fixed_lines() -> None:
    sales = cat.sales_contact_line("1300 394 468", "info@deployiq.co")
    answer = "\n\n".join(
        [
            "The assessment covers 42 controls. [1]",
            f"{cat.PRICING_LINE} {sales} [2]",
            cat.partial_line(["the price", "the CEO"]),
            cat.PAGE_LINK_LINE.format(url="https://deployiq.co/m365"),
        ]
    )
    assert _texts().judged_answer(answer) == "The assessment covers 42 controls."


def test_an_answer_of_only_fixed_lines_has_nothing_to_judge() -> None:
    answer = f"{cat.PRICING_LINE}\n\n{cat.sales_contact_line('1300 1', 'a@b.co')} [1]"
    assert not _texts().judged_answer(answer).strip()


def test_facts_around_a_fixed_line_are_kept() -> None:
    answer = f"- Identity checks [1]\n- Email checks [1]\n\n{cat.partial_line([])}"
    assert _texts().judged_answer(answer) == "- Identity checks\n- Email checks"


def test_persona_templates_are_known_by_their_logged_id() -> None:
    texts = _texts()
    for intent in cat.PERSONA_TEMPLATES:
        template_id = cat.persona_template_id(intent)
        expected = texts.for_flags(["identity_probe"], template_id)
        reply = lge.norm(cat.persona_template(intent, privacy_url="https://x.co/p"))
        assert any(lge.fixed_match(reply, t) for t in expected), intent


def test_wrong_persona_reply_still_fails() -> None:
    texts = _texts()
    expected = texts.for_flags(["identity_probe"], cat.persona_template_id("human"))
    reply = lge.norm(cat.persona_template("memory"))
    assert not any(lge.fixed_match(reply, t) for t in expected)


# ---------------------------------------------------------------- fixed lines
def test_partial_line_with_any_gap_text_is_stripped() -> None:
    gaps = [
        ["a joke"],
        ["what uptime SLA do you guarantee"],
        ["the capital of Japan, the CEO", "your prices"],
        ["how many employees does Exigo Tech have?"],
    ]
    for parts in gaps:
        line = cat.partial_line(parts)
        curly = line.replace("'", chr(0x2019))
        for variant in (line, curly, f"{line} [2]"):
            answer = f"Frontier adds 24/7 MDR. [1]\n\n{variant}"
            assert _texts().judged_answer(answer) == "Frontier adds 24/7 MDR.", parts


def test_no_answer_and_try_again_lines_are_not_claims() -> None:
    for line in (cat.NO_ANSWER_LINE, cat.TRY_AGAIN_LINE):
        assert not _texts().judged_answer(line).strip()


# ---------------------------------------------------------------- claim filter
BRANDS = je.brand_phrases(["DeployIQ"])
ISO_SOURCE = (
    "We focus on the technical controls inside your Microsoft 365 environment. "
    "Policy, risk ownership and certification stay with your organisation.\n"
    "Technical controls, not certification. Certification is carried out by an "
    "accredited certification body."
)
E8_SOURCE = (
    "Achieve ACSC Essential Eight compliance with our structured implementation "
    "services. Assessments cover 8 strategies."
)


def _scores(*claims: tuple[str, bool]) -> je.DeepJudgeScores:
    return je.DeepJudgeScores(
        claims=[{"claim": c, "supported": s, "needs_citation": True} for c, s in claims]
    )


def test_claims_the_answer_does_not_make_are_dropped() -> None:
    answer = "The Strengthen package includes everything in Foundation."
    scores = je.refine_claims(
        _scores(
            ("The Strengthen package includes everything in Foundation.", True),
            ("The capital of Japan is Tokyo.", False),
            ("Told a joke.", False),
        ),
        answer,
        [],
        "Tell me a joke, what's in Strengthen and what's the capital of Japan?",
        BRANDS,
    )
    assert [c["claim"] for c in scores.claims] == [
        "The Strengthen package includes everything in Foundation."
    ]
    assert scores.dropped_claims == ["The capital of Japan is Tokyo.", "Told a joke."]
    assert scores.groundedness == 1.0


def test_terse_answer_claims_keep_a_fabricated_item() -> None:
    answer = "- Pilot\n- Validate\n- Launch party"
    scores = je.refine_claims(
        _scores(
            ("The Essential Eight stages are Pilot, Validate and Launch party.", False)
        ),
        answer,
        [E8_SOURCE],
        "What are the stages of your Essential Eight service?",
        BRANDS,
    )
    assert len(scores.claims) == 1
    assert scores.claims[0]["supported"] is False


def test_unsupported_claim_found_in_a_source_is_not_fabricated() -> None:
    answer = (
        "Policy, risk ownership and certification stay with your organisation. "
        "DeployIQ implements the ACSC Essential Eight."
    )
    scores = je.refine_claims(
        _scores(
            ("Certification stays with your organisation.", False),
            ("DeployIQ implements the ACSC Essential Eight.", False),
        ),
        answer,
        [ISO_SOURCE, E8_SOURCE],
        "Which parts stay with us, and whose Essential Eight do you implement?",
        BRANDS,
    )
    assert all(c["supported"] for c in scores.claims)
    assert {c.get("supported_by") for c in scores.claims} == {"source_overlap"}


def test_source_overlap_keeps_actor_negation_and_numbers() -> None:
    chunks = [ISO_SOURCE, E8_SOURCE]
    assert not je.in_sources("Certification stays with DeployIQ.", chunks, BRANDS)
    assert not je.in_sources(
        "Certification is carried out by Deploy IQ.", chunks, BRANDS
    )
    assert not je.in_sources(
        "DeployIQ does not implement the ACSC Essential Eight.", chunks, BRANDS
    )
    assert not je.in_sources("Assessments cover 12 strategies.", chunks, BRANDS)
    assert je.in_sources("Assessments cover 8 strategies.", chunks, BRANDS)
