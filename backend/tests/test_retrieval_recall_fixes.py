"""Page-scope recall, rare-word recall, widened rerank, typo / URL / chatty
normalisation, non-English gating and splitter fixes (r1-r4 golden misses)."""

from __future__ import annotations

import uuid

import pytest

from app.harness.clarify import ambiguous_term
from app.harness.decision import Route
from app.models.db_models import Chunk
from app.retrieval import vector_store as vs
from app.retrieval.reranker import (
    HybridSignal,
    compute_hybrid_signal,
    entity_documents,
    heading_named,
    looks_english,
    named_entities,
    named_pages,
    rare_word_stems,
    section_number_re,
)
from app.retrieval.retriever import (
    PartRetrieval,
    correct_typos,
    decide_route,
    distil_long_ask,
    expand_retrieval_query,
    is_commercial,
    part_covered,
    resolve_page_urls,
    split_query,
)
from app.retrieval.vector_store import VectorStore


def _chunk(doc, content="text", heading="", score=0.5) -> tuple[Chunk, float]:
    chunk = Chunk(
        id=uuid.uuid4(),
        document_id=doc,
        content=content,
        embedding=[0.0],
        strategy="structure_aware",
        chunk_index=0,
        chunk_metadata={"section_heading": heading},
    )
    return chunk, score


def _part(query: str, *, ce: float | None, cos: float, **kw) -> PartRetrieval:
    return PartRetrieval(
        query=query, max_score=cos, chunk_ids=frozenset(), contents=(), max_ce=ce, **kw
    )


# --- splitter ---------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "In the CIS benchmark assessment, what's checked for Exchange, SharePoint and Teams?",
        "For ISO 27001, what stays the responsibility of our ISMS owner?",
        "When I fill in the 'Request we contact you' form, what enquiry types can I pick from?",
        "Hypothetically, if my whole IT team quit tomorrow, could you run our Microsoft 365 security for us?",
        "How are you different from a one-off auditor?",
    ],
)
def test_context_clause_stays_with_its_ask(query: str) -> None:
    assert len(split_query(query)) == 1


def test_real_asks_still_split() -> None:
    parts = split_query("Where is your Melbourne office and what is your phone number?")
    assert [p.text for p in parts] == [
        "Where is your Melbourne office",
        "what is your phone number?",
    ]


def test_pleasantries_and_dictated_punctuation_are_dropped() -> None:
    assert [p.text for p in split_query("thanks, bye - oh, does it cover Intune?")] == [
        "does it cover Intune?"
    ]
    texts = [
        p.text
        for p in split_query(
            "okay so where is the sydney office located full stop and "
            "what's the phone number question mark"
        )
    ]
    assert texts == ["where is the sydney office located", "what's the phone number"]


def test_semicolon_topics_are_one_ask_each() -> None:
    parts = split_query("zero trust basics; frontier package; your phone number")
    assert [p.text for p in parts] == [
        "zero trust basics",
        "frontier package",
        "your phone number",
    ]


def test_contact_fragment_gets_contact_vocabulary() -> None:
    assert "Contact Us" in expand_retrieval_query("what's the phone number")
    assert "Contact Us" not in expand_retrieval_query("the phone number of Telstra")


# --- query normalisation ----------------------------------------------------


def test_typos_are_corrected_to_domain_terms_only() -> None:
    vocab = frozenset({"essential", "eight", "conditional", "access", "whether"})
    assert correct_typos("esential eigth", vocab) == "essential eight"
    assert correct_typos("conditonal acess", vocab) == "conditional access"
    # Real English is never rewritten towards another KB word.
    assert correct_typos("what's the weather", vocab) == "what's the weather"
    assert correct_typos("anything", frozenset()) == "anything"


def test_page_url_becomes_the_page_name() -> None:
    assert (
        resolve_page_urls("https://deployiq.co/packages what's on this page?")
        == "the packages page what's on this page?"
    )
    assert resolve_page_urls("https://staging.deployiq.co/") == "the home page"
    assert resolve_page_urls("see https://example.com/x") == "see https://example.com/x"


def test_long_courtesy_message_is_distilled() -> None:
    text = (
        "Good afternoon, I hope this message finds you well. I'm the office "
        "manager at a small clinic and our IT provider says we should look into "
        "the Essential Eight. I'd love to understand what it actually is. Many "
        "thanks for your patience."
    )
    distilled = distil_long_ask(text)
    assert "Good afternoon" not in distilled
    assert "patience" not in distilled
    assert "what Essential Eight actually is" in distilled
    assert distil_long_ask("What is Zero Trust?") == "What is Zero Trust?"


def test_language_check() -> None:
    assert not looks_english("Wie lange dauert die kostenlose Sicherheitsbewertung?")
    assert not looks_english("¿Cuáles son los paquetes de seguridad?")
    assert not looks_english("Quais são as estratégias do Essential Eight?")
    assert looks_english("What is Zero Trust?")
    assert looks_english("how much per user per month")


# --- gate -------------------------------------------------------------------


def test_commercial_ask_is_never_off_topic() -> None:
    assert is_commercial("Can I pay by credit card?")
    assert is_commercial("plans?")
    assert not is_commercial("What is the price of a burger?")
    decision = decide_route([_part("Can I pay by credit card?", ce=-10.6, cos=0.2)])
    assert decision.route is Route.NOT_COVERED
    burger = decide_route([_part("What is the price of a burger?", ce=-11.1, cos=0.06)])
    assert burger.route is Route.OFF_TOPIC


def test_non_english_part_is_gated_on_cosine() -> None:
    assert part_covered(_part("x", ce=None, cos=0.43, non_english=True))
    assert not part_covered(_part("x", ce=None, cos=0.2, non_english=True))


# --- page scope, sections, headings, rare words -----------------------------


def test_named_pages() -> None:
    assert named_pages("How is Zero Trust described on the Get Started page?") == [
        "page:get started"
    ]
    assert named_pages("What does the privacy policy say?") == ["page:privacy"]
    assert named_pages("what does the Zero Trust page say") == ["zero trust"]
    assert named_pages("how do I get started?") == []
    assert named_entities("Zero Trust on the Get Started page")[0] == "page:get started"


def test_page_keys_match_page_documents() -> None:
    names = {
        "a": "get-started.md Get Started | DeployIQ",
        "b": "our-vision.md ",
        "c": "home.md DeployIQ | AI-Powered Cybersecurity",
        "d": "zero-trust.md Zero Trust Services - Deploy IQ",
    }
    assert entity_documents(["page:get started"], names) == ["a"]
    assert entity_documents(["page:our vision"], names) == ["b"]
    assert entity_documents(["page:home"], names) == ["c"]


def test_section_number_and_heading_names() -> None:
    regex = section_number_re("What does the privacy policy say in section 2.2?")
    assert regex is not None
    assert regex.search("2 Information  2.2 Information Collected Automatically")
    assert section_number_re("What is Zero Trust?") is None
    assert heading_named("What does the Frontier package add?", "Frontier")
    assert not heading_named("What is the Security Baseline Assessment?", "Baseline")
    assert heading_named("What are the Zero Trust pillars?", "Zero Trust Pillars")
    assert not heading_named("Is my data safe?", "Data")
    assert not heading_named("Tell me the overview", "Overview")


def test_rare_word_stems() -> None:
    frequencies = {"workshops": 2, "experts": 30, "schedule": 9}
    assert rare_word_stems(
        "Can I schedule workshops with experts?", frequencies, 4
    ) == ["workshop"]


def test_scoped_page_leads_the_selection() -> None:
    page, other = uuid.uuid4(), uuid.uuid4()
    candidates = [
        _chunk(other, score=0.9),
        _chunk(other, score=0.8),
        _chunk(page, score=0.5),
    ]
    signal = HybridSignal(entity_doc_ids=(str(page),), scoped_doc_ids=(str(page),))
    selected = VectorStore._select_diverse_chunks(candidates, top_k=3, signal=signal)  # pylint: disable=protected-access
    assert selected[0][0].document_id == page


def test_hybrid_signal_marks_heading_hits() -> None:
    doc = uuid.uuid4()
    frontier = _chunk(doc, "Frontier adds 24/7 MDR", heading="Frontier")
    other = _chunk(doc, "Packages and Essential Eight", heading="Essential Eight")
    signal = compute_hybrid_signal(
        "What does the Frontier package add?", [frontier, other], {doc: "packages.md"}
    )
    assert signal.heading_hits == frozenset({frontier[0].id})


def test_unsure_pool_reranks_the_reserve(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = uuid.uuid4()
    pool = [_chunk(doc, f"pool {i}", score=0.9 - i / 100) for i in range(2)]
    reserve = [_chunk(doc, "the answer", score=0.5)]
    scores = {"pool 0": -2.0, "pool 1": -3.0, "the answer": 5.0}

    def fake_rerank(_query, candidates, _boosts=None, logits_out=None):
        for chunk, _score in candidates:
            logits_out[chunk.id] = scores[chunk.content]
        return sorted(candidates, key=lambda c: -scores[c[0].content])

    monkeypatch.setattr(vs, "rerank", fake_rerank)
    job = vs._RankJob("q", pool + reserve, {}, False, 2, 3, None)  # pylint: disable=protected-access
    logits: dict = {}
    ranked = vs._widened_rerank(job, pool + reserve, HybridSignal(), logits)  # pylint: disable=protected-access
    assert ranked[0][0].content == "the answer"
    assert len(logits) == 3

    scores["pool 0"] = 6.0  # confident pool: the reserve is never scored
    logits = {}
    ranked = vs._widened_rerank(job, pool + reserve, HybridSignal(), logits)  # pylint: disable=protected-access
    assert len(logits) == 2


# --- clarify ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "term"),
    [
        ("iso stuff", "iso"),
        ("what's your iso offering", "iso"),
        ("m365 help", "m365"),
        ("assessments pls", "assessments"),
    ],
)
def test_filler_never_hides_the_ambiguous_term(query: str, term: str) -> None:
    assert ambiguous_term(query) == term


# --- borderline band, follow-ups, commercial (final-run gate misses) --------


@pytest.mark.parametrize(
    "query",
    [
        "What is a zero-day exploit?",
        "How far is Sydney from Canberra?",
        "Which laptop should I buy?",
        "can a robot change my car settings?",
    ],
)
def test_off_domain_asks_are_not_rewritten(query: str) -> None:
    assert expand_retrieval_query(query) == query


def test_named_service_email_follow_up_is_not_a_contact_ask() -> None:
    assert "Contact Us" not in expand_retrieval_query(
        "what about email (Security Baseline Assessment)"
    )
    assert "Contact Us" in expand_retrieval_query("email?")


def test_pronoun_part_resolves_to_our_name() -> None:
    parts = split_query("Is DeployIQ an AI-native venture, and who funds it?")
    assert [p.search for p in parts] == [
        "Is DeployIQ an AI-native venture",
        "who funds DeployIQ?",
    ]


@pytest.mark.parametrize(
    ("query", "commercial"),
    [
        ("Which laptop should I buy under $1500?", False),
        ("Where can I buy a cheap iPhone?", False),
        ("which package should I buy?", True),
        ("can I buy a plan?", True),
        ("how do I buy zero trust?", True),
    ],
)
def test_third_party_purchase_is_not_commercial(query: str, commercial: bool) -> None:
    assert is_commercial(query) is commercial


@pytest.mark.parametrize(
    ("ce", "cos", "covered"),
    [
        (-8.96, 0.331, True),  # "where would my data physically be stored?"
        (-9.21, 0.397, True),  # "is data encrypted end to end?"
        (-9.57, 0.436, False),  # "do you offer penetration testing?"
        (-11.1, 0.319, False),  # "can a robot change my car settings?"
        (-8.88, 0.315, False),  # "what time is it in Sydney?"
    ],
)
def test_borderline_band(ce: float, cos: float, covered: bool) -> None:
    assert part_covered(_part("q", ce=ce, cos=cos)) is covered
