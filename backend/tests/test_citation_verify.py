"""Citation verification: a marker survives only if the cited chunk has the claim."""

import time

import pytest

from app.guardrails.post_checks import is_decline_sentence, is_refusal
from app.harness import orchestrator
from app.harness.citations import verify_and_select, verify_citations
from app.schemas.query import SourceChunk

_ABOUT = (
    "Section: Our Purpose\n\nAbout Us\n\nSecurity delivery often slows down under "
    "manual effort and scattered tools. DeployIQ was created to bring structure, "
    "speed, and governance to this work. Book a demo or talk to a specialist."
)
_E8 = (
    "Section: How DeployIQ Implements Essential Eight\n\nDeployIQ combines AI "
    "acceleration with human oversight to deliver Essential Eight rapidly, safely "
    "and consistently. The full process from assessment to audit-ready deployment "
    "is achieved in days, not months."
)
_ZT_PLANS = (
    "Section: Zero Trust plans\n\nZero Trust Starter (Free) gives access to the "
    "Microsoft Zero Trust Assessment tool and guidance to run it. Zero Trust "
    "Professional adds guided remediation with specialist support."
)
_ZT = (
    "Section: Zero Trust\n\nWe remove implicit trust and continuously verify every "
    "access request using identity, device, context and risk. Zero Trust reduces "
    "attack surface and limits lateral movement."
)
_TRUST = (
    "Section: Trust Center\n\nA Trust Center explains our approach to security, "
    "governance, AI usage, data handling and operational assurance for customers."
)
_CIS = (
    "Section: Scope\n\nThe CIS Microsoft 365 Foundations Benchmark has 160 "
    "recommendations across 8 areas including Identity and Microsoft Defender."
)


def _src(n: int, content: str, doc: str | None = None, **extra) -> SourceChunk:
    return SourceChunk(
        chunk_id=f"c{n}",
        document_id=doc or f"00000000-0000-0000-0000-0000000000{n:02d}",
        content=content,
        score=0.8,
        metadata={"source_name": f"doc{n}.md"},
        **extra,
    )


def test_correct_marker_is_kept() -> None:
    sources = [_src(1, _E8)]
    answer, citations = verify_citations(
        "DeployIQ combines AI acceleration with human oversight to deliver "
        "Essential Eight rapidly. [1]",
        sources,
    )
    assert answer.endswith("rapidly. [1]")
    assert [c.n for c in citations] == [1]


def test_marker_on_an_irrelevant_chunk_is_dropped() -> None:
    sources = [_src(1, _ABOUT)]
    answer, citations = verify_citations(
        "The platform scores every control against CIS benchmarks automatically. [1]",
        sources,
    )
    assert "[1]" not in answer
    assert answer.endswith("automatically.")
    assert citations == []


def test_mixed_run_keeps_only_verified_and_renumbers() -> None:
    sources = [_src(1, _ABOUT), _src(2, _E8), _src(3, _TRUST)]
    result = verify_and_select(
        "DeployIQ combines AI acceleration with human oversight to deliver "
        "Essential Eight rapidly. [1][2]\n"
        "A Trust Center explains our approach to security and governance. [3]",
        sources,
    )
    assert "[1][2]" not in result.answer
    assert result.answer.splitlines()[0].endswith("rapidly. [1]")
    assert result.answer.splitlines()[1].endswith("governance. [2]")
    assert [c.n for c in result.citations] == [1, 2]
    assert [s.chunk_id for s in result.sources] == ["c2", "c3"]


def test_renumbering_has_no_gaps_and_dedupes_by_document() -> None:
    same_doc = "00000000-0000-0000-0000-0000000000aa"
    sources = [
        _src(1, _ABOUT),
        _src(2, _E8, doc=same_doc),
        _src(3, _ZT),
        _src(4, _E8 + " Essential Eight is delivered in days.", doc=same_doc),
    ]
    result = verify_and_select(
        "We remove implicit trust and verify every access request using identity "
        "and device context. [3]\n"
        "DeployIQ combines AI acceleration with human oversight to deliver "
        "Essential Eight rapidly. [2][4]",
        sources,
    )
    lines = result.answer.splitlines()
    assert lines[0].endswith("context. [1]")
    assert lines[1].endswith("rapidly. [2]")
    assert [c.n for c in result.citations] == [1, 2]
    assert [s.chunk_id for s in result.sources] == ["c3", "c2"]
    assert {c.document_id for c in result.citations} == {
        s.document_id for s in result.sources
    }


def test_free_plan_claim_cited_to_about_us_is_dropped() -> None:
    sources = [_src(1, _ABOUT)]
    answer, citations = verify_citations(
        "We offer a free Zero Trust Starter plan. [1]", sources
    )
    assert "[" not in answer
    assert citations == []


def test_free_plan_claim_is_reattached_to_the_chunk_that_has_it() -> None:
    sources = [_src(1, _ABOUT), _src(2, _ZT_PLANS)]
    result = verify_and_select("We offer a free Zero Trust Starter plan. [1]", sources)
    assert result.answer == "We offer a free Zero Trust Starter plan. [1]"
    assert [s.chunk_id for s in result.sources] == ["c2"]


def test_zero_trust_claim_cited_to_the_essential_eight_chunk_is_dropped() -> None:
    sources = [_src(1, _E8), _src(2, _ZT)]
    result = verify_and_select(
        "We remove implicit trust and verify every access request, and nothing "
        "changes without a person's approval. [1]",
        sources,
    )
    # Not the E8 chunk: it is re-attached to the Zero Trust chunk or dropped.
    assert "c1" not in [s.chunk_id for s in result.sources]


def test_figures_must_occur_in_the_chunk() -> None:
    sources = [_src(1, _CIS)]
    ok, _ = verify_citations(
        "The CIS Microsoft 365 Foundations Benchmark has 160 recommendations. [1]",
        sources,
    )
    bad, citations = verify_citations(
        "The CIS Microsoft 365 Foundations Benchmark has 250 recommendations. [1]",
        sources,
    )
    assert ok.endswith("[1]")
    assert "[1]" not in bad
    assert citations == []


@pytest.mark.parametrize(
    "decline",
    [
        "We don't have details on PCI DSS or HIPAA certification here, talk to "
        "one of our specialists. [2]",
        "No prices or trials are published here. [1][4][6][7]",
        "Formal certifications are not yet claimed. [2]",
        "That detail isn\u2019t listed in what I can see. [1]",
    ],
)
def test_decline_sentences_lose_every_marker(decline: str) -> None:
    sources = [_src(i, _TRUST) for i in range(1, 8)]
    assert is_decline_sentence(decline)
    result = verify_and_select(decline, sources)
    assert "[" not in result.answer
    assert result.citations == [] and result.sources == []


def test_pci_decline_marker_is_dropped_even_when_the_chunk_mentions_pci() -> None:
    sources = [_src(1, "PCI DSS and HIPAA certification: we do not have details.")]
    result = verify_and_select(
        "We don't have details on PCI DSS or HIPAA certification here. [1]", sources
    )
    assert "[" not in result.answer and result.sources == []


def test_decline_only_after_a_supported_claim_keeps_just_the_claim_marker() -> None:
    sources = [_src(1, _E8), _src(2, _TRUST)]
    result = verify_and_select(
        "DeployIQ combines AI acceleration with human oversight to deliver "
        "Essential Eight rapidly. [1]\n"
        "We don't have details on pricing here; talk to a specialist. [2]",
        sources,
    )
    assert result.answer.splitlines()[0].endswith("[1]")
    assert "[2]" not in result.answer
    assert [s.chunk_id for s in result.sources] == ["c1"]


def test_handoff_tail_does_not_hide_a_supported_claim() -> None:
    sources = [_src(1, _E8)]
    answer, citations = verify_citations(
        "DeployIQ combines AI acceleration with human oversight to deliver "
        "Essential Eight rapidly; talk to one of our specialists. [1]",
        sources,
    )
    assert answer.endswith("[1]") and len(citations) == 1


def test_embedding_cosine_is_applied_only_when_supplied() -> None:
    claim = "DeployIQ combines AI acceleration with human oversight to deliver Essential Eight rapidly."
    sources = [_src(1, _E8, embedding=[1.0, 0.0])]
    text = f"{claim} [1]"
    assert verify_citations(text, sources)[1]
    assert verify_citations(text, sources, {claim: [1.0, 0.1]})[1]
    assert verify_citations(text, sources, {claim: [0.0, 1.0]})[1] == []


def test_no_markers_and_out_of_range_markers() -> None:
    assert verify_citations("Plain answer.", [_src(1, _E8)]) == ("Plain answer.", [])
    answer, citations = verify_citations(
        "Essential Eight is delivered in days. [9]", [_src(1, _E8)]
    )
    assert "[" not in answer and citations == []


def test_redos_time_budget_on_a_large_answer() -> None:
    sources = [_src(i, _E8 + " " * 50 + _ZT) for i in range(1, 11)]
    nasty = ("Essential Eight " + "[1]" * 5 + " " * 30 + "isn't " * 20 + "\n") * 120
    long_line = "word " * 3000 + "[1][2] " * 700
    for text in (nasty[:20000], long_line[:20000], "[" * 20000, ("a. " * 6600)[:20000]):
        start = time.perf_counter()
        verify_and_select(text, sources)
        assert time.perf_counter() - start < 2.0


# --- orchestrator: response sources == verified citations ------------------


async def _run(monkeypatch, llm_answer: str, sources: list[SourceChunk]):
    monkeypatch.setattr(orchestrator, "run_pre_checks", lambda *_a, **_k: [])

    async def fake_retrieve(*_a, **_k):
        return sources

    async def fake_call_llm(_query, _chunks, _history=None):
        return llm_answer, {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        }

    async def fake_post_checks(*_a, **_k):
        return True, 0.85, []

    monkeypatch.setattr(orchestrator, "retrieve", fake_retrieve)
    monkeypatch.setattr(orchestrator, "call_llm", fake_call_llm)
    monkeypatch.setattr(orchestrator, "run_post_checks", fake_post_checks)
    return await orchestrator.run_query(
        query="What does DeployIQ do about Essential Eight?",
        top_k=6,
        filters=None,
        vector_store=None,
        request_id="r",
        stage_timings={},
    )


async def test_chips_are_exactly_the_cited_documents(monkeypatch) -> None:
    sources = [_src(1, _ABOUT), _src(2, _E8), _src(3, _TRUST), _src(4, _ZT)]
    response = await _run(
        monkeypatch,
        "DeployIQ combines AI acceleration with human oversight to deliver "
        "Essential Eight rapidly. [2]\nWe offer a free Zero Trust Starter plan. [1]",
        sources,
    )
    assert response.outcome == "answered"
    assert [s.document_id for s in response.sources] == [
        c.document_id for c in response.citations
    ]
    assert [c.n for c in response.citations] == [1]
    assert response.sources[0].chunk_id == "c2"
    assert "[1]" in response.answer and "[2]" not in response.answer


async def test_partial_decline_without_verified_claims_has_no_sources(
    monkeypatch,
) -> None:
    sources = [_src(1, _ABOUT), _src(2, _TRUST)]
    response = await _run(
        monkeypatch,
        "The platform scores every control against CIS benchmarks automatically. [1]\n"
        "We don't have details on pricing here; talk to one of our specialists. [2]",
        sources,
    )
    assert response.citations == []
    assert response.sources == []
    assert "[" not in response.answer


async def test_marker_bearing_pure_decline_takes_the_refusal_branch(
    monkeypatch,
) -> None:
    sources = [_src(1, _TRUST)]
    response = await _run(
        monkeypatch,
        "We don't have details on that here. [1] Talk to one of our specialists.",
        sources,
    )
    assert response.token_details["declined"] is True
    assert response.sources == [] and response.citations == []
    assert "[" not in response.answer


def test_refusal_ignores_bare_markers_but_not_real_assertions() -> None:
    assert is_refusal(
        "We don't have details on that here. [1] Talk to a specialist. [2]"
    )
    assert not is_refusal("DeployIQ covers Essential Eight. [1] Talk to a specialist.")


def test_mixed_fact_and_absence_sentence_keeps_marker() -> None:
    answer, citations = verify_citations(
        "Pricing isn't published for the Zero Trust Starter plan, it's offered "
        "as Free [1].",
        [_src(1, _ZT_PLANS)],
    )
    assert "[1]" in answer
    assert len(citations) == 1
