"""Lexical containment: short exact facts are grounded even when cosine is low."""

import time

import pytest

from app.guardrails import post_checks
from app.guardrails.post_checks import check_groundedness, lexical_support
from app.schemas.query import SourceChunk

ABOUT_US = (
    "# About Us\n\nSecurity delivery often slows down under manual effort and "
    "scattered tools. DeployIQ was created to bring structure, speed, and "
    "governance to this work.\n\nDeployIQ is powered by Exigo Tech, a global IT "
    "solutions provider headquartered in Australia with more than a decade of "
    "experience supporting organisations as they modernise, secure, and scale."
)
CIS = (
    "# CIS Microsoft 365 Foundations Benchmark Assessment\n\nConfigure Microsoft "
    "365 to the industry's most widely adopted hardening standard.\n\n"
    "- CIS Microsoft 365 Foundations Benchmark v7.0.0\n- 160 recommendations\n"
    "- Covers 8 areas"
)
PLATFORM = (
    "## Who DeployIQ Platform Is For\n\nDeployIQ is designed for organisations "
    "that need structured, predictable, and accountable security uplift, "
    "including:\n\n- Small and mid-sized businesses strengthening foundational "
    "security.\n- Startups building secure, scalable environments."
)


def _chunk(content: str, score: float = 0.8) -> SourceChunk:
    return SourceChunk(
        chunk_id="00000000-0000-0000-0000-000000000001",
        document_id="00000000-0000-0000-0000-000000000002",
        content=content,
        score=score,
        metadata={},
    )


@pytest.fixture
def low_cosine(monkeypatch):
    """Embeddings where every answer/claim is orthogonal to every source."""

    async def _embed(texts):
        return [[0.0, 1.0] if len(t) > 150 else [1.0, 0.0] for t in texts]

    monkeypatch.setattr(post_checks, "embed_texts", _embed)


@pytest.mark.parametrize(
    ("answer", "source"),
    [
        ("Exigo Tech is headquartered in Australia.", ABOUT_US),
        ("Exigo Tech's headquarters are in Australia.", ABOUT_US),
        ("DeployIQ is powered by Exigo Tech.", ABOUT_US),
        ("The assessment checks 160 recommendations.", CIS),
        (
            "DeployIQ is designed for organisations that need structured, accountable security uplift.",
            PLATFORM,
        ),
        ("DeployIQ is for startups building secure, scalable environments.", PLATFORM),
    ],
)
async def test_short_exact_fact_grounded_despite_low_cosine(low_cosine, answer, source):
    grounded, confidence, flag = await check_groundedness(
        answer, [_chunk(source)], 0.45
    )
    assert (grounded, flag) == (True, None)
    assert confidence >= 0.7


async def test_union_of_top_two_chunks_supports_claim(low_cosine):
    sources = [
        _chunk("Exigo Tech is a global IT provider.", 0.9),
        _chunk("It is headquartered in Australia.", 0.8),
    ]
    grounded, _c, _f = await check_groundedness(
        "Exigo Tech is headquartered in Australia.", sources, 0.45
    )
    assert grounded is True


@pytest.mark.parametrize(
    ("answer", "source"),
    [
        ("The assessment checks 200 recommendations.", CIS),
        ("Exigo Tech is headquartered in Sydney.", ABOUT_US),
        ("DeployIQ is powered by Contoso.", ABOUT_US),
        ("Exigo Tech is not headquartered in Australia.", ABOUT_US),
        ("Security delivery often needs people and money together.", CIS),
        ("There are many of the things in this and that.", ABOUT_US),
    ],
)
async def test_unsupported_claim_still_fails(low_cosine, answer, source):
    grounded, _c, flag = await check_groundedness(answer, [_chunk(source)], 0.45)
    assert grounded is False
    assert flag is not None


@pytest.mark.parametrize(
    "answer",
    [
        "DeployIQ has 99.9% uptime.",
        "Exigo Tech is headquartered in Australia and is guaranteed to be safe to purchase.",
        "Exigo Tech is headquartered in Australia with 24/7 support.",
        "Exigo Tech is headquartered in Australia and the assessment is 100% accurate.",
        "Exigo Tech is headquartered in Australia, certified and delivers ROI.",
        "Exigo Tech is headquartered in Australia with an SLA.",
    ],
)
async def test_assurances_and_invented_figures_still_fail(low_cosine, answer):
    grounded, _c, flag = await check_groundedness(answer, [_chunk(ABOUT_US)], 0.45)
    assert grounded is False
    assert flag in {
        "unsupported_claim",
        "unsupported_numeric_claim",
        "possible_hallucination",
    }


def test_lexical_support_requires_every_figure_and_entity():
    scores = lexical_support(
        [
            "The assessment checks 160 recommendations.",
            "The assessment checks 161 recommendations.",
        ],
        [_chunk(CIS)],
    )
    assert scores[0] > 0
    assert scores[1] == 0.0


def test_lexical_threshold_is_configurable():
    claim = ["Exigo Tech is headquartered in Australia with offices worldwide."]
    assert lexical_support(claim, [_chunk(ABOUT_US)], 0.99) == [0.0]
    assert lexical_support(claim, [_chunk(ABOUT_US)], 0.5)[0] > 0


def test_lexical_path_is_linear_on_large_answer():
    answer = ("Exigo Tech is headquartered in Australia and " + "a" * 40 + " ") * 400
    answer = answer[:20000]
    started = time.perf_counter()
    lexical_support(post_checks.split_claims(answer), [_chunk(ABOUT_US * 20)])
    assert time.perf_counter() - started < 2.0
