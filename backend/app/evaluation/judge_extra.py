"""Extended LLM-as-judge (opt-in via ``--judge``): claim-level groundedness,
answer-correctness sub-scores, helpfulness/clarity and contradictions.

One call per case. ``groundedness`` is computed in code as
supported claims / total verifiable claims. Never raises; None on failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.evaluation.judge import (
    _MAX_CHUNK_CHARS,
    _MAX_CHUNKS,
    _clamp_score,
    _client_for,
    _extract_json,
)

logger = logging.getLogger(__name__)

CORRECTNESS_AXES = (
    "factual_accuracy",
    "numerical_accuracy",
    "dates_and_names",
    "interpretation_of_conditions",
    "correct_conclusions",
    "absence_of_contradictions",
)
HELPFULNESS_AXES = (
    "conciseness",
    "readability",
    "appropriate_detail",
    "clear_structure",
    "actionability",
    "professional_tone",
    "no_repetition",
)

DEEP_JUDGE_PROMPT = (
    "You are a strict, impartial evaluator of a retrieval-augmented assistant. "
    "You get a QUESTION, numbered CONTEXT passages, optional EXPECTED FACTS and "
    "the ANSWER. Judge only against the CONTEXT.\n"
    "1. Split the ANSWER into atomic verifiable claims. For each give "
    '"supported" (true only if a context passage supports it) and '
    '"needs_citation" (true if it states a fact that should be cited).\n'
    "2. Score correctness axes 1-5, or null if the axis does not apply: "
    + ", ".join(CORRECTNESS_AXES)
    + ".\n3. Score helpfulness axes 1-5: "
    + ", ".join(HELPFULNESS_AXES)
    + ".\n4. List any statements in the answer that contradict each other or "
    "the context in contradictions.\n"
    "Respond with ONLY JSON: "
    '{"claims":[{"claim":str,"supported":bool,"needs_citation":bool}],'
    '"correctness":{axis:int|null},"helpfulness":{axis:int},'
    '"contradictions":[str],"reason":str}'
)


@dataclass
class DeepJudgeScores:
    claims: list[dict] = field(default_factory=list)
    correctness: dict[str, int | None] = field(default_factory=dict)
    helpfulness: dict[str, int] = field(default_factory=dict)
    contradictions: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def groundedness(self) -> float | None:
        """Supported answer claims / total verifiable answer claims."""

        if not self.claims:
            return None
        return sum(1 for c in self.claims if c.get("supported")) / len(self.claims)

    @property
    def claim_citation_need(self) -> int:
        return sum(1 for c in self.claims if c.get("needs_citation"))

    def mean_axis(self, group: dict) -> float | None:
        vals = [v for v in group.values() if v is not None]
        return sum(vals) / len(vals) if vals else None

    def as_dict(self) -> dict:
        return {
            "groundedness": self.groundedness,
            "claims": self.claims,
            "correctness": self.correctness,
            "correctness_mean": self.mean_axis(self.correctness),
            "helpfulness": self.helpfulness,
            "helpfulness_mean": self.mean_axis(self.helpfulness),
            "contradictions": self.contradictions,
            "reason": self.reason,
        }


def build_deep_prompt(
    question: str, chunks: list[str], answer: str, key_facts: list[str] | None
) -> str:
    context = "\n\n".join(
        f"[{i}] {c[:_MAX_CHUNK_CHARS]}" for i, c in enumerate(chunks[:_MAX_CHUNKS], 1)
    )
    facts = ""
    if key_facts:
        facts = "EXPECTED FACTS:\n" + "\n".join(f"- {f}" for f in key_facts) + "\n\n"
    return f"QUESTION:\n{question}\n\nCONTEXT:\n{context}\n\n{facts}ANSWER:\n{answer}\n"


def parse_deep_output(text: str) -> DeepJudgeScores | None:
    data = _extract_json(text)
    if data is None:
        return None
    try:
        claims = [
            {
                "claim": str(c.get("claim", "")),
                "supported": bool(c.get("supported")),
                "needs_citation": bool(c.get("needs_citation", True)),
            }
            for c in (data.get("claims") or [])
            if isinstance(c, dict)
        ]
        corr = {
            k: (
                None
                if (data.get("correctness") or {}).get(k) is None
                else _clamp_score(data["correctness"][k])
            )
            for k in CORRECTNESS_AXES
        }
        help_ = {
            k: _clamp_score(data["helpfulness"][k])
            for k in HELPFULNESS_AXES
            if (data.get("helpfulness") or {}).get(k) is not None
        }
        return DeepJudgeScores(
            claims=claims,
            correctness=corr,
            helpfulness=help_,
            contradictions=[str(c) for c in (data.get("contradictions") or [])],
            reason=str(data.get("reason", "")),
        )
    except (TypeError, ValueError):
        return None


async def _deep(question, chunks, answer, key_facts) -> DeepJudgeScores | None:
    settings = get_settings()
    deployment = (
        getattr(settings, "azure_judge_deployment_name", None)
        or settings.azure_deployment_name
    )
    client = _client_for(deployment)
    common = {
        "model": deployment,
        "messages": [
            {"role": "system", "content": DEEP_JUDGE_PROMPT},
            {
                "role": "user",
                "content": build_deep_prompt(question, chunks, answer, key_facts),
            },
        ],
        "reasoning_effort": settings.llm_reasoning_effort,
    }
    for json_mode in (True, False):
        try:
            kwargs = dict(common)
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = await client.chat.completions.create(**kwargs)
        except Exception as exc:
            logger.warning(
                "deep_judge_call_failed json_mode=%s error=%s", json_mode, exc
            )
            continue
        scores = parse_deep_output(
            (resp.choices[0].message.content if resp.choices else "") or ""
        )
        if scores is not None:
            return scores
    return None


async def deep_judge(
    question, chunks, answer, key_facts=None
) -> DeepJudgeScores | None:
    try:
        return await _deep(question, chunks, answer, key_facts)
    except Exception as exc:
        logger.warning("deep_judge_failed error=%s", exc)
        return None
