"""Quality tests for post-generation checks: refusals, claims, relevance, brackets."""

import pytest

from app.guardrails import post_checks
from app.guardrails.post_checks import (
    _NEGATIVE_ANSWER_RE,
    _unsupported_figures,
    check_groundedness,
    is_off_topic_by_overlap,
    is_refusal,
    query_has_overlap,
    split_claims,
    supported_claim_ratio,
    unsupported_claims,
)
from app.harness.citations import apply_citations, strip_free_brackets
from app.schemas.query import SourceChunk


def _source(content: str, doc: str = "00000000-0000-0000-0000-000000000002"):
    return SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id=doc,
        content=content,
        score=0.8,
        metadata={},
    )


# --- refusal regex -----------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "The documents do not specify exact pricing.",
        "We don't have details on that here.",
        "DeployIQ doesn't include that feature.",
        "I cannot provide that information.",
        "There is no information about that.",
        "That is not available in the context.",
        "Certifications are not yet claimed.",
    ],
)
def test_negative_regex_matches_real_negation(answer):
    assert _NEGATIVE_ANSWER_RE.search(answer) is not None


@pytest.mark.parametrize(
    "answer",
    [
        "DeployIQ does include Essential Eight reporting.",
        "DeployIQ does provide continuous monitoring.",
        "The platform does cover Zero Trust identity controls.",
        "Reports are available in the dashboard.",
    ],
)
def test_negative_regex_ignores_positive_does_phrases(answer):
    assert _NEGATIVE_ANSWER_RE.search(answer) is None
    assert is_refusal(answer) is False


@pytest.mark.parametrize(
    "answer",
    [
        "We don't have details on that here. Talk to one of our specialists.",
        "That is outside what I can help with.",
        "I want to give you an accurate answer and I'm not certain.",
        "Please talk to one of our specialists.",
    ],
)
def test_friendly_refusals_are_detected(answer):
    assert is_refusal(answer) is True


def test_long_answer_with_late_negative_is_not_a_refusal():
    answer = (
        "DeployIQ delivers Essential Eight controls across identity and devices. "
        "It reports continuously on posture. "
        "It supports governance workflows for administrators. "
        "Pricing is not available publicly. " + "More detail here. " * 20
    )
    assert is_refusal(answer) is False


def test_refusal_with_dollar_figure_is_not_a_refusal():
    assert is_refusal("We don't have details, but it costs $50 a month.") is False


@pytest.mark.parametrize(
    "answer",
    [
        "We don't publish pricing, but we guarantee 99.9% availability.",
        "The Starter plan doesn't include SSO. The Pro plan includes SSO, MFA and "
        "99.9% uptime guarantees.",
        "The Starter plan doesn't include SSO. [1] The Pro plan does include SSO "
        "and MFA enforcement. [2]",
        "DeployIQ maps your controls to Essential Eight and tracks maturity. "
        "Talk to one of our specialists to get started.",
        "We don't have details on that. Our SLA is strong.",
    ],
)
def test_answers_with_facts_figures_or_claims_are_not_refusals(answer):
    assert is_refusal(answer) is False


@pytest.mark.parametrize(
    "answer",
    [
        "We don't publish pricing, but we guarantee 99.9% availability.",
        "The Starter plan doesn't include SSO. The Pro plan includes SSO, MFA and "
        "99.9% uptime guarantees.",
    ],
)
async def test_non_refusal_with_figures_is_flagged_before_refusal_shortcut(answer):
    grounded, confidence, flag = await check_groundedness(
        answer, [_source("DeployIQ supports SSO on every plan.")], 0.45
    )
    assert (grounded, confidence) == (False, 0.0)
    assert flag in {"unsupported_numeric_claim", "unsupported_claim"}


async def test_negative_does_include_runs_normal_checks(monkeypatch):
    async def _fake_embed(texts):
        return [[0.0, 1.0] for _ in texts]

    monkeypatch.setattr(post_checks, "embed_texts", _fake_embed)
    sources = [
        SourceChunk(
            chunk_id="00000000-0000-0000-0000-000000000001",
            document_id="00000000-0000-0000-0000-000000000002",
            content="Unrelated",
            score=0.8,
            metadata={},
            embedding=[1.0, 0.0],
        )
    ]
    grounded, _c, flag = await check_groundedness(
        "DeployIQ does include a flux capacitor.", sources, 0.45
    )
    assert grounded is False
    assert flag == "possible_hallucination"


# --- assurance / commercial claims ------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "DeployIQ is safe to purchase.",
        "We guarantee delivery.",
        "DeployIQ is certified to ISO 27001.",
        "It is risk-free.",
        "You get 24/7 support.",
        "Expect a strong ROI.",
        "Onboarding is done within 48 hours.",
        "Deploy in minutes not weeks.",
        "Our SLA covers this.",
        "We offer 20% off.",
        "The Starter plan is free.",
        "It has a 99.9% uptime.",
    ],
)
def test_unsupported_assurance_claims_flagged(answer):
    assert unsupported_claims(answer, "DeployIQ delivers Essential Eight reporting.")


def test_supported_claims_allowed():
    assert (
        unsupported_claims(
            "The Starter plan is free and onboarding is within 48 hours.",
            "Starter (Free) plan. Onboarding is completed within 48 hours.",
        )
        == []
    )


def test_free_of_phrase_is_not_a_claim():
    assert unsupported_claims("The report is free of jargon.", "Plain text.") == []


@pytest.mark.parametrize(
    ("answer", "source"),
    [
        ("DeployIQ is compliant with the framework.", "Compliance reporting built in."),
        ("Feel free to ask our team anything.", "Plain text."),
        ("You can safely upgrade.", "Upgrades can be rolled back at any time."),
        ("You can safely upgrade.", "Upgrades are safe."),
        ("Onboarding is done within 48 hours.", "Onboarding completes in 48 hours."),
        ("The Starter plan is free.", "Starter (Free) plan includes reporting."),
        ("The report is free of jargon.", "Plain text."),
    ],
)
def test_claim_guard_accepts_equivalent_support(answer, source):
    assert unsupported_claims(answer, source) == []


@pytest.mark.parametrize(
    ("answer", "source"),
    [
        ("DeployIQ is certified.", "DeployIQ is not yet certified."),
        ("DeployIQ is certified.", "We have no certification today."),
        (
            "The Pro plan is free.",
            "Free-form notes are supported. The Pro plan is paid.",
        ),
        ("The Pro plan is free.", "Our agent-free scanner runs on Pro."),
        ("The Pro plan is free.", "The Starter plan is free."),
        ("The Pro plan is free.", "The Pro plan is not free."),
        ("You can safely upgrade.", "Plain text."),
    ],
)
def test_claim_guard_fail_open_cases_are_flagged(answer, source):
    assert unsupported_claims(answer, source) != []


def test_claim_guard_is_linear_on_pathological_input():
    nasty = "free " * 800 + "not " * 800
    assert isinstance(unsupported_claims(nasty, nasty), list)


def test_currency_words_checked_like_dollar_figures():
    assert _unsupported_figures("It costs 500 AUD per month.", "No pricing.") != []
    assert _unsupported_figures("It is 20 per user.", "No pricing.") != []
    assert _unsupported_figures("It costs 500 AUD.", "Pricing: 500 AUD.") == []


async def test_unsupported_claim_makes_answer_ungrounded(monkeypatch):
    async def _boom(*_a, **_k):
        raise AssertionError("must short-circuit before embedding")

    monkeypatch.setattr(post_checks, "embed_texts", _boom)
    grounded, confidence, flag = await check_groundedness(
        "DeployIQ is safe to purchase.", [_source("DeployIQ Essential Eight.")], 0.45
    )
    assert (grounded, confidence, flag) == (False, 0.0, "unsupported_claim")


async def test_supported_claim_reaches_semantic_check(monkeypatch):
    async def _fake_embed(texts):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(post_checks, "embed_texts", _fake_embed)
    grounded, _c, flag = await check_groundedness(
        "DeployIQ is safe to use.", [_source("It is safe to use.")], 0.45
    )
    assert grounded is True
    assert flag is None


# --- claim-level support ----------------------------------------------


def test_split_claims_sentences_bullets_and_cap():
    claims = split_claims(
        "First claim is here. - Second bullet point here\n1. Third one is ok"
    )
    assert claims == [
        "First claim is here.",
        "Second bullet point here",
        "Third one is ok",
    ]
    assert len(split_claims("Some sentence goes here. " * 30)) == 12


def test_supported_claim_ratio_uses_best_chunk_per_claim():
    sources = [[1.0, 0.0], [0.0, 1.0]]
    claims = [[1.0, 0.0], [0.0, 1.0], [-0.7, -0.7]]
    assert supported_claim_ratio(claims, sources, 0.5) == pytest.approx(2 / 3)
    assert supported_claim_ratio([], sources, 0.5) == 0.0


async def test_claim_ratio_below_minimum_is_ungrounded(monkeypatch):
    vectors = {"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [0.0, 1.0]}

    async def _fake_embed(texts):
        # answer first, then claims; whole answer matches the chunk.
        return [
            [1.0, 0.0]
            if t.startswith("Alpha") or t.startswith("Alpha one")
            else vectors["b"]
            for t in texts
        ]

    monkeypatch.setattr(post_checks, "embed_texts", _fake_embed)
    sources = [
        SourceChunk(
            chunk_id="00000000-0000-0000-0000-000000000001",
            document_id="00000000-0000-0000-0000-000000000002",
            content="Alpha",
            score=0.8,
            metadata={},
            embedding=[1.0, 0.0],
        )
    ]
    answer = "Alpha one is here. Beta two is there. Gamma three is elsewhere."
    grounded, _c, flag = await check_groundedness(answer, sources, 0.45, 0.6)
    assert grounded is False
    assert flag == "possible_hallucination"

    grounded, _c, flag = await check_groundedness(answer, sources, 0.45, 0.3)
    assert grounded is True


async def test_claims_embedded_in_one_batched_call(monkeypatch):
    calls: list[list[str]] = []

    async def _fake_embed(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(post_checks, "embed_texts", _fake_embed)
    source = SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        content="x",
        score=0.8,
        metadata={},
        embedding=[1.0, 0.0],
    )
    grounded, _c, _f = await check_groundedness(
        "First claim is here. Second claim is here.", [source], 0.45
    )
    assert grounded is True
    assert len(calls) == 1
    assert len(calls[0]) == 3


# --- query relevance ---------------------------------------------------


def test_query_has_overlap_true_for_shared_word_or_stem():
    chunks = ["DeployIQ monitors identity governance for administrators."]
    assert query_has_overlap("How does governance work?", chunks) is True
    assert query_has_overlap("administrator access", chunks) is True


def test_query_has_overlap_false_for_nonsense():
    chunks = ["DeployIQ monitors identity governance for administrators."]
    assert query_has_overlap("Topic: blorptivex zumquat", chunks) is False
    assert query_has_overlap("what is a cat", chunks) is True  # no content words


def test_off_topic_by_overlap_needs_weak_score_and_no_overlap():
    chunks = ["DeployIQ monitors identity governance."]
    assert is_off_topic_by_overlap("blorptivex zumquat", chunks, 0.2, 0.3) is True
    assert is_off_topic_by_overlap("blorptivex zumquat", chunks, 0.5, 0.3) is False
    assert is_off_topic_by_overlap("identity governance", chunks, 0.2, 0.3) is False


def test_price_words_share_a_stem():
    chunks = ["Our pricing plans start with a free tier."]
    assert query_has_overlap("How much does it cost?", chunks) is True
    assert query_has_overlap("What is the price?", chunks) is True
    assert query_has_overlap("Any fees involved?", chunks) is True
    assert is_off_topic_by_overlap("what does it cost", chunks, 0.2, 0.3) is False


def test_follow_up_skips_the_overlap_rule():
    chunks = ["DeployIQ monitors identity governance."]
    assert is_off_topic_by_overlap("blorptivex zumquat", chunks, 0.2, 0.3) is True
    assert (
        is_off_topic_by_overlap("blorptivex zumquat", chunks, 0.2, 0.3, follow_up=True)
        is False
    )


def test_offline_style_question_overlapping_product_name_stays_on_topic():
    chunks = ["DeployIQ monitors identity governance."]
    assert (
        is_off_topic_by_overlap("Does DeployIQ work offline?", chunks, 0.2, 0.3)
        is False
    )
    assert (
        is_off_topic_by_overlap("blorptivex offline zumquat", chunks, 0.2, 0.3) is True
    )
    assert (
        is_off_topic_by_overlap("blorptivex offline zumquat", chunks, 0.4, 0.3) is False
    )


# --- citations: free-text brackets ------------------------------------


def test_strip_free_brackets_keeps_markers_links_and_code():
    text = (
        "See [Deploy IQ] docs [1] and [Source: cis.pdf] or [2, 3]. "
        "A [link](https://x.y) stays.\n```\nx = a[b]\n```"
    )
    out = strip_free_brackets(text)
    assert "[Deploy IQ]" not in out
    assert "Deploy IQ docs [1]" in out
    assert "cis.pdf" not in out
    assert "[2, 3]" not in out
    assert "[link](https://x.y)" in out
    assert "x = a[b]" in out


def test_apply_citations_strips_free_brackets_and_drops_out_of_range():
    sources = [_source("a", doc="00000000-0000-0000-0000-0000000000a1")]
    answer, citations = apply_citations("It works [note] well [1][9].", sources)
    assert answer == "It works note well [1]."
    assert [c.n for c in citations] == [1]


# --- regression: correct, supported answers must not be rejected -------

_WHO_FOR_DOC = (
    "Section: Who DeployIQ Platform Is For\n\n"
    "DeployIQ is designed for organisations that need structured, predictable, "
    "and accountable security uplift, including:\n\n"
    "- Small and mid-sized businesses strengthening foundational security.\n\n"
    "- Startups building secure, scalable environments.\n\n"
    "- Organisations moving from reactive to governed security practices."
)
_E8_DOC = (
    "A structured, phased implementation of the ACSC Essential Eight delivered "
    "in days, not months, using AI-enabled capability with explicit human "
    "oversight. The full process—from assessment to audit-ready deployment"
    "—is achieved in days, not months."
)


@pytest.mark.parametrize(
    ("answer", "doc"),
    [
        (
            "We deliver the Essential Eight in days, not months. [1]",
            _E8_DOC,
        ),
        (
            "The full process is achieved in days not months.",
            _E8_DOC,
        ),
        (
            "DeployIQ is designed for small and mid-sized businesses and startups.",
            _WHO_FOR_DOC,
        ),
    ],
)
def test_supported_phrasing_passes_claim_guard(answer, doc):
    assert unsupported_claims(answer, doc) == []
    assert _unsupported_figures(answer, doc) == []


def test_unsupported_assurances_still_flagged():
    doc = _WHO_FOR_DOC
    assert unsupported_claims("DeployIQ is safe to purchase.", doc) != []
    assert unsupported_claims("Results are guaranteed.", doc) != []
    assert unsupported_claims("DeployIQ is certified.", doc) != []
    assert unsupported_claims("Done in days, not months.", doc) != []


def test_split_claims_skips_headings_labels_citations_and_declines():
    claims = split_claims(
        "We implement the Essential Eight in days. [1]\n\n"
        "## Key points\n"
        "- **Speed:**\n"
        "- **Scope:** Startups building secure environments. [2]\n\n"
        "We don't have details on exact timelines here; talk to one of our "
        "specialists."
    )
    assert claims == [
        "We implement the Essential Eight in days.",
        "Scope: Startups building secure environments.",
    ]


def test_quoted_bullets_count_as_supported_without_embedding_match():
    claims = split_claims(
        "- Startups building secure, scalable environments.\n"
        "- Small and mid-sized businesses strengthening foundational security."
    )
    orthogonal = [[0.0, 1.0] for _ in claims]
    source = [[1.0, 0.0]]
    assert supported_claim_ratio(orthogonal, source, 0.4) == 0.0
    assert supported_claim_ratio(orthogonal, source, 0.4, claims, _WHO_FOR_DOC) == 1.0


@pytest.mark.parametrize(
    "answer",
    [
        # (a) who is DeployIQ for: bullets + handoff line + citation markers
        "DeployIQ is designed for organisations that need structured, predictable "
        "security uplift.\n\n## Who it is for\n"
        "- **Small and mid-sized businesses:** strengthening foundational security. [1]\n"
        "- **Startups:** building secure, scalable environments. [1]\n\n"
        "We don't have details on specific industries here; talk to one of our "
        "specialists.",
        # (b) speed follow-up
        "We implement the ACSC Essential Eight in days, not months, with human "
        "oversight. [1]\n\n## Speed and delivery\n"
        "- The full process from assessment to audit-ready deployment takes days, "
        "not months. [2]\n\n"
        "We don't have details on exact timelines per organisation here; talk to "
        "one of our specialists.",
        "DeployIQ is designed for startups building secure, scalable environments.",
        "Small and mid-sized businesses strengthening foundational security.",
        "Organisations moving from reactive to governed security practices.",
        "- Startups building secure, scalable environments.\n"
        "- Organisations moving from reactive to governed security practices.",
        "DeployIQ delivers a phased Essential Eight implementation in days, not "
        "months.",
        "Essential Eight is delivered in days, not months, using AI-enabled "
        "capability with explicit human oversight.",
    ],
)
async def test_typical_supported_answers_are_grounded(monkeypatch, answer):
    # Decline/handoff lines and headings embed far from the chunk; every real
    # claim embeds close to it.
    async def _fake_embed(texts):
        return [
            [0.0, 1.0]
            if t.startswith(("We don't", "Key", "Who it", "Speed"))
            else [1.0, 0.0]
            for t in texts
        ]

    monkeypatch.setattr(post_checks, "embed_texts", _fake_embed)
    source = SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        content=_WHO_FOR_DOC + "\n\n" + _E8_DOC,
        score=0.8,
        metadata={},
        embedding=[1.0, 0.0],
    )
    grounded, _c, flag = await check_groundedness(answer, [source], 0.45)
    assert (grounded, flag) == (True, None)


# --- decline sentences vs citation markers ---------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "We don't have details on that. [2]",
        "Pricing isn't published here [1][4] - contact one of our specialists.",
        "Formal certifications are not yet claimed.",
        "I can't see that detail in what I have. [3]",
        "A specialist can confirm the exact plan.",
    ],
)
def test_is_decline_sentence_true(text):
    assert post_checks.is_decline_sentence(text)


@pytest.mark.parametrize(
    "text",
    [
        "DeployIQ covers the Essential Eight. [1]",
        "The Starter plan is free. [2]",
        "We assess your tenant against CIS controls.",
    ],
)
def test_is_decline_sentence_false(text):
    assert not post_checks.is_decline_sentence(text)


def test_marker_alone_is_not_an_assertion_in_a_decline():
    assert is_refusal("We don't have details on that. [1] Talk to a specialist. [2]")
    assert not is_refusal("We don't have details on 99% uptime. Talk to a specialist.")


def test_partial_decline_is_not_a_refusal_and_keeps_answer_claims():
    answer = (
        "DeployIQ assesses Essential Eight maturity and prioritises controls. [1]\n"
        "We don't have details on pricing; talk to a specialist. [2]"
    )
    assert not is_refusal(answer)
    claims = split_claims(answer)
    assert len(claims) == 1 and "Essential Eight" in claims[0]
