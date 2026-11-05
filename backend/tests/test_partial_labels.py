"""The partial line names a gap as a noun phrase, never the raw question."""

import pytest

from app.harness.claims import Draft, Part, _ask_label, _named_gaps, _names


@pytest.mark.parametrize(
    ("ask", "query", "expected"),
    [
        ("who is your CEO", "What is Zero Trust? Who is your CEO?", "our CEO"),
        (
            "what's the weather like today",
            "what's the weather like today and do you cover Intune",
            "the weather today",
        ),
        (
            "Is Microsoft Secure Score as good as a DeployIQ assessment",
            "Is Microsoft Secure Score as good as a DeployIQ assessment?",
            "how Microsoft Secure Score compares with a DeployIQ assessment",
        ),
        (
            "How far is Sydney from Canberra",
            "How far is Sydney from Canberra?",
            "the distance from Sydney to Canberra",
        ),
        (
            "How many employees does Exigo Tech have",
            "How many employees does Exigo Tech have?",
            "the number of employees at Exigo Tech",
        ),
        ("where are your offices", "where are your offices", "where our offices are"),
        ("how does ransomware encrypt files", "How does ransomware encrypt files?", None),
        ("do you have one in Perth", "Where are your offices, and do you have one in Perth?", "an office in Perth"),
        ("the price", "what is the price", "the price"),
    ],
)  # fmt: skip
def test_gap_label_is_a_noun_phrase(ask, query, expected):
    assert _ask_label(ask, query) == expected


def test_bare_name_gap_is_a_name_when_mid_sentence_in_the_question():
    # "Huntress" alone is sentence-initial; the question has it mid-sentence.
    assert _names("Huntress") == [("Huntress", True)]
    assert ("Huntress", False) in _names("Is Huntress better than DeployIQ?")


def test_gate_gap_inside_a_named_gap_is_not_named_twice():

    query = "Do you support Google Workspace? Who is your CEO?"
    draft = Draft(
        "answer",
        [Part("Google Workspace support", False), Part("thanks, bye - oh", False)],
        [],
        "json",
    )
    named = _named_gaps(draft, [], query, ["Google Workspace", "Who is your CEO?"])
    assert named == ["Google Workspace support", "our CEO"]


def test_premise_question_claims_count_as_the_correction():
    from app.harness.claims import Claim  # noqa: PLC0415
    from app.harness.orchestrator import _premise_parts_covered  # noqa: PLC0415

    claim = Claim("Starter covers identity and devices.", 1, "identity and devices", 0)
    draft = Draft("answer", [Part("Zero Trust Professional", False)], [claim], "json")
    premise = (
        "Since the Zero Trust Starter plan covers all seven pillars, "
        "what does Professional add?"
    )
    assert _premise_parts_covered(draft, premise).parts[0].covered
    # No premise: the model's "uncovered" stands.
    assert (
        not _premise_parts_covered(draft, "What is a zero-day exploit?")
        .parts[0]
        .covered
    )


@pytest.mark.parametrize(
    ("query", "terms"),
    [
        ("How does DeployIQ handle PCI DSS compliance?", ["pci", "dss"]),
        ("What is MFA fatigue?", ["mfa", "fatigue"]),
        ("What is e8", []),
        ("What are the steps in Deploy IQ's Zero Trust delivery?", ["zero", "trust"]),
        ("Do you offer 24/7 support?", []),
        ("How many employees does Exigo Tech have?", []),
    ],
)
def test_single_ask_terms_the_answer_must_name(query, terms):
    from app.harness.orchestrator import _ask_terms  # noqa: PLC0415

    assert _ask_terms(query) == terms


def test_answer_naming_none_of_the_asked_terms_is_off_question():
    from app.harness.claims import Claim, GroundedAnswer  # noqa: PLC0415
    from app.harness.orchestrator import _unanswered_terms  # noqa: PLC0415
    from app.schemas.query import SourceChunk  # noqa: PLC0415

    chunk = SourceChunk(
        document_id="d",
        chunk_id="c",
        title="Secure AI",
        content="AI agents assess changes against CIS and ISO 27001 frameworks.",
        score=0.5,
        metadata={},
    )
    answer = GroundedAnswer(
        "x", kept=[Claim("AI agents assess.", 1, "AI agents assess")]
    )
    assert _unanswered_terms("How do you handle PCI DSS?", answer, [chunk]) == [
        "pci",
        "dss",
    ]
    assert (
        _unanswered_terms("Do your AI agents assess against CIS?", answer, [chunk])
        == []
    )


def test_yes_no_ask_about_a_third_party_needs_a_claim_naming_it():
    from app.harness.claims import Claim, GroundedAnswer  # noqa: PLC0415
    from app.harness.orchestrator import _subject_unanswered  # noqa: PLC0415

    def answer(*texts: str) -> GroundedAnswer:
        return GroundedAnswer("x", kept=[Claim(t, 1, t) for t in texts])

    query = "is Microsoft certified for ISO 27001?"
    assert _subject_unanswered(query, answer("Not on its own.")) == "microsoft"
    named = answer("The Essential Eight is a set of eight strategies.")
    assert _subject_unanswered("Is Essential Eight mandatory?", named) is None
    assert _subject_unanswered("Is DeployIQ certified?", answer("No.")) is None


def test_weak_gate_match_tells_generation_to_require_an_explicit_answer():
    from app.harness.decision import PartVerdict, Route, RouteDecision  # noqa: PLC0415
    from app.harness.orchestrator import _weak_match_block  # noqa: PLC0415

    weak = PartVerdict("Do you offer penetration testing services?", True, -9.57)
    strong = PartVerdict("What is Zero Trust?", True, 11.2)
    note = _weak_match_block(RouteDecision(Route.ANSWER, parts=(weak, strong)))
    assert '"Do you offer penetration testing services?"' in note
    assert "What is Zero Trust?" not in note
    assert "no answer" in note
    assert _weak_match_block(RouteDecision(Route.ANSWER, parts=(strong,))) == ""
