"""Pure parts of scripts/live_golden_eval.py: fixed lines and persona templates."""

from __future__ import annotations

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
