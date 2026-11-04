"""Quote-backed claim verification (app.harness.claims) and the label matrix.

The verifier is deterministic, so it is unit-tested here. These tests mock
the LLM reply: they pin the code contract only; behaviour evidence comes from
real /query calls (scripts/live_golden_eval.py).
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import pytest

from app.chunking.section_tree import SectionTreeChunker
from app.guardrails.intent import IntentMatch
from app.harness import orchestrator
from app.harness import prompt_catalog as catalog
from app.harness.claims import ground_answer, parse_draft
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
    assert result.text.endswith(catalog.partial_line(["the price"]))
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
    from app.harness.claims import find_sales_contact

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

    monkeypatch.setattr(orchestrator, "classify_intent", fake_intent)
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
    assert r.answer.endswith(catalog.partial_line(["the price"]))


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
    assert "The Frontier package includes" not in answer.text


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
    assert answer.uncovered == []
