"""Phase 4: multi-part split, calibrated gate routing and the deterministic
clarification mechanism (offline; live evidence comes from /query runs)."""

from __future__ import annotations

import pytest

from app.harness import clarify
from app.harness.clarify import (
    ClarifyDecision,
    ServiceDoc,
    decide_clarification,
    page_label,
    resolve_clarification_reply,
    service_card,
)
from app.harness.decision import Route
from app.retrieval.retriever import (
    PartRetrieval,
    QueryPart,
    _service_search,
    _standalone,
    decide_route,
    decompose_query,
    expand_retrieval_query,
    is_bare_reference,
    split_query,
    trust_clarified,
)
from app.schemas.query import ChatTurn, Clarification, SourceChunk

# (message, expected number of parts) from the audited multi-part set.
MULTI_PART = [
    (
        "What is the Essential Eight and how long does it typically take to implement?",
        2,
    ),
    (
        "What does the Microsoft 365 Security Baseline Assessment check? Who is the CEO of Exigo Tech?",
        2,
    ),
    ("Compare Zero Trust and Essential Eight", 2),
    (
        "What is included in the Zero Trust Starter plan, also do you support AWS environments?",
        2,
    ),
    ("Tell me what ISO 42001 covers and what's the weather in Sydney today", 2),
    (
        "What does the Frontier package include\nWill DeployIQ certify us against ISO 42001",
        2,
    ),
    ("Is the Zero Trust Starter plan free and what does it cover?", 2),
    (
        "What is the Essential Eight, how long does it take and which maturity levels do you cover?",
        3,
    ),
    ("compare Zero Trust, Essential Eight and CIS", 3),
    (
        "1) What are the Zero Trust pillars? 2) What are the steps of your delivery "
        "approach? 3) What outcomes can we expect?",
        3,
    ),
    ("1. what packages do you offer\n2. what is MDR\n3. can I upgrade later", 3),
    (
        "What is the Baseline package; what is the Strengthen package; what is the Frontier package",
        3,
    ),
    (
        "How does the CIS assessment work, will you change anything without approval, "
        "and how often do you recheck my tenant?",
        3,
    ),
    (
        "What are the Essential Eight strategies, how long does implementation take, which "
        "maturity levels do you support and do you provide audit evidence?",
        4,
    ),
    (
        "Can you explain Responsible AI, Secure AI, the platform and your data handling?",
        4,
    ),
    (
        "Tell me about the Zero Trust pillars and also Essential Eight maturity levels and "
        "also ISO 27001 cryptography controls and also the Security Baseline backup area",
        4,
    ),
    (
        "What is Zero Trust? What is the Essential Eight? What is CIS? What is ISO 42001? What is MDR?",
        5,
    ),
]


@pytest.mark.parametrize(("message", "count"), MULTI_PART)
def test_multi_part_split_counts(message: str, count: int) -> None:
    assert len(split_query(message)) == count


@pytest.mark.parametrize(
    "message",
    [
        "What is Deploy IQ?",
        "How does Deploy IQ handle customer data?",
        "Exchange and SharePoint security",
        "We are a bakery. Do you do ISO 27001?",
        "security",
    ],
)
def test_single_asks_are_not_split(message: str) -> None:
    assert decompose_query(message) == [" ".join(message.split())]


def test_parts_are_never_folded_beyond_five() -> None:
    message = " ".join(f"What is item {i}?" for i in range(1, 8))
    assert len(split_query(message)) == 7  # noqa: PLR2004


def test_referential_part_carries_the_subject() -> None:
    parts = split_query("Is the Zero Trust Starter plan free and what does it cover?")
    assert parts[1].text == "what does it cover?"
    assert parts[1].search == "what does Zero Trust Starter plan cover?"
    elliptic = split_query(
        "What is the Essential Eight and how long does it typically take?"
    )
    assert "Essential Eight" in elliptic[1].search


def test_named_part_keeps_its_own_subject() -> None:
    parts = split_query(
        "What is the Essential Eight? And what is the capital of France?"
    )
    assert parts[1].search == parts[1].text


def test_comparison_gives_one_part_per_compared_thing() -> None:
    parts = split_query("Is Zero Trust better than Essential Eight?")
    assert [p.text for p in parts] == ["Zero Trust", "Essential Eight"]
    assert all(p.comparison for p in parts)


def test_follow_up_carries_the_conversation_subject() -> None:
    history = [
        ChatTurn(role="user", content="Tell me about the Zero Trust Starter plan"),
        ChatTurn(role="assistant", content="It gives access to the assessment tool."),
    ]
    resolved = _standalone(
        QueryPart("how do I get the tool?", "how do I get the tool?"), history
    )
    assert "Zero Trust Starter plan" in resolved.search
    own = _standalone(QueryPart("What is ISO 42001?", "What is ISO 42001?"), history)
    assert own.search == "What is ISO 42001?"


def test_buying_ask_searches_for_the_named_service() -> None:
    q = "How do I purchase the Zero Trust Professional plan?"
    assert "purchase" not in _service_search(QueryPart(q, q)).search
    assert _service_search(QueryPart("sign me up", "sign me up")).search == "sign me up"


def test_bare_product_words_are_expanded_for_retrieval() -> None:
    assert expand_retrieval_query("intune") == "Microsoft Intune endpoint management"
    assert expand_retrieval_query("m365") == "Microsoft 365"
    assert expand_retrieval_query("e8") == "Essential Eight"


def _part(text: str, ce: float, cos: float = 0.5, doc: str = "d1") -> PartRetrieval:
    return PartRetrieval(
        query=text,
        max_score=cos,
        max_ce=ce,
        chunk_ids=frozenset({f"{doc}-c"}),
        contents=("x",),
        document_ids=(doc,),
    )


@pytest.fixture
def _thresholds(monkeypatch):
    s = clarify.get_settings()
    monkeypatch.setattr(s, "retrieval_ce_covered_min", -8.5)
    monkeypatch.setattr(s, "retrieval_ce_relevant_min", -3.0)
    monkeypatch.setattr(s, "retrieval_ce_confident_min", 1.5)
    monkeypatch.setattr(s, "retrieval_offdomain_max_cosine", 0.25)
    return s


@pytest.mark.usefixtures("_thresholds")
def test_route_answer_partial_not_covered_off_topic() -> None:
    assert decide_route([_part("a", 6.0), _part("b", 2.0)]).route is Route.ANSWER
    partial = decide_route([_part("a", 6.0), _part("weather?", -10.8, cos=0.2)])
    assert partial.route is Route.PARTIAL
    assert [v.covered for v in partial.parts] == [True, False]
    assert partial.parts[1].document_ids == ()
    assert decide_route([_part("SOC 2?", -9.0, cos=0.41)]).route is Route.NOT_COVERED
    assert decide_route([_part("burger", -11.2, cos=0.07)]).route is Route.OFF_TOPIC


@pytest.mark.usefixtures("_thresholds")
def test_price_part_of_a_multi_part_ask_is_its_own_uncovered_part() -> None:
    decision = decide_route(
        [
            _part("What does Strengthen include", 5.7),
            _part("how much does it cost?", -7.2),
        ],
        buying=True,
    )
    assert decision.route is Route.PARTIAL
    assert decision.buying


@pytest.mark.usefixtures("_thresholds")
def test_clarification_reply_is_trusted_unless_off_topic() -> None:
    trusted = trust_clarified(decide_route([_part("long story", -9.0, cos=0.5)]))
    assert trusted.route is Route.ANSWER
    off = trust_clarified(decide_route([_part("burger", -11.0, cos=0.05)]))
    assert off.route is Route.OFF_TOPIC


def _source(doc: str, name: str, title: str, chunk: str) -> SourceChunk:
    return SourceChunk(
        chunk_id=chunk,
        document_id=doc,
        content="c",
        score=0.5,
        metadata={"source_name": name},
        title=title,
    )


SERVICE_SOURCES = [
    _source(
        "e8", "essential-eight.md", "Essential Eight Compliance - Deploy IQ", "e8-1"
    ),
    _source(
        "iso",
        "iso-27001-microsoft-365-technical-controls.md",
        "ISO 27001 Technical Controls in Microsoft 365 - Deploy IQ",
        "iso-1",
    ),
    _source(
        "home", "home.md", "DeployIQ | AI-Powered Cybersecurity & Compliance", "home-1"
    ),
]
CE = {"e8-1": -0.9, "iso-1": -1.8, "home-1": 0.2}


def _decide(query: str, **kw) -> ClarifyDecision:
    args = {
        "sources": SERVICE_SOURCES,
        "ce_by_chunk": CE,
        "max_ce": 0.2,
        "route": "answer",
        "buying": False,
        "clarification_reply": False,
        "parts": 1,
    }
    args.update(kw)
    return decide_clarification(query, None, **args)


def test_page_label_drops_the_site_suffix() -> None:
    assert page_label(SERVICE_SOURCES[0]) == "Essential Eight Compliance"


@pytest.mark.usefixtures("_thresholds")
@pytest.mark.parametrize("query", ["How do I get compliant?", "compliance", "audit"])
def test_generic_kb_question_across_services_asks(query: str) -> None:
    decision = _decide(query)
    assert decision.ask
    card = service_card(query, decision, [])
    assert card is not None
    assert [o.label for o in card.questions[0].options] == [
        "Essential Eight Compliance",
        "ISO 27001 Technical Controls in Microsoft 365",
    ]
    assert all(o.document_ids for o in card.questions[0].options)


@pytest.mark.usefixtures("_thresholds")
@pytest.mark.parametrize("query", ["e8", "zero trust?", "intune", "entra", "free"])
def test_bare_kb_entity_is_answered_not_carded(query: str) -> None:
    assert not _decide(query).ask


@pytest.mark.usefixtures("_thresholds")
def test_off_domain_is_never_clarified() -> None:
    assert not _decide("price of a burger", route="off_topic", buying=True).ask
    assert not _decide("what is a cat?", route="off_topic").ask


@pytest.mark.usefixtures("_thresholds")
def test_buying_without_a_service_asks_which_service() -> None:
    for query in ("pricing", "sign me up", "Can I get a quote?"):
        decision = _decide(query, buying=True, route="not_covered", max_ce=-10.0)
        assert decision.ask and decision.buying, query


@pytest.mark.usefixtures("_thresholds")
def test_buying_with_a_service_or_the_brand_is_answered() -> None:
    assert not _decide("How much is the Frontier package?", buying=True).ask
    assert not _decide("Can I get a demo of DeployIQ?", buying=True).ask
    booking = _decide("How do I book a consultation?", buying=True, max_ce=-0.3)
    assert not booking.ask


@pytest.mark.usefixtures("_thresholds")
def test_dangling_reference_asks_even_below_the_gate() -> None:
    assert _decide("how long does it take?", route="off_topic", max_ce=-4.3).ask


@pytest.mark.usefixtures("_thresholds")
def test_no_card_for_multi_part_or_reply_or_streak() -> None:
    assert not _decide("compliance", parts=2).ask
    assert not _decide("compliance", clarification_reply=True).ask


def test_service_card_fills_from_the_catalogue_and_caps_options() -> None:
    decision = ClarifyDecision(
        ask=True, docs=(ServiceDoc("e8", "Essential Eight Compliance", 1.0),)
    )
    catalogue = [ServiceDoc(f"d{i}", f"Service {i}", 0.0) for i in range(6)]
    card = service_card("pricing", decision, catalogue)
    labels = [o.label for o in card.questions[0].options]
    assert labels[0] == "Essential Eight Compliance"
    assert len(labels) == 4  # noqa: PLR2004


def _card_history() -> list[ChatTurn]:
    decision = ClarifyDecision(
        ask=True,
        docs=(
            ServiceDoc("e8", "Essential Eight Compliance", 1.0),
            ServiceDoc("zt", "Zero Trust Services", 0.5),
            ServiceDoc("iso", "ISO 27001 Technical Controls in Microsoft 365", 0.2),
        ),
    )
    card: Clarification = service_card("How do I get compliant?", decision, [])
    return [
        ChatTurn(role="user", content="How do I get compliant?"),
        ChatTurn(
            role="assistant",
            content="Which service?",
            outcome=clarify.CLARIFY_OUTCOME,
            clarification=card,
        ),
    ]


@pytest.mark.parametrize(
    ("reply", "doc"),
    [
        ("2", "zt"),
        ("option 3", "iso"),
        ("the first one", "e8"),
        ("the last one", "iso"),
        ("e8", "e8"),
        ("zero trust please", "zt"),
        ("Essential Eight Compliance", "e8"),
    ],
)
def test_every_pointer_reply_resolves_to_its_option(reply: str, doc: str) -> None:
    resolved = resolve_clarification_reply(reply, _card_history())
    assert resolved is not None
    assert resolved.document_ids == [doc]
    assert resolved.query.startswith("How do I get compliant?")


def test_not_sure_and_long_free_text_resolve() -> None:
    unsure = resolve_clarification_reply("not sure", _card_history())
    assert unsure is not None
    assert set(unsure.document_ids) == {"e8", "zt", "iso"}
    long_reply = (
        "we are a small accounting firm that wants to win government contracts and we "
        "keep hearing about the Australian government's baseline cyber requirements"
    )
    free = resolve_clarification_reply(long_reply, _card_history())
    assert free is not None and free.free_text
    assert set(free.document_ids) == {"e8", "zt", "iso"}


def test_new_standalone_question_is_answered_as_such() -> None:
    assert (
        resolve_clarification_reply("Where is your Melbourne office?", _card_history())
        is None
    )


class _Link:
    def __init__(self, name: str) -> None:
        self.source_name, self.title = name, ""


class _Store:
    document_links = {"zt-doc": _Link("zero-trust.md Zero Trust Services")}  # noqa: RUF012


class _Chunk:
    document_id = "zt-doc"


@pytest.mark.parametrize(
    ("text", "bare"),
    [
        ("zero trust", True),
        ("tell me about Zero Trust?", True),
        ("What is CIS in Kubernetes?", False),
        ("How do I set up Zero Trust on my home router?", False),
        ("security", False),
    ],
)
def test_bare_topic_is_covered_deterministically(text: str, bare: bool) -> None:
    from app.retrieval.retriever import _is_bare_topic  # noqa: PLC0415

    assert _is_bare_topic(text, [(_Chunk(), 0.5)], _Store()) is bare


@pytest.mark.usefixtures("_thresholds")
def test_bare_topic_routes_answer_even_with_a_low_logit() -> None:
    part = PartRetrieval(
        query="ISO 27001",
        max_score=0.4,
        max_ce=-9.5,
        chunk_ids=frozenset({"c"}),
        contents=("x",),
        bare_topic=True,
    )
    decision = decide_route([part])
    assert decision.route is Route.ANSWER
    assert decision.reason == "bare_topic"


def test_casual_paraphrases_are_expanded_for_retrieval() -> None:
    assert expand_retrieval_query("who owns you guys?") == "who owns Deploy IQ?"
    law = expand_retrieval_query("what jurisdiction applies to the T&Cs?")
    assert "Terms & Conditions" in law
    assert "governing law" in law
    shoes = "What platform is best for selling shoes online?"
    assert expand_retrieval_query(shoes) == shoes


@pytest.mark.usefixtures("_thresholds")
def test_unmentioned_person_or_platform_part_is_uncovered() -> None:
    ceo = PartRetrieval(
        query="who is the CEO of Exigo Tech?",
        max_score=0.45,
        max_ce=0.27,
        chunk_ids=frozenset({"c"}),
        contents=("Exigo Tech is headquartered in Sydney.",),
    )
    gws = PartRetrieval(
        query="does it work for Google Workspace tenants?",
        max_score=0.5,
        max_ce=-0.5,
        chunk_ids=frozenset({"g"}),
        contents=("The assessment reviews your Microsoft 365 tenant.",),
    )
    decision = decide_route([_part("What is the Baseline package?", 6.0), ceo, gws])
    assert decision.route is Route.PARTIAL
    assert [v.covered for v in decision.parts] == [True, False, False]
    named = PartRetrieval(
        query="who is the CEO?",
        max_score=0.5,
        max_ce=2.0,
        chunk_ids=frozenset({"n"}),
        contents=("Our CEO leads the team.",),
    )
    assert decide_route([_part("a", 6.0), named]).route is Route.ANSWER


ISO_CATALOGUE = [
    ServiceDoc(
        "iso42",
        "ISO 42001 Technical Controls for AI in Microsoft 365",
        0.6,
        "iso-42001-ai-management-system.md",
    ),
    ServiceDoc(
        "iso",
        "ISO 27001 Technical Controls in Microsoft 365",
        0.5,
        "iso-27001-microsoft-365-technical-controls.md",
    ),
    ServiceDoc("pkg", "Microsoft 365 Security Packages", 1.4, "packages.md"),
    ServiceDoc(
        "base",
        "Microsoft 365 Security Baseline Assessment",
        -10.0,
        "microsoft-365-security-baseline-assessment.md",
    ),
    ServiceDoc(
        "cis",
        "CIS Microsoft 365 Foundations Benchmark Assessment",
        -10.0,
        "cis-microsoft-365-foundations-benchmark-assessment.md",
    ),
]


@pytest.mark.usefixtures("_thresholds")
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("iso", {"iso", "iso42"}),
        ("ISO?", {"iso", "iso42"}),
        ("tell me about iso", {"iso", "iso42"}),
        ("assessment", {"base", "cis"}),
    ],
)
def test_term_naming_several_services_gets_exactly_those(
    query: str, expected: set[str]
) -> None:
    assessments = [
        _source(
            "base",
            "microsoft-365-security-baseline-assessment.md",
            "Microsoft 365 Security Baseline Assessment - Deploy IQ",
            "base-1",
        ),
        _source(
            "cis",
            "cis-microsoft-365-foundations-benchmark-assessment.md",
            "CIS Microsoft 365 Foundations Benchmark Assessment - Deploy IQ",
            "cis-1",
        ),
    ]
    decision = _decide(query, sources=[*SERVICE_SOURCES, *assessments])
    assert decision.ask
    assert decision.reason == "ambiguous_term"
    card = service_card(query, decision, ISO_CATALOGUE)
    assert card is not None
    options = card.questions[0].options
    assert {o.document_ids[0] for o in options} == expected
    assert all(len(o.document_ids) == 1 for o in options)


_FAMILY_CATALOGUE = [
    *ISO_CATALOGUE,
    ServiceDoc("zt", "Zero Trust Services", -2.0, "zero-trust.md"),
    ServiceDoc("e8", "Essential Eight Compliance", -4.0, "essential-eight.md"),
]


@pytest.mark.usefixtures("_thresholds")
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("m365", {"iso42", "iso", "pkg", "base"}),  # 5 match: capped at 4
        ("plans", {"pkg", "zt"}),
        ("what plans do you offer?", {"pkg", "zt"}),
        ("certification", {"iso42", "iso", "e8"}),
    ],
)
def test_service_family_word_gets_that_family(query: str, expected: set[str]) -> None:
    decision = _decide(query, sources=[])
    assert decision.ask
    assert decision.reason == "ambiguous_term"
    card = service_card(query, decision, _FAMILY_CATALOGUE)
    assert card is not None
    assert {o.document_ids[0] for o in card.questions[0].options} == expected


@pytest.mark.usefixtures("_thresholds")
@pytest.mark.parametrize(
    "query",
    [
        "what about the second one?",
        "can you explain that again?",
        "which one is better?",
        "tell me more",
    ],
)
def test_bare_reference_without_history_asks_which_service(query: str) -> None:
    assert decide_route([_part(query, -9.9, cos=0.17)]).route is Route.NOT_COVERED
    decision = _decide(query, route="not_covered", max_ce=-9.9)
    assert decision.ask
    card = service_card(query, decision, _FAMILY_CATALOGUE)
    assert card is not None
    assert card.preamble == clarify.REFERENCE_PREAMBLE


@pytest.mark.parametrize(
    "query",
    ["Is it going to be sunny this weekend?", "which one of these shoes is best?"],
)
def test_off_domain_with_a_pronoun_is_not_a_bare_reference(query: str) -> None:
    assert not is_bare_reference(query)


@pytest.mark.parametrize(
    ("query", "expanded"),
    [
        ("What is your email address?", True),
        ("Wo befinden sich Ihre Büros?", True),
        ("¿Dónde están sus oficinas?", True),
        ("what is the IP address of google?", False),
    ],
)
def test_contact_asks_carry_the_contact_page_vocabulary(
    query: str, expanded: bool
) -> None:
    assert ("Contact Us" in expand_retrieval_query(query)) is expanded


def test_free_trial_searches_for_the_free_assessment() -> None:
    assert "Security Posture Assessment" in expand_retrieval_query(
        "Do you offer a free trial?"
    )


@pytest.mark.usefixtures("_thresholds")
@pytest.mark.parametrize("query", ["iso 27001", "cis", "frontier", "benchmark"])
def test_term_naming_one_service_is_answered(query: str) -> None:
    decision = _decide(query)
    card = service_card(query, decision, ISO_CATALOGUE) if decision.ask else None
    assert card is None
