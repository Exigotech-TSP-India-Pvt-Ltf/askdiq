"""Quote-backed claim verification (app.harness.claims) and the label matrix.

The verifier is deterministic, so it is unit-tested here. These tests mock
the LLM reply: they pin the code contract only; behaviour evidence comes from
real /query calls (scripts/live_golden_eval.py).
"""

from __future__ import annotations

# pylint: disable=too-many-lines  # one suite for the verifier's contract
import json
from functools import cache
from pathlib import Path

import pytest

from app.chunking.section_tree import SectionTreeChunker
from app.guardrails.intent import IntentMatch
from app.harness import orchestrator
from app.harness import prompt_catalog as catalog
from app.harness.claims import (
    find_sales_contact,
    ground_answer,
    parse_draft,
    uncovered_parts,
)
from app.harness.decision import Intent, IntentResult, PartVerdict, Route, RouteDecision
from app.ingestion.cleanup import clean_markdown
from app.ingestion.site_pages import parse_site_page
from app.retrieval.retriever import PartRetrieval, RetrievalResult
from app.schemas.query import SourceChunk

SBA = (
    "Microsoft 365 Security Baseline Assessment 203a Hero\n"
    "- 42 controls\n- 5 focus areas\n- Your approval, every step\n"
    "We evaluate your tenant across five areas that matter most: identity, "
    "email, endpoints, endpoint protection and backup. Nothing changes in "
    "your environment without your sign-off. Typically 1 to 2 weeks."
)
CONTACT = "Contact us 203a Sales\nCall 1300 394 468 or email info@deployiq.co."


def _source(content: str, n: int = 1, name: str = "sba.md") -> SourceChunk:
    return SourceChunk(
        chunk_id=f"00000000-0000-0000-0000-00000000000{n}",
        document_id=f"00000000-0000-0000-0000-00000000010{n}",
        content=content,
        score=0.8,
        metadata={"source_name": name},
    )


def _reply(claims: list[dict], parts: list[dict] | None = None) -> str:
    return json.dumps(
        {"parts": parts or [{"ask": "the controls", "covered": True}], "claims": claims}
    )


def _claim(
    text: str, quote: str, source: int = 1, part: int = 0, kind: str = "sentence"
) -> dict:
    return {"text": text, "quote": quote, "source": source, "part": part, "kind": kind}


def _ground(claims: list[dict], parts: list[dict] | None = None, query: str = "q?"):
    draft = parse_draft(_reply(claims, parts))
    assert draft is not None
    return ground_answer(draft, [_source(SBA)], query)


@pytest.mark.parametrize(
    ("text", "quote", "reason"),
    [
        ("It covers 58 controls.", "42 controls", "figure_not_in_source"),
        (
            "It covers 42 controls across nine areas.",
            "42 controls",
            "figure_not_in_source",
        ),
        ("It takes 2 days.", "Typically 1 to 2 weeks", "duration_not_in_source"),
        ("It covers 42 controls.", "forty-two controls", "quote_not_in_source"),
        ("We evaluate every area of your tenant.", "identity", "quote_too_short"),
        (
            "Your environment changes without your sign-off.",
            "Nothing changes in your environment without your sign-off",
            "negation_mismatch",
        ),
        (
            "Telstra uses the assessment across identity and email.",
            "identity, email, endpoints",
            "name_not_in_source",
        ),
        (
            "Our materials cover identity, email and endpoints.",
            "identity, email, endpoints",
            "banned_wording",
        ),
        (
            "The assessment is guaranteed across identity and email.",
            "identity, email, endpoints",
            "assurance_not_in_quote",
        ),
    ],
)
def test_unsupported_claim_is_dropped(text: str, quote: str, reason: str) -> None:
    result = _ground([_claim(text, quote)])
    assert not result.answered
    assert result.text == catalog.NO_ANSWER_LINE
    assert [r for _c, r in result.dropped] == [reason]


def test_page_wording_is_not_banned_wording() -> None:
    # terms-downloads: "materials" is the page's own term there, not stock wording.
    page = "These materials are not Services or Deliverables."
    draft = parse_draft(_reply([_claim(page, page)]))
    assert draft is not None
    result = ground_answer(draft, [_source(page)], "what are downloads?")
    assert result.answered
    assert "materials are not Services" in result.text


@pytest.mark.parametrize(
    ("text", "answered"),
    [
        ("No, they aren't tailored to any environment.", True),
        ("They aren't provided as is.", False),
    ],
)
def test_negation_is_checked_against_the_restated_clause(
    text: str, answered: bool
) -> None:
    # terms-downloads: the quote negates two clauses; the claim restates one.
    quote = "These are not Services, are not tailored to any environment, and are provided as is"
    draft = parse_draft(_reply([_claim(text, quote)]))
    assert draft is not None
    result = ground_answer(draft, [_source(quote + ".")], "are they tailored?")
    assert result.answered is answered


def test_negation_from_another_sentence_does_not_back_a_claim() -> None:
    # "Not" belongs to the first sentence; the claim reverses the second.
    quote = "Not on its own. We deliver the Essential Eight uplift."
    draft = parse_draft(
        _reply([_claim("We do not deliver the Essential Eight uplift.", quote)])
    )
    assert draft is not None
    result = ground_answer(draft, [_source(quote)], "do you deliver it?")
    assert not result.answered


def test_supported_claims_render_with_renumbered_markers() -> None:
    result = _ground(
        [
            _claim("The assessment covers 42 controls.", "42 controls"),
            _claim(
                "Nothing changes in your environment without your sign-off.",
                "Nothing changes in your environment without your sign-off",
            ),
        ]
    )
    assert result.answered and result.verified_ratio == 1.0
    assert result.text == (
        "The assessment covers 42 controls. Nothing changes in your "
        "environment without your sign-off. [1]"
    )
    assert [c.n for c in result.citations] == [1]


def test_quote_matches_across_case_punctuation_and_ellipsis() -> None:
    result = _ground(
        [
            _claim(
                "We evaluate identity and backup.", "we evaluate your TENANT ... backup"
            )
        ]
    )
    assert result.answered


def test_uncovered_part_gets_the_fixed_partial_line() -> None:
    result = _ground(
        [_claim("The assessment covers 42 controls.", "42 controls")],
        parts=[
            {"ask": "the controls", "covered": True},
            {"ask": "the price", "covered": False},
        ],
        query="How many controls and what is the price?",
    )
    # A price part gets the fixed pricing line, never "I don't have details".
    assert result.text.endswith(catalog.PRICING_LINE)
    assert catalog.partial_line(["the price"]) not in result.text
    assert result.uncovered == ["the price"]


def test_partial_line_never_repeats_an_invented_part_name() -> None:
    result = _ground(
        [_claim("The assessment covers 42 controls.", "42 controls")],
        parts=[
            {"ask": "the controls", "covered": True},
            {"ask": "our guaranteed 24/7 support", "covered": False},
        ],
        query="How many controls?",
    )
    assert result.text.endswith(catalog.partial_line([]))


def test_gate_gap_is_named_in_the_visitors_words() -> None:
    query = "How many controls does it check and what are its opening hours?"
    draft = parse_draft(
        _reply(
            [_claim("The assessment covers 42 controls.", "42 controls")],
            [{"ask": query, "covered": True}],
        )
    )
    assert draft is not None
    result = ground_answer(
        draft,
        [_source(SBA)],
        query,
        gate_uncovered=True,
        gap_parts=["and what are its opening hours?"],
    )
    assert result.text.endswith(catalog.partial_line(["its opening hours"]))
    assert catalog.PARTIAL_FALLBACK_PARTS not in result.text


def test_claim_cut_mid_phrase_is_dropped() -> None:
    result = _ground(
        [
            _claim("The assessment covers 42 controls.", "42 controls"),
            _claim("It covers 5 focus areas and.", "5 focus areas"),
        ]
    )
    assert result.answered
    assert "truncated" in {r for _c, r in result.dropped}


def test_claims_for_an_uncovered_part_and_declines_are_dropped() -> None:
    result = _ground(
        [
            _claim("The assessment covers 42 controls.", "42 controls", part=1),
            _claim("Pricing isn't published, talk to a specialist.", "42 controls"),
        ],
        parts=[
            {"ask": "the weather", "covered": False},
            {"ask": "x", "covered": False},
        ],
    )
    assert not result.answered
    assert {r for _c, r in result.dropped} <= {"uncovered_part", "decline"}


def test_yes_no_lead_in_is_cut_and_the_rest_verified() -> None:
    result = _ground(
        [
            _claim(
                "No, nothing changes in your environment without your sign-off.",
                "Nothing changes in your environment without your sign-off",
            )
        ]
    )
    assert result.text.startswith("Nothing changes")


def test_plain_text_reply_ships_only_verbatim_sentences() -> None:
    draft = parse_draft("42 controls [1]. We also hold ISO 27001 certification [1].")
    assert draft is not None
    result = ground_answer(draft, [_source(SBA)], "q?")
    assert "ISO" not in result.text
    assert result.mode == "plain_verbatim"


def test_conflicting_values_cannot_be_ranged() -> None:
    other = _source("Packages 203a Baseline\n- 39 controls across 5 focus areas", 2)
    draft = parse_draft(_reply([_claim("It covers 39 to 42 controls.", "42 controls")]))
    assert draft is not None
    result = ground_answer(draft, [_source(SBA), other], "q?")
    assert not result.answered


def test_sales_contact_is_read_from_the_contact_chunk() -> None:
    found = find_sales_contact([_source(SBA), _source(CONTACT, 2, "contact-us.md")])
    assert found is not None
    assert (found.source, found.phone, found.email) == (
        2,
        "1300 394 468",
        "info@deployiq.co",
    )
    assert find_sales_contact([_source(SBA)]) is None


# ---------------------------------------------------------------------------
# Label matrix (run_query): every path's outcome / grounded / confidence.
# ---------------------------------------------------------------------------


def _patch_pipeline(
    monkeypatch, reply: str | Exception, route: Route = Route.ANSWER, sources=None
):
    sources = sources or [_source(SBA)]
    monkeypatch.setattr(orchestrator, "run_pre_checks", lambda *_a, **_k: [])

    async def fake_intent(_q):
        return IntentMatch(IntentResult(Intent.QUESTION, 0.9))

    async def fake_retrieve(*_a, **_k):
        part = PartRetrieval(
            query="q",
            max_score=0.8,
            chunk_ids=frozenset(s.chunk_id for s in sources),
            contents=tuple(s.content for s in sources),
            sources=tuple(sources),
        )
        return RetrievalResult(sources=list(sources), parts=[part])

    def fake_route(_parts, *, buying=False):
        covered = route in (Route.ANSWER, Route.PARTIAL)
        return RouteDecision(route, parts=(PartVerdict("q", covered),), buying=buying)

    async def fake_llm(*_a, **_k):
        if isinstance(reply, Exception):
            raise reply
        return reply, {"prompt_tokens": 10, "completion_tokens": 5}

    async def no_contact_page(*_a, **_k):
        # A buying ask fetches the KB contact page by route; this KB has none.
        return []

    monkeypatch.setattr(orchestrator, "classify_intent", fake_intent)
    monkeypatch.setattr(orchestrator, "route_sources", no_contact_page)
    monkeypatch.setattr(orchestrator, "retrieve_parts", fake_retrieve)
    monkeypatch.setattr(orchestrator, "decide_route", fake_route)
    monkeypatch.setattr(orchestrator, "clarify_allowed", lambda *_a, **_k: False)
    monkeypatch.setattr(orchestrator, "call_llm", fake_llm)


async def _run(query: str = "How many controls does the baseline cover?"):
    return await orchestrator.run_query(
        query=query,
        top_k=5,
        filters=None,
        vector_store=None,
        request_id="matrix",
        stage_timings={},
    )


async def test_matrix_answered(monkeypatch) -> None:
    _patch_pipeline(
        monkeypatch, _reply([_claim("It covers 42 controls.", "42 controls")])
    )
    r = await _run()
    assert (r.outcome, r.grounded, r.confidence) == ("answered", True, 1.0)
    assert len(r.citations) == len(r.sources) == 1
    assert r.guardrail_flags == []


async def test_sources_carry_every_chunk_behind_a_kept_claim(monkeypatch) -> None:
    head, tail = SBA.split(" Nothing changes", 1)
    first, second = _source(head, 1), _source("Nothing changes" + tail, 2)
    second = second.model_copy(update={"document_id": first.document_id})
    unused = _source(CONTACT, 3, name="contact.md")
    reply = _reply(
        [
            _claim("It covers 42 controls.", "42 controls", source=1),
            _claim(
                "Nothing changes in your environment without your sign-off.",
                "Nothing changes in your environment without your sign-off",
                source=2,
            ),
        ]
    )
    _patch_pipeline(monkeypatch, reply, sources=[first, second, unused])
    r = await _run()
    assert r.outcome == "answered"
    assert [c.n for c in r.citations] == [1]
    # sources[0] stays citation 1's chunk; the document's other backing chunk
    # follows (once); the unused chunk is not returned.
    assert [s.chunk_id for s in r.sources] == [first.chunk_id, second.chunk_id]


async def test_matrix_partial(monkeypatch) -> None:
    reply = _reply(
        [_claim("It covers 42 controls.", "42 controls")],
        parts=[
            {"ask": "controls", "covered": True},
            {"ask": "the price", "covered": False},
        ],
    )
    _patch_pipeline(monkeypatch, reply)
    r = await _run("How many controls and what is the price?")
    assert (r.outcome, r.grounded) == ("answered", True)
    assert r.guardrail_flags == ["partial_answer"]
    assert r.answer.endswith(catalog.PRICING_LINE)


@pytest.mark.parametrize(
    "reply",
    [
        _reply([], parts=[{"ask": "x", "covered": False}]),
        _reply([_claim("It covers 58 controls.", "42 controls")]),
    ],
    ids=["not_covered", "verification_failed"],
)
async def test_matrix_ungrounded_ships_only_the_fixed_line(
    monkeypatch, reply: str
) -> None:
    _patch_pipeline(monkeypatch, reply)
    r = await _run()
    assert (r.outcome, r.grounded, r.confidence) == ("ungrounded", False, 0.0)
    assert r.answer == catalog.NO_ANSWER_LINE
    assert r.sources == [] and r.citations == []


async def test_matrix_gate_off_topic(monkeypatch) -> None:
    _patch_pipeline(monkeypatch, "{}", route=Route.OFF_TOPIC)
    r = await _run("What is the capital of France?")
    assert (r.outcome, r.grounded, r.confidence) == ("blocked", False, 0.0)
    assert r.guardrail_flags == ["off_topic"]
    assert r.answer == catalog.off_topic_line()


async def test_matrix_llm_error_is_its_own_outcome(monkeypatch) -> None:
    import httpx
    import openai

    error = openai.APITimeoutError(request=httpx.Request("POST", "https://x"))
    _patch_pipeline(monkeypatch, error)
    r = await _run()
    assert (r.outcome, r.grounded, r.confidence) == ("llm_failed", False, 0.0)
    assert r.answer == catalog.TRY_AGAIN_LINE


async def test_matrix_verifier_bug_is_an_error_not_a_no_answer(monkeypatch) -> None:
    _patch_pipeline(
        monkeypatch, _reply([_claim("It covers 42 controls.", "42 controls")])
    )

    def boom(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.setattr(orchestrator, "ground_answer", boom)
    r = await _run()
    assert (r.outcome, r.grounded, r.confidence) == ("verification_error", False, 0.0)
    assert r.answer == catalog.TRY_AGAIN_LINE


def test_matrix_fixed_fallback_lines_come_from_the_catalog() -> None:
    cases = {
        ("unsafe_input_detected", "off_topic"): catalog.policy_template("unsafe"),
        ("spam_detected",): catalog.SPAM_LINE,
        ("unclear_input",): catalog.UNCLEAR_LINE,
        ("query_too_long",): catalog.TOO_LONG_LINE,
        ("off_topic",): catalog.off_topic_line(),
        ("llm_call_failed",): catalog.TRY_AGAIN_LINE,
        ("not_covered",): catalog.NO_ANSWER_LINE,
        ("no_sources",): catalog.NO_ANSWER_LINE,
    }
    for flags, line in cases.items():
        r = orchestrator._fallback_response(
            request_id="x", flags=list(flags), latency_ms=1.0
        )
        assert (r.answer, r.grounded, r.confidence, r.sources) == (line, False, 0.0, [])


# ---------------------------------------------------------------------------
# Locality: a value is backed only together with its own subject
# (real chunks of the site snapshot, built the way ingestion builds them)
# ---------------------------------------------------------------------------

_DATA = Path(__file__).resolve().parents[1] / "data"
_EN = "\N{EN DASH}"
_CRUMB = "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}"


@cache
def _page_chunks(name: str) -> dict[str, SourceChunk]:
    """The section-tree chunks of ``data/<name>``, keyed by section heading."""
    page = parse_site_page((_DATA / name).read_text(encoding="utf-8"))
    chunks = SectionTreeChunker().chunk_sync(
        clean_markdown(page.body), {"source_name": name, "page_title": page.title}
    )
    out: dict[str, SourceChunk] = {}
    for n, chunk in enumerate(chunks):
        heading = str(chunk.metadata.get("section_heading") or "")
        out.setdefault(
            f"{heading}#{len([k for k in out if k.startswith(heading + '#')])}",
            SourceChunk(
                chunk_id=f"{name}-{n}",
                document_id=f"doc-{name}",
                content=chunk.content,
                score=0.8,
                metadata=chunk.metadata,
            ),
        )
    return out


def _tier(heading: str) -> SourceChunk:
    """The home page's "Not sure which service fits?" card for one tier."""
    return next(
        c
        for k, c in _page_chunks("home.md").items()
        if k.startswith(f"{heading}#") and "Typical roadmap" in c.content
    )


def _sba() -> SourceChunk:
    return next(
        c
        for c in _page_chunks("microsoft-365-security-baseline-assessment.md").values()
        if "42 controls" in c.content
    )


def _packages_baseline() -> SourceChunk:
    return next(
        c for c in _page_chunks("packages.md").values() if "39 controls" in c.content
    )


def _rows_chunk() -> SourceChunk:
    """One chunk listing several tier rows (the real lines of both cards)."""
    lines = [
        f"Redefining Cybersecurity & Compliance {_CRUMB} Not sure which service fits?"
    ]
    for name in ("Baseline", "Foundation"):
        body = _tier(name).content
        lines += [name, *[ln for ln in body.split("\n") if ln.startswith("- ")][:2]]
    return SourceChunk(
        chunk_id="rows", document_id="doc-rows", content="\n".join(lines),
        score=0.8, metadata={"source_name": "home.md"},
    )  # fmt: skip


_FOUNDATION_Q = (
    "How long does the Foundation tier take and which framework is it aligned to?"
)


def _verdict(
    claims: list[dict], sources: list[SourceChunk], query: str
) -> list[str | None]:
    draft = parse_draft(_reply(claims, [{"ask": query, "covered": True}]))
    assert draft is not None
    result = ground_answer(draft, sources, query)
    reasons = dict((c.text, r) for c, r in result.dropped)
    return [reasons.get(c["text"]) for c in claims]


def test_real_tier_cards_are_separate_chunks() -> None:
    assert f"1{_EN}2 weeks" in _tier("Baseline").content
    assert f"4{_EN}6 weeks" in _tier("Foundation").content
    assert "Foundation" not in _tier("Baseline").content


@pytest.mark.parametrize(
    ("text", "quote", "source"),
    [
        # Foundation answered with the Baseline card's values.
        (
            f"Typical roadmap: 1{_EN}2 weeks, aligned to CIS Controls IG1.",
            f"Typical roadmap: 1{_EN}2 weeks",
            1,
        ),
        (
            "Aligned framework: CIS Controls IG1.",
            "Aligned framework: CIS Controls IG1",
            1,
        ),
        (
            f"The Foundation tier takes 1{_EN}2 weeks.",
            f"Typical roadmap: 1{_EN}2 weeks",
            1,
        ),
    ],
)
def test_foundation_with_baselines_values_fails(
    text: str, quote: str, source: int
) -> None:
    sources = [_tier("Baseline"), _tier("Foundation")]
    assert _verdict([_claim(text, quote, source)], sources, _FOUNDATION_Q) != [None]


@pytest.mark.parametrize(
    ("text", "quote"),
    [
        (
            "Typical roadmap: 4 to 6 weeks, aligned to the Zero Trust framework.",
            f"Typical roadmap: 4{_EN}6 weeks",
        ),
        (
            f"The Foundation tier takes 4{_EN}6 weeks.",
            f"Typical roadmap: 4{_EN}6 weeks",
        ),
        ("Foundation is aligned to Zero Trust.", "Aligned framework: Zero Trust"),
    ],
)
def test_foundation_with_its_own_values_passes(text: str, quote: str) -> None:
    sources = [_tier("Baseline"), _tier("Foundation")]
    assert _verdict([_claim(text, quote, 2)], sources, _FOUNDATION_Q) == [None]


def test_rows_in_one_chunk_back_only_their_own_subject() -> None:
    rows = [_rows_chunk()]
    verdicts = _verdict(
        [
            _claim(
                f"Foundation takes 1{_EN}2 weeks.", f"Typical roadmap: 1{_EN}2 weeks"
            ),
            _claim("Foundation follows CIS Controls IG1.", "CIS Controls IG1"),
            _claim(
                f"Foundation takes 4{_EN}6 weeks.", f"Typical roadmap: 4{_EN}6 weeks"
            ),
            _claim(f"Baseline takes 1{_EN}2 weeks.", f"Typical roadmap: 1{_EN}2 weeks"),
        ],
        rows,
        "How long do Baseline and Foundation take?",
    )
    assert verdicts == ["subject_not_local", "subject_not_local", None, None]
    # The question's subject counts when the claim names none.
    assert _verdict(
        [_claim(f"Typical roadmap: 1{_EN}2 weeks.", f"Typical roadmap: 1{_EN}2 weeks")],
        rows,
        _FOUNDATION_Q,
    ) == ["subject_not_local"]


def test_value_must_be_in_the_quote_not_elsewhere_in_the_chunk() -> None:
    claim = _claim(
        f"Baseline takes 4{_EN}6 weeks.", "Aligned framework: CIS Controls IG1"
    )
    assert _verdict([claim], [_rows_chunk()], "q?") == ["value_not_in_quote"]


def test_conflicting_sources_each_back_only_their_own_value() -> None:
    sources = [_packages_baseline(), _sba()]
    query = "How many controls does the Baseline package check?"
    claims = [
        _claim("The Baseline package checks 39 controls.", "39 controls across 5 focus areas"),
        _claim("The assessment covers 42 controls.", "42 controls", 2),
        _claim("The Baseline package checks 42 controls.", "39 controls", 1),
        _claim("It covers 39 to 42 controls.", "42 controls", 2),
        _claim("It covers 39 to 42 controls.", "39 controls ... 42 controls", 1),
    ]  # fmt: skip
    assert _verdict(claims[:2], sources, query) == [None, None]
    assert _verdict(claims[2:3], sources, query) != [None]
    assert _verdict(claims[3:4], sources, query) != [None]
    merged = SourceChunk(
        chunk_id="m", document_id="doc-m", score=0.8, metadata={},
        content=f"{_packages_baseline().content}\n{_sba().content}",
    )  # fmt: skip
    assert _verdict(claims[4:], [merged], query) == ["merged_values"]


def test_matrix_canned_and_clarify_are_never_grounded() -> None:
    canned = orchestrator._canned_response(
        "Hi!", request_id="x", start=0.0, follow_up_detected=False, history_turns_sent=0
    )
    assert (canned.grounded, canned.confidence, canned.outcome) == (
        False,
        0.0,
        "conversational",
    )


# ---------------------------------------------------------------------------
# Compact schema (p/s/q/t/k, a/c) and the length rules
# ---------------------------------------------------------------------------
def _compact(claims: list[dict], parts: list[dict] | None = None) -> str:
    return json.dumps(
        {
            "policy": "none",
            "parts": parts or [{"a": "the controls", "c": True}],
            "claims": claims,
        }
    )


def _c(q: str, t: str = "", s: int = 1, p: int = 0, k: str = "s") -> dict:
    return {"p": p, "s": s, "q": q, "t": t, "k": k}


def test_compact_keys_parse_and_empty_text_is_the_quote():
    draft = parse_draft(
        _compact(
            [
                _c("Nothing changes in your environment without your sign-off."),
                _c("42 controls", t="The assessment covers 42 controls.", k="i"),
            ]
        )
    )
    assert draft is not None
    assert draft.parts[0].ask == "the controls" and draft.parts[0].covered
    first, second = draft.claims
    assert first.text == first.quote
    assert (second.text, second.kind) == ("The assessment covers 42 controls.", "item")


def test_empty_text_claim_still_runs_every_check():
    answer = ground_answer(
        parse_draft(
            _compact(
                [
                    _c("Nothing changes in your environment without your sign-off."),
                    _c("Typically 3 to 4 weeks."),  # not in the source
                ]
            )
        ),
        [_source(SBA)],
        "what is the assessment?",
    )
    assert [c.text for c in answer.kept] == [
        "Nothing changes in your environment without your sign-off."
    ]
    assert answer.dropped[0][1] == "quote_not_in_source"


def test_short_bullets_quoting_the_item_verify_and_cap_at_eight():
    source = _source(
        "Frontier package\nEverything in Strengthen, plus:\n"
        + "\n".join(f"- Feature number {w} included" for w in "abcdefghij")
    )
    claims = [_c("Everything in Strengthen, plus:", t="Frontier adds to Strengthen.")]
    claims += [_c(f"Feature number {w} included", k="i") for w in "abcdefghij"]
    answer = ground_answer(
        parse_draft(_compact(claims, [{"a": "what Frontier includes", "c": True}])),
        [source],
        "What does the Frontier package include?",
    )
    assert len(answer.kept) == 11  # all verified
    bullets = [line for line in answer.text.splitlines() if line.startswith("- ")]
    assert len(bullets) == 8


def test_shared_bullet_lead_is_cut():
    source = _source(
        "Frontier\nThe Frontier package includes 24/7 detection and response.\n"
        "The Frontier package includes AI data governance controls.\n"
        "The Frontier package includes continuous posture reviews."
    )
    claims = [
        _c("The Frontier package includes 24/7 detection and response", k="i"),
        _c("The Frontier package includes AI data governance controls", k="i"),
        _c("The Frontier package includes continuous posture reviews", k="i"),
    ]
    answer = ground_answer(
        parse_draft(_compact(claims, [{"a": "what Frontier includes", "c": True}])),
        [source],
        "What does the Frontier package include?",
    )
    assert "- 24/7 detection and response [1]" in answer.text
    # The shared lead is said once, as the list's lead sentence.
    assert answer.text.count("The Frontier package includes") == 1
    assert answer.text.startswith("The Frontier package includes:")


def test_sentences_repeating_one_lead_render_as_lead_plus_bullets():
    source = _source(
        "Frontier\nThe Frontier package includes everything in Strengthen.\n"
        "The Frontier package includes 24/7 detection and response.\n"
        "The Frontier package includes AI data governance controls."
    )
    claims = [
        _c("The Frontier package includes everything in Strengthen."),
        _c("The Frontier package includes 24/7 detection and response."),
        _c("The Frontier package includes AI data governance controls."),
    ]
    answer = ground_answer(
        parse_draft(_compact(claims, [{"a": "what Frontier includes", "c": True}])),
        [source],
        "What does the Frontier package include?",
    )
    assert answer.text.startswith(
        "The Frontier package includes everything in Strengthen. [1]"
    )
    assert "- 24/7 detection and response. [1]" in answer.text
    assert answer.text.count("The Frontier package includes") == 1


def test_a_sentence_restating_the_reply_is_dropped():
    source = _source(
        "Frontier\nFrontier includes 24/7 managed detection and response.\n"
        "24/7 managed detection and response is included in Frontier."
    )
    claims = [
        _c("Frontier includes 24/7 managed detection and response."),
        _c("24/7 managed detection and response is included in Frontier."),
    ]
    answer = ground_answer(
        parse_draft(_compact(claims, [{"a": "what Frontier includes", "c": True}])),
        [source],
        "What does Frontier include?",
    )
    assert len(answer.kept) == 2
    assert answer.text.count("managed detection") == 1


def test_word_budget_drops_extra_claims_but_never_a_part(monkeypatch):
    from app.harness import claims as claims_module

    monkeypatch.setattr(
        claims_module.get_settings(), "answer_max_words", 8, raising=False
    )
    answer = ground_answer(
        parse_draft(
            _compact(
                [
                    _c(
                        "Nothing changes in your environment without your sign-off.",
                        p=0,
                    ),
                    _c("We evaluate your tenant across five areas", p=0),
                    _c("Typically 1 to 2 weeks.", p=1),
                ],
                [{"a": "the assessment", "c": True}, {"a": "how long", "c": True}],
            )
        ),
        [_source(SBA)],
        "What is the assessment and how long does it take?",
    )
    assert len(answer.kept) == 3
    assert "five areas" not in answer.text
    assert "1 to 2 weeks" in answer.text
    assert not answer.uncovered


# ---------------------------------------------------------------------------
# Final-run fixes: true claims that were dropped, fabrications that passed,
# and rendering (removes / restructures only).
# ---------------------------------------------------------------------------

HOME = (
    "Redefining Cybersecurity & Compliance\n\n"
    "Security that works the way your team does.\nThe Intelligence Shift\n\n"
    "The perfect balance of intelligent agents, automation, and human judgment.\n\n"
    "- 01 Agentic by design\n- 02 Continuous automation\n- 03 Human when it matters\n"
    "- 04 Fast by default\n- 05 Secure at every step\n"
)
VISION = (
    "Our Vision\n\n- Centre circle: No Tradeoffs\n"
    "- Chips below: Responsible AI \u00b7 Secure AI \u00b7 Learning Loops"
)
JOURNEY = (
    "Packages \u203a How it works\n\nOnboard \u2192 Assess \u2192 Recommend \u2192 "
    "Approve \u2192 Apply \u2192 Validate \u2192 Improve"
)
CONTACT_PAGE = (
    "Contact Us\n\nAustralia\nSydney Office\nSuite B5-B6, 12-14 Solent Circuit, Norwest\n"
    "Melbourne Office\nLevel 2 Riverside Quay, 1 Southbank Blvd\nCall\n1300 394 468\n"
    "Mail\ninfo@deployiq.co\nEnquiry category tabs\n- Sales\n- Support\n- Careers"
)
WHO_FOR = (
    "Deploy IQ Platform \u203a Who Deploy IQ Platform Is For\n\n"
    "Designed for organisations that need structured, predictable, and "
    "accountable security uplift\n\nStartups\n\nBuilding secure, scalable "
    "environments. Turn the struggle up with best practices.\n\n"
    "Mature teams\n\nSeeking consistent execution across distributed responsibilities."
)
GAIN = (
    "Deploy IQ Platform \u203a What Organisations Gain\n\n"
    "Using Deploy IQ, organisations benefit from:\n\n"
    "- Hardened configuration states aligned to recognised frameworks.\n"
    "- Reduced exposure through prioritised, validated implementation."
)
TIERS = (
    "Microsoft 365 Security Packages \u203a Packages\n\nBaseline\n\n"
    "A point-in-time assessment of your tenant.\n\nStrengthen\n\n"
    "Everything in Foundation, plus:\n- Risk-based access for sign-ins\n"
    "- Just-in-time admin access"
)


def _answer(claims, sources, query, parts=None, **kwargs):
    draft = parse_draft(_compact(claims, parts or [{"a": "the ask", "c": True}]))
    assert draft is not None
    return ground_answer(draft, sources, query, **kwargs)


def _reasons(answer) -> list[str]:
    return [reason for _, reason in answer.dropped]


def test_list_items_under_a_list_heading_verify_without_page_numerals():
    # home-intel-04: items 02-05 sit 3+ lines below "The Intelligence Shift".
    items = [
        "01 Agentic by design",
        "02 Continuous automation",
        "03 Human when it matters",
        "04 Fast by default",
        "05 Secure at every step",
    ]
    answer = _answer(
        [_c(q, k="i") for q in items],
        [_source(HOME, name="home.md")],
        "What are the five principles of 'The Intelligence Shift'?",
    )
    assert len(answer.kept) == 5, answer.dropped
    assert "- Secure at every step [1]" in answer.text
    assert "01" not in answer.text
    assert "05" not in answer.text


def test_a_count_of_the_quotes_own_list_verifies():
    # our-vision-02 ("three") and packages-12 ("seven", arrows in the chunk).
    three = _answer(
        [
            _c(
                "Responsible AI \u00b7 Secure AI \u00b7 Learning Loops",
                t="The three themes are Responsible AI, Secure AI and Learning Loops.",
            )
        ],
        [_source(VISION, name="our-vision.md")],
        "What three themes sit underneath the no-tradeoffs vision?",
    )
    assert three.answered, three.dropped
    seven = _answer(
        [
            _c(
                "Onboard Assess Recommend Approve Apply Validate Improve",
                t="There are seven stages: Onboard, Assess, Recommend, Approve, "
                "Apply, Validate and Improve.",
            )
        ],
        [_source(JOURNEY, name="packages.md")],
        "How many stages are in the journey?",
    )
    assert seven.answered, seven.dropped


@pytest.mark.parametrize(
    "text",
    [
        "Onboarding: Onboard, Assess, Recommend, Approve, Apply, Validate and Improve.",
        "There are six stages: Onboard, Assess, Recommend, Approve, Apply, "
        "Validate and Improve.",
    ],
)
def test_a_lead_in_that_is_not_the_lists_count_is_still_an_item_name(text):
    answer = _answer(
        [_c("Onboard Assess Recommend Approve Apply Validate Improve", t=text)],
        [_source(JOURNEY, name="packages.md")],
        "How many stages are in the journey?",
    )
    assert not answer.answered
    assert [reason for _, reason in answer.dropped] == ["item_name_not_heading"]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (
            "The four themes are Responsible AI, Secure AI and Learning Loops.",
            "figure_not_in_source",
        ),
        (
            "The three themes take 3 weeks: Responsible AI, Secure AI and Learning Loops.",
            "figure_not_in_source",
        ),
    ],
)
def test_a_count_the_list_does_not_back_still_fails(text: str, reason: str):
    answer = _answer(
        [_c("Responsible AI \u00b7 Secure AI \u00b7 Learning Loops", t=text)],
        [_source(VISION, name="our-vision.md")],
        "What themes sit underneath the vision?",
    )
    assert _reasons(answer) == [reason]


def test_verbatim_quote_cited_with_the_wrong_source_number_is_re_attributed():
    # hard-negative-06 / privacy section 8: the quote is verbatim in Source 2.
    retention = _source(
        "Privacy Policy \u203a 8 Retention\n\nInformation is retained only for the "
        "duration needed to deliver services or meet legal obligations. It is "
        "then securely archived or disposed of.",
        n=2,
        name="privacy.md",
    )
    quote = (
        "Information is retained only for the duration needed to deliver "
        "services or meet legal obligations."
    )
    answer = _answer(
        [_c(quote, s=1), _c("It is retained for seven years.", s=1)],
        [_source(SBA), retention],
        "How long do you keep personal information?",
    )
    assert [c.source for c in answer.kept] == [2]
    assert _reasons(answer) == ["quote_not_in_source"]


def test_contact_values_with_framing_words_verify_and_render_once():
    # contact-05: "Our phone number is ..." over "Call / 1300 394 468".
    answer = _answer(
        [
            _c("1300 394 468", t="Our phone number is 1300 394 468."),
            _c("info@deployiq.co", t="You can email info@deployiq.co."),
            _c("1300 394 468", t="Our Australia phone number is 1300 394 468."),
        ],
        [_source(CONTACT_PAGE, name="contact-us.md")],
        "How can I get in touch?",
    )
    assert not answer.dropped
    assert answer.text.count("1300 394 468") == 1  # stated once, not twice
    assert "info@deployiq.co" in answer.text


@pytest.mark.parametrize(
    "text",
    [
        "Our phone number is 1300 394 469.",
        "Our support line is open around the clock on 1300 394 468.",
    ],
)
def test_contact_framing_never_backs_a_new_fact(text: str):
    answer = _answer([_c("1300 394 468", t=text)], [_source(CONTACT_PAGE)], "phone?")
    assert not answer.answered


def test_short_items_verify_and_a_label_echoing_the_question_does_not():
    # "Support" / "ML3" have no content stem; "TARGET MATURITY LEVEL." only
    # repeats the question.
    source = _source("Essential Eight\n\nTARGET MATURITY LEVEL\nML3\n" + CONTACT_PAGE)
    answer = _answer(
        [_c("Support", k="i"), _c("ML3", k="i"), _c("TARGET MATURITY LEVEL")],
        [source],
        "What target maturity level is shown, and what tabs are on the form?",
    )
    assert [c.text for c in answer.kept] == ["Support", "ML3"]
    assert _reasons(answer) == ["label_only"]


def test_a_generic_list_is_not_presented_as_one_audiences_benefits():
    # platform-10: the "What Organisations Gain" list attributed to startups.
    sources = [
        _source(WHO_FOR, n=1, name="platform.md"),
        _source(GAIN, n=2, name="platform.md"),
    ]
    answer = _answer(
        [
            _c(
                "Hardened configuration states aligned to recognised frameworks.",
                t="Deploy IQ helps startups gain hardened configurations.",
                s=2,
            ),
            _c(
                "Building secure, scalable environments.",
                t="Startups build secure, scalable environments with Deploy IQ.",
            ),
            _c(
                "Reduced exposure through prioritised, validated implementation.",
                t="Organisations gain reduced exposure.",
                s=2,
            ),
        ],
        sources,
        "Is the Deploy IQ platform suitable for startups?",
    )
    assert _reasons(answer) == ["subject_not_local"]
    assert "Startups build" in answer.text
    assert "Organisations gain" in answer.text


def test_a_claim_about_another_tier_than_asked_is_dropped():
    answer = _answer(
        [
            _c(
                "A point-in-time assessment of your tenant.",
                t="The Baseline package is a point-in-time assessment of your tenant.",
            ),
            _c(
                "Everything in Foundation, plus:",
                t="It includes everything in Foundation.",
            ),
            _c("Risk-based access for sign-ins", k="i"),
        ],
        [_source(TIERS, name="packages.md")],
        "What does the Strengthen package include?",
    )
    assert _reasons(answer) == ["other_subject"]
    assert "everything in Foundation" in answer.text


def test_list_lead_restated_facts_and_fragments_render_cleanly():
    answer = _answer(
        [
            _c("Sales", k="i"),
            _c("Support", k="i"),
            _c(
                "Enquiry category tabs",
                t="The enquiry category tabs are on the contact form.",
            ),
            _c("Call"),
            _c("Careers", k="i"),
        ],
        [_source(CONTACT_PAGE)],
        "What can I pick on the contact form?",
    )
    lines = answer.text.splitlines()
    assert lines[0].startswith("The enquiry category tabs")  # lead before bullets
    assert [line for line in lines if line.startswith("- ")] == [
        "- Sales [1]",
        "- Support [1]",
        "- Careers [1]",
    ]
    assert "Call." not in answer.text  # a bare page label


def test_flattened_numbered_steps_become_items():
    source = _source("Steps\n1. Step 1 Onboard\n2. Step 2 Assess\n3. Step 3 Recommend")
    answer = _answer(
        [
            _c(
                "1. Step 1 Onboard 2. Step 2 Assess 3. Step 3 Recommend",
                t="Step 1, Onboard 2. Step 2, Assess 3. Step 3, Recommend",
                k="i",
            )
        ],
        [source],
        "What are the steps?",
        numbered=True,
    )
    assert answer.text.splitlines() == [
        "1. Onboard [1]",
        "2. Assess [1]",
        "3. Recommend [1]",
    ]


def test_sentences_sharing_a_tail_become_lead_and_bullets():
    tail = "is one of Deploy IQ's Responsible AI principles."
    heads = ("Transparency", "Accountability", "Human oversight")
    source = _source("Responsible AI\n" + "\n".join(f"{w} {tail}" for w in heads))
    answer = _answer(
        [_c(f"{w} {tail}") for w in heads],
        [source],
        "What are your Responsible AI principles?",
    )
    assert answer.text.count(tail) == 1
    assert "- Accountability [1]" in answer.text
    assert "- Human oversight [1]" in answer.text


def test_fixed_sales_line_replaces_a_model_sales_sentence():
    sources = [_source(SBA), _source(CONTACT, n=2, name="contact-us.md")]
    contact = find_sales_contact(sources)
    assert contact is not None
    answer = _answer(
        [
            _c("Nothing changes in your environment without your sign-off."),
            _c(
                "Call 1300 394 468",
                t="You can buy the service by calling 1300 394 468.",
                s=2,
            ),
        ],
        sources,
        "How do I buy the assessment?",
        sales_contact=contact,
    )
    assert "You can buy the service" not in answer.text
    assert answer.text.count("1300 394 468") == 1
    assert contact.line in answer.text


def test_fixed_lines_count_towards_the_word_cap(monkeypatch):
    from app.harness import claims as claims_module  # noqa: PLC0415

    monkeypatch.setattr(
        claims_module.get_settings(), "answer_max_words", 30, raising=False
    )
    answer = _answer(
        [
            _c("Nothing changes in your environment without your sign-off."),
            _c("We evaluate your tenant across five areas that matter most"),
        ],
        [_source(SBA)],
        "What is the assessment and who founded you?",
        parts=[
            {"a": "the assessment", "c": True},
            {"a": "who founded you", "c": False},
        ],
    )
    assert "five areas" not in answer.text  # the partial line took the room
    assert "sign-off" in answer.text
    assert answer.uncovered == ["who founded you"]


@pytest.mark.parametrize(
    ("ask", "query", "expected"),
    [
        (
            "do you have one in Perth",
            "Where are your offices, and do you have one in Perth?",
            ["an office in Perth"],
        ),
        (
            "suggest I talk to a specialist",
            "not sure, suggest I talk to a specialist",
            None,
        ),
        # Not the whole question: a part restating it is answered by any claim.
        ("thanks, bye - oh", "What is the assessment? thanks, bye - oh", None),
    ],
)
def test_partial_line_names_a_noun_phrase_or_nothing(ask, query, expected):
    draft = parse_draft(
        _compact(
            [_c("Nothing changes in your environment without your sign-off.")],
            [{"a": "the assessment", "c": True}, {"a": ask, "c": False}],
        )
    )
    assert draft is not None
    assert uncovered_parts(draft, list(draft.claims), query) == expected


@pytest.mark.parametrize(
    ("text", "shipped"),
    [
        ("01, Access Control & Identity", "Access Control & Identity"),
        ("05 Secure at every step", "Secure at every step"),
        ("Steps (in order): Onboard, Assess", "Onboard, Assess"),
        (
            "The Zero Trust, Starter plan is self-service.",
            "The Zero Trust Starter plan",
        ),
    ],
)
def test_page_numerals_labels_and_name_commas_never_ship(text: str, shipped: str):
    source = _source(
        "Controls\n- 01 Access Control & Identity\n- 05 Secure at every step\n"
        "Onboard, Assess\nZero Trust - Starter plan is self-service."
    )
    quote = {
        "01, Access Control & Identity": "01 Access Control & Identity",
        "05 Secure at every step": "05 Secure at every step",
        "Steps (in order): Onboard, Assess": "Onboard, Assess",
    }.get(text, "Starter plan is self-service")
    answer = _answer([_c(quote, t=text, k="i")], [source], "What are the controls?")
    assert answer.answered, answer.dropped
    assert answer.text.startswith(f"- {shipped}")


def test_items_framed_in_the_questions_words_and_page_numerals_verify():
    # which-03 ("The service handles Cryptography") and intel-04 ("principle two").
    iso = _source(
        "ISO 27001 Technical Controls\n- 01 Access Control & Identity\n"
        "- 02 Cryptography\n- 03 Logging & Monitoring"
    )
    answer = _answer(
        [
            _c(
                "Access Control & Identity",
                t="The service handles Access Control.",
                k="i",
            ),
            _c("02 Cryptography", t="The service handles Cryptography.", k="i"),
            _c("02 Cryptography", t="The service outsources Cryptography.", k="i"),
            _c("02 Cryptography", t="Control area two is Cryptography.", k="i"),
            _c("02 Cryptography", t="Control area four is Cryptography.", k="i"),
        ],
        [iso],
        "List the ISO 27001 control areas you handle in Microsoft 365.",
    )
    assert [c.text for c in answer.kept] == [
        "The service handles Access Control.",
        "The service handles Cryptography.",
        "Control area two is Cryptography.",
    ]
    assert _reasons(answer) == ["low_overlap", "figure_not_in_source"]


def _page_source(content: str, n: int, doc: int) -> SourceChunk:
    return SourceChunk(
        chunk_id=f"00000000-0000-0000-0000-0000000002{n:02d}",
        document_id=f"00000000-0000-0000-0000-0000000003{doc:02d}",
        content=content,
        score=0.8,
        metadata={"source_name": f"page{doc}.md"},
    )


def _ground_sources(claims: list[dict], sources: list[SourceChunk], query: str):
    draft = parse_draft(_reply(claims))
    assert draft is not None
    return ground_answer(draft, sources, query)


# The chunk's breadcrumb names the free assessment; the quote does not.
GET_STARTED = (
    "Get Started > Get Your Free Security Posture Assessment\n\n"
    "Your Security Score\nTakes only 5 minutes"
)
FREE_ASSESSMENT = "The free security posture assessment takes only 5 minutes."


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        # get-started-free-02: "free" is on the same page, about the assessment.
        (FREE_ASSESSMENT, None),
        # Same page, another subject: the workshop is never called free.
        (
            "The free onboarding workshop takes only 5 minutes.",
            "assurance_not_in_quote",
        ),
    ],
)
def test_free_is_backed_by_the_same_page_about_the_same_subject(
    text: str, reason: str | None
) -> None:
    answer = _ground_sources(
        [_claim(text, "Takes only 5 minutes")],
        [_page_source(GET_STARTED, 1, 1)],
        "Is the assessment free?",
    )
    assert _reasons(answer) == ([reason] if reason else [])


@pytest.mark.parametrize(
    ("hero", "query"),
    [
        # Another page saying "free" never backs it ...
        ("Pricing\n\nGet Your Free Security Posture Assessment", "How long is it?"),
        # ... nor does the question saying it.
        ("Pricing\n\nPlans", "Is the free security posture assessment free?"),
    ],
)
def test_free_from_another_page_or_the_question_is_dropped(
    hero: str, query: str
) -> None:
    score = (
        "Get Started > Your Security Score\n\nYour Security Score\nTakes only 5 minutes"
    )
    answer = _ground_sources(
        [_claim(FREE_ASSESSMENT, "Takes only 5 minutes")],
        [_page_source(score, 1, 1), _page_source(hero, 2, 2)],
        query,
    )
    assert not answer.answered


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        # home-snapshot-13: "No" is a determiner here, not an answer word.
        ("The snapshot requires no technical expertise.", True),
        ("The snapshot requires technical expertise.", False),
    ],
)
def test_leading_no_determiner_counts_as_negation(text: str, kept: bool) -> None:
    page = "Snapshot\n\nSecurity Posture Snapshot\nNo technical expertise required"
    answer = _ground_sources(
        [_claim(text, "No technical expertise required")],
        [_page_source(page, 1, 1)],
        "Do I need technical expertise for the snapshot?",
    )
    assert answer.answered is kept


TERMS_DOWNLOADS = (
    "Terms > 12 Downloads\n\nDownloadable Materials are provided as is. Users are "
    "solely responsible for reviewing, testing (including in non-production "
    "environments), and determining suitability before use."
)
TERMS_QUOTE = (
    "Users are solely responsible for reviewing, testing (including in "
    "non-production environments), and determining suitability before use."
)


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        # terms-13: the cited chunk's own term, not stock wording.
        (
            "Users are solely responsible for reviewing and testing Downloadable "
            "Materials before use.",
            True,
        ),
        (
            "The materials say users are solely responsible for testing before use.",
            False,
        ),
        ("Users are solely responsible for testing these materials before use.", False),
        ("Materials: users are solely responsible for testing before use.", False),
    ],
)
def test_banned_word_is_allowed_only_as_the_cited_chunks_term(
    text: str, kept: bool
) -> None:
    answer = _ground_sources(
        [_claim(text, TERMS_QUOTE)],
        [_page_source(TERMS_DOWNLOADS, 1, 1)],
        "Should I test downloadable scripts?",
    )
    assert answer.answered is kept
    if not kept:
        assert _reasons(answer) == ["banned_wording"]


E8_REPORT = (
    "Essential Eight > Continuous Validation\n\n- Continuous evidence collection\n\n"
    "Overall Essential Eight Score\n"
    "This section shows an illustrative sample to explain the report, not real results."
)
E8_QUOTE = (
    "This section shows an illustrative sample to explain the report, not real results."
)


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        # essential-eight-score-16: "Eight" names the score heading above the quote.
        (
            "The Overall Essential Eight Score is an illustrative sample, not real results.",
            True,
        ),
        # A count that is no name still sits in the quote's own words.
        ("The report shows eight illustrative samples, not real results.", False),
        (
            "The Overall Essential Nine Score is an illustrative sample, not real results.",
            False,
        ),
    ],
)
def test_number_word_of_a_name_is_checked_near_the_quote(text: str, kept: bool) -> None:
    answer = _ground_sources(
        [_claim(text, E8_QUOTE)],
        [_page_source(E8_REPORT, 1, 1)],
        "Are the Essential Eight Score figures real?",
    )
    assert answer.answered is kept


ISO42001_FAQ = (
    "ISO 42001 > Frequently asked questions\n\nWill DeployIQ certify us against "
    "ISO 42001?\nNo. We focus on technical controls within your Microsoft 365 "
    "environment. Certification is carried out by an accredited certification body."
)
ISO42001_QUOTE = (
    "No. We focus on technical controls within your Microsoft 365 environment. "
    "Certification is carried out by an accredited certification body."
)


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        # iso42001-why-07 / mt-42001-certify: the page answers the question "No."
        ("DeployIQ does not certify organisations against ISO 42001.", True),
        (
            "We don't perform certification; an accredited certification body does.",
            True,
        ),
        # Negating another word of the quote is still a mismatch.
        (
            "We do not focus on technical controls within your Microsoft 365 environment.",
            False,
        ),
        ("We do not focus on ISO 42001.", False),
    ],
)
def test_no_answer_backs_negating_the_faq_question(text: str, kept: bool) -> None:
    answer = _ground_sources(
        [_claim(text, ISO42001_QUOTE)],
        [_page_source(ISO42001_FAQ, 1, 1)],
        "Will you certify us?",
    )
    assert answer.answered is kept


def test_negation_without_a_no_answer_is_still_a_mismatch() -> None:
    page = (
        "ISO 42001 > FAQ\n\nWe focus on technical controls within your Microsoft 365."
    )
    answer = _ground_sources(
        [
            _claim(
                "We do not focus on technical controls within your Microsoft 365.",
                "We focus on technical controls within your Microsoft 365.",
            )
        ],
        [_page_source(page, 1, 1)],
        "Will you certify us?",
    )
    assert _reasons(answer) == ["negation_mismatch"]


BOOKING = (
    "Book your session\n\nSession Details\n- Kick-off & Discovery\n- 30 minutes\n"
    "- Web conference details to follow"
)
CONTACT_CALL = "Contact us\n\nCall\n1300 394 468"


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        # para-kickoff: "30 minutes" is a value line, not another subject.
        ("The kick-off and discovery session is 30 minutes.", True),
        # A named other label of the page is still another subject.
        ("The session details web conference takes 30 minutes.", False),
    ],
)
def test_value_line_is_not_another_subject(text: str, kept: bool) -> None:
    answer = _ground_sources(
        [_claim(text, "30 minutes")],
        [_page_source(BOOKING, 1, 1), _page_source(CONTACT_CALL, 2, 2)],
        "how long's the kick-off call?",
    )
    assert answer.answered is kept


ISO27001_SCOPE = (
    "ISO 27001 > Scope\n\nISO 27001 is based on a complete Information Security "
    "Management System: policy, risk assessment, people and physical controls, "
    "internal audit, and an independent certification audit. Our scope is narrower "
    "and technical: the Annex A controls in Microsoft 365, on which your ISMS relies."
)


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        # i27-not-handled: "ISMS" is the quote's "Information Security Management System".
        (
            "ISO 27001 covers a complete ISMS: policy, risk assessment and internal audit.",
            True,
        ),
        # An acronym the quote does not spell out is still another subject.
        (
            "ISO 27001 covers a complete SOC: policy, risk assessment and internal audit.",
            False,
        ),
    ],
)
def test_acronym_spelled_out_in_the_quote_is_its_subject(text: str, kept: bool) -> None:
    quote = (
        "ISO 27001 is based on a complete Information Security Management System: "
        "policy, risk assessment, people and physical controls, internal audit"
    )
    answer = _ground_sources(
        [_claim(text, quote)],
        [_page_source(ISO27001_SCOPE, 1, 1)],
        "Which parts of ISO 27001 do you not handle?",
    )
    assert answer.answered is kept


NOT_ON_ITS_OWN = (
    "Not on its own. We deliver the technical controls and the evidence behind "
    "them. Compliance also depends on your policies, processes and people"
)


@pytest.mark.parametrize(
    ("text", "quote", "kept"),
    [
        # i27-not-handled: the claim restates the quote's un-negated sentence.
        (
            "We deliver the technical controls and the evidence behind them.",
            NOT_ON_ITS_OWN,
            True,
        ),
        # A negation no sentence of the quote makes is still a mismatch.
        (
            "We do not deliver the technical controls and the evidence behind them.",
            NOT_ON_ITS_OWN.removeprefix("Not on its own. "),
            False,
        ),
    ],
)
def test_negation_is_checked_per_sentence_of_the_quote(
    text: str, quote: str, kept: bool
) -> None:
    page = f"ISO 27001 > FAQ\n\nWill this make us compliant?\n{NOT_ON_ITS_OWN}."
    answer = _ground_sources(
        [_claim(text, quote)],
        [_page_source(page, 1, 1)],
        "Which parts of ISO 27001 do you not handle?",
    )
    assert answer.answered is kept


PACKAGE_TABLE = (
    "Packages > Compare\n\nCompare packages\n"
    "24/7 Managed Detection & Response: included in Frontier; not included in "
    "Baseline, Foundation, Strengthen."
)
MDR_QUOTE = (
    "24/7 Managed Detection & Response: included in Frontier; not included in "
    "Baseline, Foundation, Strengthen."
)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        # fp-baseline-mdr: what a package does not include is a fact, not a decline.
        (
            "The Baseline package does not include 24/7 Managed Detection & Response.",
            None,
        ),
        # A source-absence line stays a decline ...
        ("The page does not include details on Baseline response.", "decline"),
        ("We don't have details on Baseline Managed Detection & Response.", "decline"),
        # ... and the negation is still verified.
        (
            "Frontier does not include 24/7 Managed Detection & Response.",
            "negation_mismatch",
        ),
    ],
)
def test_product_negative_backed_by_its_quote_is_not_a_decline(
    text: str, reason: str | None
) -> None:
    answer = _ground_sources(
        [_claim(text, MDR_QUOTE)],
        [_page_source(PACKAGE_TABLE, 1, 1)],
        "Why does the Baseline package include 24/7 MDR?",
    )
    assert _reasons(answer) == ([reason] if reason else [])


BASELINE_HOME = (
    "Home > Not sure which service fits? > Baseline\n\nRecommended solution\n\n"
    "Expected outcome: A clear view of your exposure.\n\n"
    "- Aligned framework: CIS Controls IG1\n- Typical roadmap: 1-2 weeks"
)
MATURITY_PATH = "Home > Security maturity path\n\nStage\nBaseline\nFoundation"


@pytest.mark.parametrize(
    ("text", "query", "kept"),
    [
        # home-baseline-stage: "stage" only qualifies the asked "Baseline".
        (
            "The Baseline stage is aligned to the CIS Controls IG1 framework.",
            "What framework is the Baseline maturity stage aligned to?",
            True,
        ),
        # Two subjects joined by "and" still both need their own part.
        (
            "The Baseline and the stage are aligned to the CIS Controls IG1 framework.",
            "What framework are the Baseline and stage aligned to?",
            False,
        ),
    ],
)
def test_audience_word_qualifying_the_asked_label(
    text: str, query: str, kept: bool
) -> None:
    answer = _ground_sources(
        [_claim(text, "- Aligned framework: CIS Controls IG1")],
        [_page_source(BASELINE_HOME, 1, 1), _page_source(MATURITY_PATH, 2, 2)],
        query,
    )
    assert answer.answered is kept


ONBOARD_JOURNEY = (
    "About > How Deploy IQ Works\n\nThe same governed journey, with human approval:\n\n"
    "1. Onboard\n2. Assess\n3. Recommend\n4. Approve\n5. Apply\n6. Validate\n7. Improve\n\n"
    "Your team confirms the connection scope and target standard, reviews the "
    "remediation plan and approves each recommendation before agents apply it."
)
JOURNEY_QUOTE = (
    "Your team confirms the connection scope and target standard, reviews the "
    "remediation plan and approves each recommendation before agents apply it."
)


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        # home-onboard-step: "approves ... apply" are verbs, not the far steps.
        (JOURNEY_QUOTE, True),
        # Naming another step as the subject is still another subject.
        (
            "In the Approve step your team confirms the connection scope and target "
            "standard and reviews the remediation plan.",
            False,
        ),
    ],
)
def test_step_words_used_as_verbs_are_not_another_subject(
    text: str, kept: bool
) -> None:
    answer = _ground_sources(
        [_claim(text, JOURNEY_QUOTE)],
        [_page_source(ONBOARD_JOURNEY, 1, 1)],
        "What happens in the Onboard step of the DeployIQ journey?",
    )
    assert answer.answered is kept


PRIVACY_USES = (
    "Privacy Policy > 3 How We Use Information\n\nDeploy IQ uses information to:\n\n"
    "- respond to enquiries\n- assess environment context\n- scope and deliver services\n"
    "- maintain business records"
)
TERMS_DATA = (
    "Terms > 6 Data Handling\n\nCustomer Data\n"
    "Deploy IQ processes Customer Data only for delivering the Services."
)


def test_off_question_list_from_a_chunk_without_the_asked_term_is_dropped() -> None:
    # terms-09: the privacy list is about "information", never "Customer Data".
    quote = (
        "Deploy IQ uses information to: - respond to enquiries - assess environment "
        "context - scope and deliver services - maintain business records"
    )
    answer = _ground_sources(
        [
            _claim(
                "Deploy IQ uses information to respond to enquiries, assess "
                "environment context, scope and deliver services and maintain "
                "business records.",
                quote,
            )
        ],
        [_page_source(PRIVACY_USES, 1, 1), _page_source(TERMS_DATA, 2, 2)],
        "What does Deploy IQ use our Customer Data for?",
    )
    assert _reasons(answer) == ["other_subject"]


PLATFORM_ENABLES = (
    "Platform > What Deploy IQ Platform Enables\n\n"
    "Clear alignment to intent and obligations\n"
    "Security decisions and actions are anchored to organisational goals.\n\n"
    "Deploy IQ provides a governed, continuous security and compliance system that "
    "connects business objectives, regulatory requirements, and real-world "
    "environment signals into structured, traceable action."
)


def _platform_source() -> SourceChunk:
    source = _page_source(PLATFORM_ENABLES, 1, 1)
    source.metadata["section_heading"] = "What Deploy IQ Platform Enables"
    return source


@pytest.mark.parametrize(
    ("query", "kept"),
    [
        # plat-enables: the asked section's own row is the answer ...
        ("What does the Deploy IQ platform enable?", True),
        # ... but under another question it is a stray heading.
        ("How do I book a consultation?", False),
    ],
)
def test_row_of_the_asked_section_is_not_a_heading_fragment(
    query: str, kept: bool
) -> None:
    quote = "Clear alignment to intent and obligations"
    answer = _ground_sources(
        [_claim("Clear alignment to intent and obligations.", quote)],
        [_platform_source()],
        query,
    )
    assert answer.answered is kept


def test_prose_with_commas_is_not_split_into_bullets() -> None:
    # plat-enables: "a governed, continuous ... X, Y, and Z into structured,
    # traceable action" is one sentence, not a five-item list.
    sentence = (
        "Deploy IQ provides a governed, continuous security and compliance system "
        "that connects business objectives, regulatory requirements, and real-world "
        "environment signals into structured, traceable action."
    )
    answer = _ground_sources(
        [_claim(sentence, sentence)],
        [_platform_source()],
        "What does the Deploy IQ platform enable?",
    )
    assert "\n- " not in answer.text
    assert answer.text.startswith(sentence)


def test_real_series_is_still_split_into_bullets() -> None:
    page = (
        "Services > Scope\n\nThe service includes identity hardening, email "
        "protection, endpoint compliance, backup validation, and audit reporting."
    )
    sentence = (
        "The service includes identity hardening, email protection, endpoint "
        "compliance, backup validation, and audit reporting."
    )
    answer = _ground_sources(
        [_claim(sentence, sentence)],
        [_page_source(page, 1, 1)],
        "What does the service include?",
    )
    assert "\n- Identity hardening" in answer.text


MAIL_PAGE = "Contact Us\n\nCall\n1300 394 468\n\nMail\ninfo@deployiq.co"


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        # buy-book: "Email" frames the quote's own address ("Mail info@...").
        ("Email info@deployiq.co to book.", True),
        # A name the chunk does not have is still dropped.
        ("Email Telstra at info@deployiq.co to book.", False),
    ],
)
def test_contact_framing_word_is_not_a_name(text: str, kept: bool) -> None:
    answer = _ground_sources(
        [_claim(text, "Mail info@deployiq.co")],
        [_page_source(MAIL_PAGE, 1, 1)],
        "How do I book a consultation?",
    )
    assert answer.answered is kept
