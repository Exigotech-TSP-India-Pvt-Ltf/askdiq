"""Seeded, deterministic fuzz sets for the clarifier rule functions.

Four buckets (generated with a fixed seed, so CI replays the same questions):

A. CLEAR / specific questions: never asked back, never PARTIAL by accident.
B. AMBIGUOUS bare topics: decided by rules (``assess`` / ``clarify_eligible``),
   generic one-product questions are answered, never asked.
C. VAGUE goals: a framework / maturity series (SLOTS), never a dead end.
D. Multi-turn back-references with synthetic history.

Plus folded clarification replies ("question (Framework: X; Maturity: Y)").
Pure functions only: no LLM, DB or network.
"""

from __future__ import annotations

import hashlib
import random
import uuid

import pytest

from app.harness.ambiguity import AmbiguityKind, assess
from app.harness.answer_shape import AnswerShape, detect_shape, refine_shape
from app.harness.clarify import clarify_eligible
from app.schemas.query import ChatTurn, SourceChunk

SEED = 20260428
_MIN_SET = 250

# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------


def _variants(rng: random.Random, text: str) -> list[str]:
    """The text, upper-cased, lower-cased and with trailing punctuation."""
    return [text, text.lower(), text.upper(), f"{text}?", f"{text.lower()}?"][
        : rng.randint(2, 5)
    ]


_ENTITIES = (
    "Essential Eight",
    "Essential 8",
    "ISO 27001",
    "ISO27001",
    "CIS Benchmark",
    "Zero Trust",
    "Zero-Trust",
    "Security Baseline",
    "DeployIQ",
    "Deploy IQ",
    "Exigo Tech",
    "Exigo",
    "E8",
    "ACSC",
)
_TYPO_ENTITIES = (
    "Essentail Eight",
    "Essential Eigth",
    "Zero Trsut",
    "DeployQI",
    "Exigo Tehc",
    "Securty Baseline",
    "ISO 27011",
)
_COMPANY_FACTS = (
    "Where is {e} headquartered",
    "Where is {e} HQ",
    "Who is the CEO of {e}",
    "What is {e}'s phone number",
    "What is the {e} address",
    "When was {e} founded",
    "How do I contact {e}",
    "{e} contact details",
    "What is the email address for {e}",
    "Where are the {e} offices",
    "Who founded {e}",
    "Where is {e} located",
)
_COMPANY_ENTITIES = ("Exigo Tech", "Exigo", "DeployIQ", "exigo tech", "EXIGO TECH")
_COMPANY_FACT_TYPOS = (
    "wheer is exigo headquartred",
    "whos the ceo of exigo",
    "exigo phone numbr",
    "exigo adress",
    "when was exigo foundd",
    "contakt exigo",
    "who is the c.e.o. of exigo tech",
    "What is Exigo Tech's phone number?",
)
_SPECIFIC = (
    "What is {e}",
    "What is {e} and why does it matter",
    "How does {e} implement {y}",
    "How does DeployIQ implement {e} for {y}",
    "Tell me about {e} pricing",
    "{e} pricing",
    "How long does a {e} assessment take",
    "Does DeployIQ support {e}",
    "Explain {e} to me",
)
_TOPICS = (
    "application control",
    "multi-factor authentication",
    "patching",
    "privileged access",
    "backups",
    "conditional access",
)


def bucket_a() -> list[str]:
    rng = random.Random(SEED)  # noqa: S311
    out: list[str] = []
    for entity in _ENTITIES:
        for template in _SPECIFIC:
            question = template.format(e=entity, y=rng.choice(_TOPICS))
            out.append(rng.choice(_variants(rng, question)))
    for entity in _COMPANY_ENTITIES:
        for template in _COMPANY_FACTS:
            out.append(rng.choice(_variants(rng, template.format(e=entity))))
    out.extend(_COMPANY_FACT_TYPOS)
    for entity in _TYPO_ENTITIES:
        out.append(f"What is {entity}")
        out.append(f"how does deployiq implement {entity.lower()}")
    return list(dict.fromkeys(out))


_B_ASKED_TOPICS = (
    "pricing",
    "cost",
    "price",
    "prices",
    "fees",
    "plans",
    "packages",
    "support",
    "trial",
    "demo",
    "integrations",
    "compliance",
    "security",
    "how long",
    "how much",
    "onboarding",
    "partners",
    "certifications",
    "free trial",
    "quote",
    "timeline",
    "what are your prices",
    "how much does it cost",
    "how long does it take",
    "do you buy",
)
_B_ANSWERED_GENERIC = (
    "is it free",
    "is it safe",
    "is it secure",
    "how does it work",
    "what does it do",
    "does it work",
)


def _bare(rng: random.Random, topics: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for topic in topics:
        for text in [*_variants(rng, topic), f"{topic} ?", f"  {topic}  "]:
            out.append(text)
    return out


def bucket_b_asked() -> list[str]:
    return list(dict.fromkeys(_bare(random.Random(SEED + 1), _B_ASKED_TOPICS)))  # noqa: S311


def bucket_b_generic() -> list[str]:
    return list(dict.fromkeys(_bare(random.Random(SEED + 2), _B_ANSWERED_GENERIC)))  # noqa: S311


_C_GOALS = (
    "i need compliance",
    "we need compliance",
    "help us get ready for our audit",
    "we have an audit coming up",
    "we got hacked",
    "we were breached",
    "how do I secure my company",
    "how can we protect our business",
    "we want ISO certification",
    "we want to get certified",
    "we need to improve our security posture, where do we start",
    "we need to get complience fast, what should we do?",
    "we need to be complaint, where do we start",
    "we have an audti coming up",
    "i need complaince",
    "we want to be compliant",
    "how do we get compliant",
    "help me prepare for the audit",
    "we want to achieve certification",
    "how do I harden our environment",
)
_C_PREFIX = ("", "hi, ", "Hello! ", "ok so ", "please ", "Hey team, ")
_C_SUFFIX = ("", "?", ".", " asap", "!")


def bucket_c() -> list[str]:
    rng = random.Random(SEED + 3)  # noqa: S311
    out = [
        f"{rng.choice(_C_PREFIX)}{g}{rng.choice(_C_SUFFIX)}"
        for g in _C_GOALS
        for _ in range(3)
    ]
    out.extend(_C_GOALS)
    return list(dict.fromkeys(out))


_PRODUCTS = (
    "Essential Eight",
    "ISO 27001",
    "CIS Benchmark",
    "Zero Trust",
    "Security Baseline",
)
_D_QUERIES = (
    "the one you mentioned above",
    "Tell me more about the one you mentioned above",
    "that one",
    "tell me more about that one",
    "the previous one",
    "What about the previous one?",
    "tell me more",
    "Tell me more.",
    "the one above",
    "and the one above?",
    "that product",
    "how much does that product cost",
    "the last one",
)


def bucket_d() -> list[tuple[str, list[ChatTurn], int]]:
    """(query, history, products named by the last assistant answer)."""
    rng = random.Random(SEED + 4)  # noqa: S311
    cases: list[tuple[str, list[ChatTurn], int]] = []
    for query in _D_QUERIES:
        for count in (0, 1, 2):
            for _ in range(4):
                named = rng.sample(_PRODUCTS, count)
                answer = (
                    "Here is an overview of " + " and ".join(named) + "."
                    if named
                    else "We offer managed security services for Microsoft tenants."
                )
                user = rng.choice(
                    ("what do you offer", "tell me about your services", "hi, help?")
                )
                history = [
                    ChatTurn(role="user", content=user),
                    ChatTurn(role="assistant", content=answer, outcome="answered"),
                ]
                cases.append((query, history, count))
    return cases


_FRAMEWORKS = ("ISO 27001", "Essential Eight", "CIS Benchmark", "Zero Trust")
_MATURITY = ("Not assessed yet", "Partly implemented", "Mostly in place")
_FOLD_ORIGINALS = (
    "I need compliance",
    "help us get ready for our audit",
    "we want to get certified",
)


def folded_replies() -> list[tuple[str, list[str]]]:
    """Folded replies and the frameworks named in them (12 combinations)."""
    out: list[tuple[str, list[str]]] = []
    for original, fw in zip(
        _FOLD_ORIGINALS * 4,
        [(f, m) for f in _FRAMEWORKS for m in _MATURITY],
        strict=False,
    ):
        framework, maturity = fw
        out.append(
            (f"{original} (Framework: {framework}; Maturity: {maturity})", [framework])
        )
    return out


def _sources(*names: str) -> list[SourceChunk]:
    return [
        SourceChunk(
            chunk_id=str(uuid.uuid4()),
            document_id=f"doc-{i}",
            content=f"{name} guide. Maturity and current state assessment.",
            score=0.6,
            metadata={"source_name": f"{name.replace(' ', '_')}.md"},
        )
        for i, name in enumerate(names)
    ]


# ---------------------------------------------------------------------------
# Fixtures guard: the generated sets never drift silently
# ---------------------------------------------------------------------------


def _digest(items: list[str]) -> str:
    return hashlib.sha1("\n".join(items).encode()).hexdigest()[:10]  # noqa: S324


def test_sets_are_deterministic_and_large() -> None:
    assert bucket_a() == bucket_a()
    assert len(bucket_a()) >= _MIN_SET // 2
    total = (
        len(bucket_a())
        + len(bucket_b_asked())
        + len(bucket_b_generic())
        + len(bucket_c())
        + len(bucket_d())
        + len(folded_replies())
    )
    assert total >= _MIN_SET


# ---------------------------------------------------------------------------
# Bucket A: never asked back
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", bucket_a())
def test_a_clear_question_is_never_asked_back(query: str) -> None:
    assert assess(query, None).kind is AmbiguityKind.NONE, query
    assert not clarify_eligible(query, None, follow_up=False), query
    assert detect_shape(query) is not AnswerShape.PARTIAL


# ---------------------------------------------------------------------------
# Bucket B: bare topics are decided by the rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", bucket_b_asked())
def test_b_bare_topic_is_asked_by_rules(query: str) -> None:
    ruled = assess(query, None).detected
    gated = clarify_eligible(query, None, follow_up=False)
    assert ruled or gated, query
    assert detect_shape(query) is not AnswerShape.PARTIAL


@pytest.mark.parametrize("query", bucket_b_generic())
def test_b_generic_product_question_is_answered(query: str) -> None:
    assert assess(query, None).kind is AmbiguityKind.NONE, query
    assert not clarify_eligible(query, None, follow_up=False), query


def test_b_bare_cost_and_time_never_depend_on_the_llm_gate() -> None:
    """Cost / time / plan topics are detected by ``assess`` itself."""
    for topic in (
        "pricing",
        "cost",
        "price",
        "fees",
        "plans",
        "how long",
        "how much",
        "quote",
        "timeline",
        "free trial",
    ):
        for text in (topic, topic.upper(), f"{topic}?"):
            assert assess(text, None).kind is AmbiguityKind.REFERENT, text


# ---------------------------------------------------------------------------
# Bucket C: vague goals get a framework / maturity series
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", bucket_c())
def test_c_vague_goal_gets_a_series_not_a_dead_end(query: str) -> None:
    result = assess(query, None)
    if result.kind is AmbiguityKind.SLOTS:
        assert result.missing_slots[0] == "framework"
    else:
        # A named framework ("we want ISO certification") or an incident
        # ("we got hacked") is answered directly, not asked back.
        assert result.kind is AmbiguityKind.NONE, query
        assert not clarify_eligible(query, None, follow_up=False), query
    assert detect_shape(query, result) is not AnswerShape.PARTIAL


# ---------------------------------------------------------------------------
# Bucket D: back-references resolve (one product) or ask (none / several)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("query", "history", "count"), bucket_d())
def test_d_back_reference_resolves_or_asks(
    query: str, history: list[ChatTurn], count: int
) -> None:
    kind = assess(query, history).kind
    if count == 1:
        assert kind is AmbiguityKind.NONE, (query, history[-1].content)
    else:
        assert kind is not AmbiguityKind.NONE, (query, history[-1].content)


# ---------------------------------------------------------------------------
# Folded clarification replies are never PARTIAL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("query", "frameworks"), folded_replies())
def test_folded_reply_is_never_partial(query: str, frameworks: list[str]) -> None:
    sources = _sources(*frameworks)
    assert refine_shape(AnswerShape.GENERAL, query, sources) is not AnswerShape.PARTIAL
    assert (
        refine_shape(AnswerShape.HOW_TO, query, _sources(*_FRAMEWORKS))
        is not AnswerShape.PARTIAL
    )


# ---------------------------------------------------------------------------
# Purchase-trust regex: matching is linear (no ReDoS) and stays specific
# ---------------------------------------------------------------------------


def test_purchase_trust_regex_is_linear_on_adversarial_input() -> None:
    import time  # noqa: PLC0415

    from app.harness.clarify import PURCHASE_TRUST_RE  # noqa: PLC0415

    nasty = [
        "is " + "that " * 5000 + "safe",
        "is that product safe to " + "purchase " * 5000,
        "is it worth it" + "!" * 5000,
        "is " + " " * 5000 + "safe",
    ]
    start = time.perf_counter()
    for text in nasty:
        PURCHASE_TRUST_RE.match(text)
    assert time.perf_counter() - start < 1.0


@pytest.mark.parametrize(
    "query",
    ["is E8 safe to use?", "is Zero Trust worth it", "is ISO 27001 safe to buy"],
)
def test_purchase_trust_regex_ignores_named_subjects(query: str) -> None:
    from app.harness.clarify import PURCHASE_TRUST_RE  # noqa: PLC0415

    assert PURCHASE_TRUST_RE.match(query) is None
