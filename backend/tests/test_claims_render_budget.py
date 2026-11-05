"""Rendering, length budget and gap naming of quote-backed answers
(app.harness.claims, app.harness.orchestrator covered retry).

Deterministic code contract only (mocked model replies); behaviour evidence
comes from scripts/live_golden_eval.py against a live server.
"""

from __future__ import annotations

import json

from app.harness import claims as claims_module
from app.harness.claims import Claim, GroundedAnswer, Part, ground_answer, parse_draft
from app.harness.orchestrator import _merged_draft
from app.schemas.query import SourceChunk

SEP = chr(0x203A)  # breadcrumb separator (app.chunking.section_tree)
PILLARS = (
    f"Zero Trust Security {SEP} Zero Trust Pillars\n\n"
    "Identity\nDevices\nNetwork\nInfrastructure\nApplications\nData\n"
)
STARTER = (
    f"Security Services {SEP} Zero Trust - Starter\n\n"
    "- Access to the Microsoft Zero Trust Assessment tool\n"
    "- Assessment coverage limited to identity and devices\n"
)
ZT = (
    f"Zero Trust Security {SEP} What Is Zero Trust?\n\n"
    "Zero Trust is a security model that removes implicit trust from "
    "technology environments. It is a global approach, applicable across "
    "cloud, hybrid, and on-prem environments."
)
FRONTIER = (
    f"Microsoft 365 Security Packages {SEP} Package cards {SEP} Frontier\n\n"
    "Stage 4\n\nSecure, AI-Ready and Watched 24/7\n\n"
    "- 24/7 Managed Detection & Response: round-the-clock threat monitoring"
)


def _source(content: str, n: int = 1, heading: str = "") -> SourceChunk:
    return SourceChunk(
        chunk_id=f"00000000-0000-0000-0000-00000000000{n}",
        document_id=f"00000000-0000-0000-0000-00000000010{n}",
        content=content,
        score=0.8,
        metadata={"source_name": f"page{n}.md", "section_heading": heading},
    )


def _c(q: str, t: str = "", s: int = 1, p: int = 0, k: str = "s") -> dict:
    return {"p": p, "s": s, "q": q, "t": t, "k": k}


def _answer(claims, sources, query, parts, **kwargs):
    raw = json.dumps({"policy": "none", "parts": parts, "claims": claims})
    draft = parse_draft(raw)
    assert draft is not None
    return ground_answer(draft, sources, query, **kwargs)


def test_bare_labels_written_as_sentences_render_as_a_list():
    labels = ["Identity", "Devices", "Network", "Infrastructure"]
    answer = _answer(
        [_c(label) for label in labels],
        [_source(PILLARS)],
        "What are the Zero Trust pillars?",
        [{"a": "the Zero Trust pillars", "c": True}],
    )
    assert answer.text.splitlines()[:4] == [f"- {label} [1]" for label in labels]


def test_a_gate_gap_naming_what_no_kept_claim_covers_is_named():
    # The model claims the AWS part with a related fact; the gate found no
    # evidence for it, and no kept claim's chunk mentions AWS.
    answer = _answer(
        [
            _c("Assessment coverage limited to identity and devices", p=0),
            _c(
                "It is a global approach, applicable across cloud, hybrid, and "
                "on-prem environments.",
                s=2,
                p=1,
            ),
        ],
        [_source(STARTER), _source(ZT, 2)],
        "What is included in the Zero Trust Starter plan, also do you support "
        "AWS environments?",
        [
            {"a": "What is included in the Zero Trust Starter plan", "c": True},
            {"a": "Do you support AWS environments", "c": True},
        ],
        gap_parts=["do you support AWS environments?"],
    )
    assert "identity and devices" in answer.text
    assert "AWS" in answer.text.rsplit("\n\n", 1)[-1]
    assert answer.uncovered


def test_a_gate_gap_a_kept_claim_answers_is_not_named():
    answer = _answer(
        [_c("Assessment coverage limited to identity and devices")],
        [_source(STARTER)],
        "Does the Starter plan cover identity?",
        [{"a": "Does the Starter plan cover identity", "c": True}],
        gap_parts=["does the Starter plan cover identity?"],
    )
    assert not answer.uncovered


def test_a_part_naming_its_own_framework_is_not_held_to_another_parts_subject():
    answer = _answer(
        [
            _c(
                "Zero Trust is a security model that removes implicit trust "
                "from technology environments.",
                p=0,
            ),
            _c(
                "Secure, AI-Ready and Watched 24/7",
                t="Frontier is secure, AI-ready and watched 24/7.",
                s=2,
                p=1,
            ),
        ],
        [_source(ZT), _source(FRONTIER, 2, heading="Frontier")],
        "zero trust basics; frontier package",
        [{"a": "zero trust basics", "c": True}, {"a": "frontier package", "c": True}],
    )
    assert "implicit trust" in answer.text


def test_budget_reserves_each_later_parts_first_claim(monkeypatch):
    monkeypatch.setattr(
        claims_module.get_settings(), "answer_max_words", 30, raising=False
    )
    answer = _answer(
        [
            _c(
                "Zero Trust is a security model that removes implicit trust "
                "from technology environments.",
                p=0,
            ),
            _c(
                "It is a global approach, applicable across cloud, hybrid, and "
                "on-prem environments.",
                p=0,
            ),
            _c("Assessment coverage limited to identity and devices", s=2, p=1),
        ],
        [_source(ZT), _source(STARTER, 2)],
        "What is Zero Trust, and what does the Starter plan cover?",
        [
            {"a": "What is Zero Trust", "c": True},
            {"a": "what does the Starter plan cover", "c": True},
        ],
    )
    # The second Zero Trust sentence would fit alone, but not beside the
    # Starter part's first claim: it is the one trimmed.
    assert "implicit trust" in answer.text
    assert "on-prem" not in answer.text
    assert "identity and devices" in answer.text


def test_a_re_ask_for_the_missing_part_keeps_the_parts_already_answered():
    first = Claim("Zero Trust removes implicit trust.", 1, "removes implicit trust")
    retried = Claim("Coverage is identity and devices.", 2, "identity and devices")
    current = GroundedAnswer(
        "x",
        kept=[first],
        uncovered=["the Starter plan"],
        parts=[Part("zero trust", True), Part("the Starter plan", True)],
    )
    result = GroundedAnswer("y", kept=[retried], parts=[Part("the Starter plan", True)])
    draft = _merged_draft(current, result)
    assert [p.ask for p in draft.parts] == ["zero trust", "the Starter plan"]
    assert [(c.text, c.part) for c in draft.claims] == [
        (first.text, 0),
        (retried.text, 1),
    ]
