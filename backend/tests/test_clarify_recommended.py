"""OR-9: one clarification option per question may be tagged ``recommended``.

The tag is rule-based from the retrieval scores already computed (no LLM
call, so document text cannot steer it), set only when the top option leads
the runner-up by ``clarify_recommend_margin``, never more than one per
question, and the option order is kept (the 1-4 shortcuts map to positions).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.sessions import stored_clarification
from app.harness import ambiguity
from app.harness.ambiguity import (
    CatalogueEntry,
    service_options,
    slot_clarification,
)
from app.harness.clarify import (
    parse_decision,
    recommend_option,
    render_clarification_text,
    resolve_clarification_reply,
    select_candidates,
)
from app.schemas.query import (
    ChatTurn,
    Clarification,
    ClarificationOption,
    ClarificationQuestion,
    SourceChunk,
)

settings = get_settings()


def _src(doc: str, score: float, name: str, content: str = "") -> SourceChunk:
    return SourceChunk(
        chunk_id=str(uuid.uuid4()),
        document_id=doc,
        content=content or f"{name} content about the offering",
        score=score,
        metadata={"source_name": name},
        embedding=[0.05] * 8,
    )


def _opts(n: int) -> list[ClarificationOption]:
    return [
        ClarificationOption(id=f"O{i}", label=f"Option {i}") for i in range(1, n + 1)
    ]


def _flags(options: list[ClarificationOption]) -> list[bool]:
    return [o.recommended for o in options]


# ---------------------------------------------------------------------------
# The margin rule
# ---------------------------------------------------------------------------


def test_clear_leader_is_the_only_recommendation() -> None:
    out = recommend_option(_opts(3), [0.40, 0.65, 0.30])
    assert _flags(out) == [False, True, False]
    # Never reordered: shortcuts keep their positions.
    assert [o.id for o in out] == ["O1", "O2", "O3"]


def test_lead_exactly_the_margin_counts() -> None:
    assert _flags(recommend_option(_opts(2), [0.65, 0.60])) == [True, False]


@pytest.mark.parametrize(
    "scores",
    [[0.60, 0.60], [0.62, 0.60, 0.59], [0.31, 0.30, 0.29, 0.29]],
)
def test_close_or_tied_scores_recommend_nothing(scores: list[float]) -> None:
    assert not any(_flags(recommend_option(_opts(len(scores)), scores)))


def test_flag_off_and_bad_input_recommend_nothing(monkeypatch) -> None:
    assert not any(_flags(recommend_option(_opts(2), [0.9])))
    assert not any(_flags(recommend_option(_opts(1), [0.9])))
    monkeypatch.setattr(settings, "clarify_recommend_enabled", False)
    assert not any(_flags(recommend_option(_opts(2), [0.9, 0.1])))


def test_margin_is_configurable(monkeypatch) -> None:
    monkeypatch.setattr(settings, "clarify_recommend_margin", 0.3)
    assert not any(_flags(recommend_option(_opts(2), [0.7, 0.5])))


def test_single_default_breaks_a_tie_but_two_do_not() -> None:
    scores = [0.60, 0.60, 0.40]
    assert _flags(recommend_option(_opts(3), scores, [False, True, False])) == [
        False,
        True,
        False,
    ]
    assert not any(_flags(recommend_option(_opts(3), scores, [True, True, False])))
    # A default far behind the leaders does not win the tie.
    assert not any(_flags(recommend_option(_opts(3), scores, [False, False, True])))


# ---------------------------------------------------------------------------
# Schema: at most one, additive, round-trips
# ---------------------------------------------------------------------------


def test_validator_rejects_two_recommended() -> None:
    options = [o.model_copy(update={"recommended": True}) for o in _opts(2)]
    with pytest.raises(ValidationError, match="at most one"):
        ClarificationQuestion.model_validate(
            {
                "id": "Q1",
                "header": "Topic",
                "question": "Which?",
                "options": [o.model_dump() for o in options],
            }
        )


def test_old_rows_default_to_false_and_new_rows_round_trip() -> None:
    old = stored_clarification(
        {
            "clarification": {
                "original_query": "pricing",
                "questions": [
                    {
                        "id": "Q1",
                        "header": "Topic",
                        "question": "Which?",
                        "options": [
                            {"id": "O1", "label": "A"},
                            {"id": "O2", "label": "B"},
                        ],
                    }
                ],
            }
        }
    )
    assert old is not None
    assert not any(_flags(old.questions[0].options))

    question = ClarificationQuestion(
        id="Q1",
        header="Topic",
        question="Which?",
        options=recommend_option(_opts(2), [0.9, 0.2]),
    )
    clar = Clarification(questions=[question], original_query="pricing")
    loaded = stored_clarification({"clarification": clar.model_dump()})
    assert loaded is not None
    assert _flags(loaded.questions[0].options) == [True, False]


def test_openapi_documents_the_field() -> None:
    field = ClarificationOption.model_json_schema()["properties"]["recommended"]
    assert field["default"] is False
    assert "Recommended" in field["description"]


# ---------------------------------------------------------------------------
# LLM path: grounded topics' scores, never the model's say
# ---------------------------------------------------------------------------


def _llm_clarification(scores: tuple[float, float, float]) -> Clarification:
    sources = [
        _src("doc-svc", scores[0], "services.md"),
        _src("doc-price", scores[1], "pricing.md"),
        _src("doc-partner", scores[2], "partner-program.md"),
    ]
    payload: dict[str, Any] = {
        "action": "clarify",
        "answer": "",
        "preamble": "",
        "questions": [
            {
                "header": "Topic",
                "question": "Which topic?",
                "multi_select": False,
                "options": [
                    # The model cannot set the tag: unknown keys are ignored.
                    {
                        "label": "Services",
                        "description": "",
                        "source_ids": ["T1"],
                        "recommended": False,
                    },
                    {
                        "label": "Pricing",
                        "description": "",
                        "source_ids": ["T2"],
                        "recommended": True,
                    },
                    {"label": "Partners", "description": "", "source_ids": ["T3"]},
                ],
            },
            {
                "header": "Region",
                "question": "Which regions?",
                "multi_select": True,
                "options": [
                    {"label": "Australia", "description": "", "source_ids": ["T1"]},
                    {"label": "New Zealand", "description": "", "source_ids": ["T3"]},
                ],
            },
        ],
    }
    candidates = select_candidates(sources)
    decision = parse_decision(json.dumps(payload), candidates, "buy?")
    assert decision.clarification is not None
    return decision.clarification


def test_llm_options_follow_the_retrieval_scores(monkeypatch) -> None:
    monkeypatch.setattr(settings, "clarify_score_margin", 0.5)
    topic, region = _llm_clarification((0.70, 0.50, 0.45)).questions
    assert _flags(topic.options) == [True, False, False]
    # Multi-select questions get at most one too.
    assert region.multi_select is True
    assert _flags(region.options) == [True, False]


def test_llm_close_options_have_no_tag() -> None:
    clar = _llm_clarification((0.62, 0.60, 0.59))
    assert all(not any(_flags(q.options)) for q in clar.questions)


def test_text_fallback_marks_the_recommendation_and_replies_still_match(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "clarify_score_margin", 0.5)
    clar = _llm_clarification((0.70, 0.50, 0.45))
    text = render_clarification_text(clar)
    assert "- Services (recommended)" in text
    assert "Pricing (recommended)" not in text
    history = [
        ChatTurn(role="user", content="buy?"),
        ChatTurn(role="assistant", content=text, outcome="clarify", clarification=clar),
    ]
    reply = resolve_clarification_reply("Services (recommended)", history)
    assert reply is not None
    assert reply.answers[0]["labels"] == ["Services"]


# ---------------------------------------------------------------------------
# Rule-based path (ambiguity.py)
# ---------------------------------------------------------------------------


def _service_sources(e8: float, cis: float, iso: float) -> list[SourceChunk]:
    return [
        _src("doc-e8", e8, "e8-service-page.md"),
        _src("doc-cis", cis, "cis-benchmark-assessment.md"),
        _src("doc-iso", iso, "iso-27001-technical-controls.md"),
    ]


def test_service_options_recommend_a_clear_leader() -> None:
    options = service_options(_service_sources(0.55, 0.31, 0.30))
    assert [o.label for o in options] == [
        "Essential Eight",
        "CIS Benchmark",
        "ISO 27001",
    ]
    assert _flags(options) == [True, False, False]


def test_service_options_close_scores_have_no_tag() -> None:
    assert not any(_flags(service_options(_service_sources(0.31, 0.30, 0.29))))


def test_catalogue_default_breaks_a_service_tie(monkeypatch) -> None:
    catalogue = tuple(
        CatalogueEntry(e.label, e.pattern, e.description, e.framework, default=True)
        if e.label == "ISO 27001"
        else e
        for e in ambiguity.CATALOGUE
    )
    monkeypatch.setattr(ambiguity, "CATALOGUE", catalogue)
    options = service_options(_service_sources(0.31, 0.30, 0.29))
    assert [o.label for o in options][2] == "ISO 27001"
    assert _flags(options) == [False, False, True]


def test_slot_questions_get_at_most_one_each() -> None:
    sources = [
        *_service_sources(0.60, 0.31, 0.30),
        _src(
            "doc-env",
            0.29,
            "environments.md",
            "Microsoft 365, Entra ID and Intune, hybrid or on-premises environments.",
        ),
    ]
    clar = slot_clarification("audit?", ("framework", "environment"), sources)
    assert clar is not None
    assert _flags(clar.questions[0].options)[0] is True
    for question in clar.questions:
        assert sum(_flags(question.options)) <= 1
