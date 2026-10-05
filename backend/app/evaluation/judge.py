"""LLM-as-judge scoring for answer quality (opt-in via ``--judge``).

The pipeline's own groundedness check is an embedding-cosine threshold, which
is a weak signal (it can pass a fluent answer that adds a wrong detail, and
fail a correct paraphrase). This module asks an LLM to grade each answer
against the *retrieved context* on three 1-5 axes:

- faithfulness  : every claim in the answer is supported by the context
- relevance     : the answer addresses what was asked
- completeness  : it covers the parts of the question the context supports

Notes
-----
* By default the judge uses the same Azure deployment as the generator, so it
  can be biased toward its own phrasing. To use a different (ideally
  stronger) model, add ``azure_judge_deployment_name`` to Settings - it is
  picked up automatically via ``getattr`` below; no other change needed.
* Each judged case costs one extra LLM call, so this is off by default.
* A judge failure returns ``None`` and never fails a case on its own.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache

from openai import AzureOpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_MAX_CHUNKS = 10
_MAX_CHUNK_CHARS = 2000

JUDGE_SYSTEM_PROMPT = (
    "You are a strict, impartial evaluator of a retrieval-augmented "
    "assistant. You are given a QUESTION, the CONTEXT passages the assistant "
    "was allowed to use, and the assistant's ANSWER. Grade only against the "
    "CONTEXT - never use outside knowledge to decide whether a claim is "
    "true.\n\n"
    "Score each axis as an integer from 1 to 5:\n"
    "- faithfulness: 5 = every claim in the answer is directly supported by "
    "the context; 3 = mostly supported but contains a minor unsupported "
    "detail or overstatement; 1 = contains fabricated or contradicted "
    "claims.\n"
    "- relevance: 5 = directly answers the question asked; 3 = partly "
    "on-topic; 1 = does not address the question.\n"
    "- completeness: 5 = covers every part of the question that the context "
    "can support (and every KEY FACT listed, if any); 3 = misses some "
    "supportable parts; 1 = misses most of them. Do NOT penalise the answer "
    "for omitting something the context does not contain.\n\n"
    "List any unsupported claims verbatim in unsupported_claims (empty list "
    "if none). Respond with ONLY a JSON object, no prose, in this exact "
    'shape: {"faithfulness": int, "relevance": int, "completeness": int, '
    '"unsupported_claims": [str], "reason": str}'
)


@dataclass
class JudgeScores:
    faithfulness: int
    relevance: int
    completeness: int
    unsupported_claims: list[str] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "faithfulness": self.faithfulness,
            "relevance": self.relevance,
            "completeness": self.completeness,
            "unsupported_claims": self.unsupported_claims,
            "reason": self.reason,
        }


def build_judge_prompt(
    question: str,
    context_chunks: list[str],
    answer: str,
    key_facts: list[str] | None = None,
) -> str:
    context = "\n\n".join(
        f"[{i}] {chunk[:_MAX_CHUNK_CHARS]}"
        for i, chunk in enumerate(context_chunks[:_MAX_CHUNKS], start=1)
    )
    facts_block = ""
    if key_facts:
        facts_block = "KEY FACTS the answer should mention:\n" + "\n".join(
            f"- {fact}" for fact in key_facts
        )
        facts_block += "\n\n"

    return (
        f"QUESTION:\n{question}\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"{facts_block}"
        f"ANSWER:\n{answer}\n"
    )


def _extract_json(text: str) -> dict | None:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _clamp_score(value) -> int:
    return max(1, min(5, int(round(float(value)))))


def parse_judge_output(text: str) -> JudgeScores | None:
    """Parse the judge's reply; None if it is not usable."""

    data = _extract_json(text)
    if data is None:
        return None
    try:
        claims = data.get("unsupported_claims") or []
        return JudgeScores(
            faithfulness=_clamp_score(data["faithfulness"]),
            relevance=_clamp_score(data["relevance"]),
            completeness=_clamp_score(data["completeness"]),
            unsupported_claims=[str(c) for c in claims],
            reason=str(data.get("reason", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


@lru_cache(maxsize=4)
def _client_for(deployment: str) -> AzureOpenAI:
    settings = get_settings()
    return AzureOpenAI(
        api_key=settings.azure_openai_api_key,
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
        azure_deployment=deployment,
        timeout=settings.azure_openai_request_timeout,
    )


def _judge_sync(
    question: str,
    context_chunks: list[str],
    answer: str,
    key_facts: list[str] | None,
) -> JudgeScores | None:
    settings = get_settings()
    deployment = (
        getattr(settings, "azure_judge_deployment_name", None)
        or settings.azure_deployment_name
    )
    client = _client_for(deployment)

    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": build_judge_prompt(question, context_chunks, answer, key_facts),
        },
    ]
    common = dict(
        model=deployment,
        messages=messages,
        temperature=settings.azure_openai_temperature,
        max_completion_tokens=settings.llm_max_completion_tokens,
        reasoning_effort=settings.llm_reasoning_effort,
    )

    # Prefer JSON mode; fall back to a plain call if the deployment rejects it.
    for use_json_mode in (True, False):
        try:
            kwargs = dict(common)
            if use_json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - evaluation must not crash
            logger.warning(
                "judge_call_failed json_mode=%s error_type=%s error=%s",
                use_json_mode,
                type(exc).__name__,
                exc,
            )
            continue

        content = response.choices[0].message.content if response.choices else ""
        scores = parse_judge_output(content or "")
        if scores is not None:
            return scores
        logger.warning("judge_unparseable_output json_mode=%s", use_json_mode)

    return None


async def judge_answer(
    question: str,
    context_chunks: list[str],
    answer: str,
    key_facts: list[str] | None = None,
) -> JudgeScores | None:
    """Grade ``answer`` against ``context_chunks``. Never raises."""

    try:
        return await asyncio.to_thread(
            _judge_sync, question, context_chunks, answer, key_facts
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("judge_failed error_type=%s error=%s", type(exc).__name__, exc)
        return None