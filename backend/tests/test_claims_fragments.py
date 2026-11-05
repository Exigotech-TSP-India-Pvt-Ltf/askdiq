"""Heading fragments, truncated bullets, near-duplicates and hard length caps
of quote-backed answers (app.harness.claims).

Deterministic code contract only (mocked model replies); behaviour evidence
comes from scripts/live_golden_eval.py against a live server.
"""

from __future__ import annotations

import json

from app.harness.claims import (
    Claim,
    _Chunk,
    _fragment_reason,
    ground_answer,
    parse_draft,
)
from app.schemas.query import SourceChunk

BOOKING = (
    "Booking\n\n### Talk with Principal Security Consultation\n\n"
    "You will speak directly with a Deploy IQ security and compliance expert.\n\n"
    "### Session Details\n\n- Kick-off & Discovery\n- 30 minutes\n"
    "- Web conference details to follow\n"
)
HOME = (
    "Home\n\n## Build the right capability at every stage.\n\n"
    "Eyebrow: Your security maturity path\n\n"
    "Start where you are today. Each stage builds on the last, so your "
    "investment stays focused.\n\n**What you get**\n\n- Control mapping\n"
    "- Audit-ready evidence\n"
)
ISO = (
    "ISO 42001\n\n**Technical controls, not certification.** ISO/IEC 42001 "
    "also covers policy, risk assessment and roles, which stay with your "
    "organisation.\n"
)
ABOUT = (
    "About\n\nThe same governed journey connects onboarding to ongoing "
    "improvement, with human approval at every stage:\n\n"
    "- Human-approved\n- AI AUTOMATION\n"
)
PACKAGES = (
    "Packages\n\nRisk-based access and just-in-time admin are included in "
    "Strengthen and Frontier, and not in Baseline or Foundation. Data "
    "protection and loss prevention are included in Strengthen and Frontier, "
    "and not in Baseline or Foundation. Application allowlisting is included "
    "in Strengthen and Frontier, and not in Baseline or Foundation.\n"
)
CONTACT = "Contact\n\nEmail: info@deployiq.co\n\nContact: info@deployiq.co\n"


def _source(content: str, n: int = 1) -> SourceChunk:
    return SourceChunk(
        chunk_id=f"00000000-0000-0000-0000-00000000000{n}",
        document_id=f"00000000-0000-0000-0000-00000000010{n}",
        content=content,
        score=0.8,
        metadata={"source_name": f"page{n}.md"},
    )


def _c(q: str, t: str = "", s: int = 1, p: int = 0, k: str = "s") -> dict:
    return {"p": p, "s": s, "q": q, "t": t, "k": k}


def _answer(claims, sources, query, parts=None):
    parts = parts or [{"a": "the question", "c": True}]
    raw = json.dumps({"policy": "none", "parts": parts, "claims": claims})
    draft = parse_draft(raw)
    assert draft is not None
    return ground_answer(draft, sources, query)


def _reasons(answer) -> list[str]:
    return [reason for _, reason in answer.dropped]


def test_a_badge_value_not_asked_for_is_dropped():
    answer = _answer(
        [
            _c(
                "You will speak directly with a Deploy IQ security and "
                "compliance expert."
            ),
            _c("30 minutes", "30 minutes."),
        ],
        [_source(BOOKING)],
        "What type of session is offered on the booking page?",
    )
    assert "30 minutes" not in answer.text
    assert "heading_fragment" in _reasons(answer)


def test_the_asked_for_value_is_kept():
    answer = _answer(
        [_c("30 minutes", "30 minutes.")],
        [_source(BOOKING)],
        "How long is the booking session?",
    )
    assert answer.text.startswith("30 minutes.")


def test_eyebrow_and_section_heading_sentences_are_dropped():
    answer = _answer(
        [
            _c("Eyebrow: Your security maturity path", "Your security maturity path."),
            _c(
                "Start where you are today.",
                "Start where you are today.",
            ),
            _c("What you get", "What you get."),
            _c("Control mapping", k="i"),
            _c("Audit-ready evidence", k="i"),
        ],
        [_source(HOME)],
        "what is the Fortify stage",
    )
    assert "maturity path" not in answer.text
    assert "What you get" not in answer.text
    assert "- Control mapping [1]" in answer.text


def test_bold_scope_note_heading_is_dropped():
    answer = _answer(
        [
            _c(
                "Technical controls, not certification.",
                "Technical controls, not certification.",
            ),
            _c(
                "ISO/IEC 42001 also covers policy, risk assessment and roles, "
                "which stay with your organisation."
            ),
        ],
        [_source(ISO)],
        "Which parts of ISO/IEC 42001 stay with our organisation?",
    )
    assert "Technical controls" not in answer.text
    assert "policy, risk assessment and roles" in answer.text


def test_two_heading_shards_in_one_claim_are_dropped():
    answer = _answer(
        [
            _c("Human-approved", "Human-approved. AI AUTOMATION."),
            _c(
                "The same governed journey connects onboarding to ongoing "
                "improvement, with human approval at every stage",
            ),
        ],
        [_source(ABOUT)],
        "What's DeployIQ's tagline?",
    )
    assert "AI AUTOMATION" not in answer.text


def test_a_lead_colon_without_a_list_ends_as_a_sentence():
    answer = _answer(
        [
            _c(
                "The same governed journey connects onboarding to ongoing "
                "improvement, with human approval at every stage:"
            )
        ],
        [_source(ABOUT)],
        "how does deploy iq work?",
    )
    assert "every stage. [1]" in answer.text
    assert ":" not in answer.text


def test_shared_tail_bullets_are_never_cut_before_their_complement():
    sentences = [
        "Risk-based access and just-in-time admin are included in Strengthen "
        "and Frontier, and not in Baseline or Foundation.",
        "Data protection and loss prevention are included in Strengthen and "
        "Frontier, and not in Baseline or Foundation.",
        "Application allowlisting is included in Strengthen and Frontier, and "
        "not in Baseline or Foundation.",
    ]
    answer = _answer(
        [_c(s) for s in sentences], [_source(PACKAGES)], "what's on this page?"
    )
    for line in answer.text.splitlines():
        assert not line.rstrip(" [1]").endswith((" are", " is"))
    assert "- Data protection and loss prevention are [1]" not in answer.text


def test_the_same_email_from_two_claims_ships_once():
    answer = _answer(
        [
            _c("info@deployiq.co", "info@deployiq.co."),
            _c("Contact: info@deployiq.co", "Contact: info@deployiq.co.", p=1),
            _c("Email: info@deployiq.co", "Email: info@deployiq.co.", p=2),
        ],
        [_source(CONTACT)],
        "what's the email address for your team?",
        [{"a": f"ask {i}", "c": True} for i in range(3)],
    )
    assert answer.text.count("info@deployiq.co") == 1


def test_a_restated_sentence_from_another_part_ships_once():
    page = (
        "Services\n\nAI-assisted analysis is used only within governed "
        "boundaries. Deploy IQ uses AI-assisted analysis only within governed "
        "boundaries.\n"
    )
    answer = _answer(
        [
            _c("AI-assisted analysis is used only within governed boundaries."),
            _c(
                "Deploy IQ uses AI-assisted analysis only within governed boundaries.",
                p=1,
            ),
        ],
        [_source(page)],
        "How does Deploy IQ use AI?",
        [{"a": "ai use", "c": True}, {"a": "boundaries", "c": True}],
    )
    assert answer.text.count("governed boundaries") == 1


def test_repeated_lead_ins_collapse_under_one_lead():
    page = (
        "Strengthen\n\n- Configuration checks\n- Risk-based findings\n"
        "- Guided fixes\n\nIt adds risk-based access where sign-in decisions "
        "adapt to user and session risk. It adds just-in-time admin access.\n"
    )
    items = [
        (
            "Configuration checks",
            "The Strengthen solution includes configuration checks",
        ),
        ("Risk-based findings", "The Strengthen solution includes risk-based findings"),
        ("Guided fixes", "The Strengthen solution includes guided fixes"),
        (
            "It adds risk-based access where sign-in decisions adapt to user and "
            "session risk.",
            "",
        ),
        ("It adds just-in-time admin access.", ""),
    ]
    answer = _answer(
        [_c(q, t, k="i") for q, t in items],
        [_source(page)],
        "What do I get with the Strengthen solution?",
    )
    assert answer.text.count("Strengthen solution includes") == 1
    assert "It adds" not in answer.text
    assert "- Configuration checks [1]" in answer.text


def test_bullet_cap_is_per_part_and_words_stay_capped():
    pillars = [
        "Identity", "Devices", "Network", "Infrastructure", "Applications",
        "Data", "Visibility", "Automation", "Governance", "Resilience",
    ]  # fmt: skip
    steps = ["Assess", "Design", "Assure"]
    page = "Page\n\n" + "\n".join(f"- {b}" for b in [*pillars, *steps])
    claims = [_c(b, k="i", p=0) for b in pillars]
    claims += [_c(b, k="i", p=1) for b in steps]
    answer = _answer(
        claims,
        [_source(page)],
        "1) What are the Zero Trust pillars? 2) What are your delivery steps?",
        [{"a": f"ask {i}", "c": True} for i in range(2)],
    )
    lines = answer.text.splitlines()
    assert sum(f"- {p} [1]" in lines for p in pillars) == 8
    for step in steps:
        assert f"- {step} [1]" in lines
    assert "[1]\n\n- Assess [1]" in answer.text  # each part's own list
    body = [w for w in answer.text.split() if not w.startswith("[")]
    assert len(body) <= 150


def test_run_on_series_renders_as_a_capped_list():
    sentence = (
        "The service includes scoped, phased implementation, maturity "
        "assessment, risk-aware prioritisation, incremental implementation, "
        "control alignment, audit readiness, governed delivery, practical "
        "outcomes, low-risk deployment, and human oversight."
    )
    answer = _answer([_c(sentence)], [_source(f"E8\n\n{sentence}\n")], "buy e8")
    lines = answer.text.splitlines()
    assert lines[0].startswith("The service includes:")
    assert "- Scoped, phased implementation [1]" in lines
    assert sum(line.startswith("- ") for line in lines) == 8


def test_shared_tail_bullets_lose_the_repeated_tail():
    rows = ["Cryptography", "Logging & Monitoring", "Secure Configuration"]
    tail = " is included as a technical control we implement in Microsoft 365."
    page = "ISO\n\n" + "\n".join(f"- {r}{tail}" for r in rows)
    answer = _answer(
        [_c(f"{r}{tail}", k="i") for r in rows], [_source(page)], "iso 27001?"
    )
    assert answer.text.count("technical control we implement") == 1
    assert "- Logging & Monitoring [1]" in answer.text


def test_title_case_heading_sentence_is_dropped():
    page = (
        "Strengthen\n\n**Stop Threats Before They Run**\n\n"
        "Recommended solution\n\nRisk-based access makes sign-in decisions "
        "that adapt to user and session risk.\n"
    )
    answer = _answer(
        [
            _c("Stop Threats Before They Run", "Stop Threats Before They Run."),
            _c("Recommended solution", "Recommended solution."),
            _c(
                "Risk-based access makes sign-in decisions that adapt to user "
                "and session risk."
            ),
        ],
        [_source(page)],
        "I'm ready to sign up for the Strengthen package",
    )
    assert "Stop Threats" not in answer.text
    assert "Recommended solution" not in answer.text
    assert answer.text.startswith("Risk-based access")


def test_a_lead_left_hanging_on_plus_is_dropped():
    page = (
        "Foundation\n\nEverything in Baseline, plus:\n\n"
        "Microsoft 365 hardening applies the baseline across identity, email, "
        "devices and data.\n"
    )
    answer = _answer(
        [
            _c(
                "Everything in Baseline, plus",
                "The Foundation package adds Everything in Baseline, plus.",
            ),
            _c(
                "Microsoft 365 hardening applies the baseline across identity, "
                "email, devices and data."
            ),
        ],
        [_source(page)],
        "what packages do you offer",
    )
    assert "plus" not in answer.text
    assert answer.text.startswith("Microsoft 365 hardening")


def test_fragment_check_never_reads_a_chunk_for_an_out_of_range_source():
    chunks = [_Chunk(_source(BOOKING))]
    for source in (0, -1, 2):
        claim = Claim("30 minutes.", source, "30 minutes")
        assert _fragment_reason(claim, chunks, "what session?") is None
    claim = Claim("30 minutes.", 1, "30 minutes")
    assert _fragment_reason(claim, chunks, "what session?") == "heading_fragment"
