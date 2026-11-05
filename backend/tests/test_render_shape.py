"""Answer-shape repairs (app.harness.render_shape) on real KB chunk text."""

from __future__ import annotations

from app.harness.claims import Claim
from app.harness.render_shape import shape_reply
from app.schemas.query import SourceChunk

_SEP = chr(0x203A)
_EM = chr(0x2014)
_EN = chr(0x2013)
_CIS = "CIS Microsoft 365 Foundations Benchmark Assessment"
_SERVICES = "Security Services Delivered with Control and Intent"


def _chunk(
    content: str, heading: str = "", path: list[str] | None = None
) -> SourceChunk:
    return SourceChunk(
        chunk_id=heading or content[:20],
        document_id=content[:12],
        content=content,
        score=0.5,
        metadata={"section_heading": heading, "section_path": path or []},
    )


_SERVICES_LIST = _chunk(
    f"{_SERVICES} {_SEP} {_CIS}\n\nWe assess and configure your tenant against it.\n\n"
    "- 1 Identity (Entra)\n\n- 2 Microsoft Defender\n- 3 Exchange, SharePoint & Teams\n"
    "- 4 Admin Center, Intune & Purview",
    _CIS,
)
_CARD = _chunk(
    f"{_CIS} {_SEP} What does a CIS Microsoft 365 Foundations Benchmark assessment "
    f"cover? {_SEP} 04 {_EM} Admin Center, Intune & Purview\n\n"
    "Tenant-wide governance and data controls.",
    f"04 {_EM} Admin Center, Intune & Purview",
)
_STEPS = _chunk(
    f"{_CIS} {_SEP} How does the CIS Benchmark assessment work?\n\n"
    + "\n".join(
        f"{n}. Step {n} {_EM} {s}"
        for n, s in enumerate(
            [
                "Onboard",
                "Assess",
                "Recommend",
                "Approve",
                "Apply",
                "Validate",
                "Improve",
            ],
            start=1,
        )
    )
    + "\n\nWe implement the highest-impact, lowest-effort fixes first and do not "
    "make any change without your approval.",
    "How does the CIS Benchmark assessment work?",
)


def _texts(claims: list[Claim]) -> list[str]:
    return [c.text for c in claims]


def test_card_number_is_removed() -> None:
    claim = Claim("4 Admin Center, Intune & Purview is one of the CIS areas.", 1, "q")
    out = shape_reply([claim], [claim], [_SERVICES_LIST, _CARD])
    assert _texts(out) == ["Admin Center, Intune & Purview is one of the CIS areas."]


def test_inline_card_numbers_are_removed() -> None:
    text = (
        "The controls covered are 01 Access Control & Identity, 02 Cryptography, "
        "and 03 Logging & Monitoring."
    )
    claim = Claim(text, 1, "q")
    out = shape_reply([claim], [claim], [_CARD])
    assert _texts(out) == [
        "The controls covered are Access Control & Identity, Cryptography, "
        "and Logging & Monitoring."
    ]


def test_quantity_is_kept() -> None:
    claim = Claim("A typical roadmap is 4 to 6 weeks for 365 Users.", 1, "q")
    assert shape_reply([claim], [claim], [_SERVICES_LIST, _CARD]) == [claim]


def test_bare_heading_dropped_next_to_an_answer() -> None:
    heading = Claim("4 Admin Center, Intune & Purview", 1, "4 Admin Center")
    body = Claim("Intune relates to tenant-wide governance and data controls.", 2, "t")
    out = shape_reply([heading, body], [heading, body], [_SERVICES_LIST, _CARD])
    assert _texts(out) == [body.text]


def test_lone_bare_heading_shows_its_card_body() -> None:
    heading = Claim("4 Admin Center, Intune & Purview", 1, "4 Admin Center")
    out = shape_reply([heading], [heading], [_SERVICES_LIST, _CARD])
    assert _texts(out) == [
        "Admin Center, Intune & Purview: Tenant-wide governance and data controls."
    ]
    assert out[0].source == 2  # cited from the card that says it


def test_step_rows_become_items_and_lost_step_is_restored() -> None:
    names = ["Onboard", "Assess", "Recommend"]
    originals = [
        Claim(f"Step {i}, {n}.", 1, f"Step {i}") for i, n in enumerate(names, 1)
    ]
    tail = Claim("We implement the highest-impact fixes first.", 1, "We implement")
    shown = [*(Claim(c.text, 1, c.quote, kind="item") for c in originals[1:])]
    shown.append(Claim(tail.text, 1, tail.quote, kind="item"))
    out = shape_reply(shown, [*originals, tail], [_STEPS])
    assert _texts(out) == [*names, tail.text]
    assert [c.kind for c in out] == ["item", "item", "item", "sentence"]


def test_heading_dash_is_not_a_comma() -> None:
    plan = _chunk(
        f"Zero Trust Plans {_SEP} Zero Trust {_EN} Starter\n\n- Access",
        f"Zero Trust {_EN} Starter",
    )
    claim = Claim("Zero Trust, Starter includes access to the assessment tool.", 1, "q")
    out = shape_reply([claim], [claim], [plan])
    assert _texts(out) == ["Zero Trust Starter includes access to the assessment tool."]


def test_image_annotation_is_cut() -> None:
    claim = Claim("Left ring (red) lists Necessary Controls (top).", 1, "q")
    out = shape_reply([claim], [claim], [_CARD])
    assert _texts(out) == ["Left ring lists Necessary Controls."]


def test_dangling_lead_before_list_ends_in_colon() -> None:
    lead = Claim("Deploy IQ uses information to.", 1, "Deploy IQ uses information to:")
    item = Claim("Respond to enquiries", 1, "- respond to enquiries", kind="item")
    out = shape_reply([lead, item], [lead, item], [_CARD])
    assert _texts(out) == ["Deploy IQ uses information to:", "Respond to enquiries"]


def test_dangling_lead_without_list_is_dropped() -> None:
    lead = Claim("Deploy IQ uses information to.", 1, "Deploy IQ uses information to:")
    other = Claim("It is used to respond to enquiries.", 1, "respond to enquiries")
    out = shape_reply([lead, other], [lead, other], [_CARD])
    assert _texts(out) == [other.text]


def test_relative_clause_split_is_rejoined() -> None:
    text = (
        "ISO 27001 is based on a complete Information Security Management System "
        "that includes policy, risk assessment, people and physical controls, "
        "internal audit, and an independent certification audit."
    )
    original = Claim(text, 1, "ISO 27001 is based on")
    lead = Claim(
        "ISO 27001 is based on a complete Information Security Management System that:",
        1,
        original.quote,
    )
    items = [
        Claim(t, 1, original.quote, kind="item")
        for t in ("Includes policy", "Risk assessment", "Internal audit")
    ]
    out = shape_reply([lead, *items], [original], [_CARD])
    assert _texts(out) == [text]


def test_joined_card_labels_split_and_join_word_cut() -> None:
    iso = _chunk(
        "ISO \n\n- 01 Access Control & Identity\n- 02 Cryptography\n"
        "- 03 Logging & Monitoring\n- 04 Backup & Data Protection",
        "Which ISO 27001 technical controls do we implement in Microsoft 365?",
    )
    items = [
        Claim(t, 1, "q", kind="item")
        for t in ("Cryptography, Logging & Monitoring", "And Backup & Data Protection")
    ]
    out = shape_reply(items, items, [iso])
    assert _texts(out) == [
        "Cryptography",
        "Logging & Monitoring",
        "Backup & Data Protection",
    ]


def test_gap_label_drops_also_frame() -> None:
    from app.harness.claims import _gap_label  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    query = (
        "What CIS benchmark version do you use and do you also support the CIS "
        "benchmark for Google Workspace?"
    )
    label = _gap_label(
        "do you also support the CIS benchmark for Google Workspace?", query
    )
    assert label == "the CIS benchmark for Google Workspace"


def test_step_is_label_rows_become_items() -> None:
    claims = [
        Claim(f"Step {i} is {n}.", 1, f"Step {i}")
        for i, n in enumerate(("Onboard", "Assess"), 1)
    ]
    out = shape_reply(claims, claims, [_STEPS])
    assert _texts(out) == ["Onboard", "Assess"]


def test_step_with_lowercase_prose_is_not_a_row() -> None:
    claims = [
        Claim(f"Step {i} confirms the {n} scope.", 1, f"Step {i}")
        for i, n in enumerate(("onboard", "assess"), 1)
    ]
    assert shape_reply(claims, claims, [_STEPS]) == claims
