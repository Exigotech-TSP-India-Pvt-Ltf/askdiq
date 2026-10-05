"""Azure OpenAI generation wrapper used by the RAG harness."""

import logging

from openai import AzureOpenAI

from app.core.config import get_settings
from app.retrieval.retriever import needs_conversation_context
from app.schemas.query import ChatTurn

logger = logging.getLogger(__name__)

settings = get_settings()

_client = AzureOpenAI(
    api_key=settings.azure_openai_api_key,
    azure_endpoint=settings.azure_openai_endpoint,
    api_version=settings.azure_openai_api_version,
    azure_deployment=settings.azure_deployment_name,
    timeout=settings.azure_openai_request_timeout,
)

# How many prior turns (user+assistant messages combined) to surface to the
# model when resolving follow-up questions. Kept small to bound prompt size.
_MAX_HISTORY_TURNS = 6


SYSTEM_PROMPT = (
    "You are the DeployIQ website assistant. You answer visitor questions "
    "ONLY using the context supplied by the application for this turn — "
    "never outside knowledge, and never your own assumptions, estimates, or "
    "general industry knowledge to fill a gap in the context. If the context "
    "doesn't state something needed for part of the question, silently omit "
    "that part — never say the documents don't specify/cover/mention it, and "
    "never guess a plausible-sounding answer to fill the gap. Only mention an "
    "absence when the context itself explicitly states it (e.g. a "
    "certification is 'not yet claimed') — that's a real documented fact, not "
    "a gap. DeployIQ is the active subject of your answers wherever the "
    "context supports it (e.g. 'DeployIQ manages...', 'DeployIQ "
    "applies...'), not a passive afterthought. "
    "You may be shown the recent conversation so far — use it ONLY to "
    "resolve what a follow-up question refers to (e.g. what 'it' or 'that' "
    "means), never as a source of facts; every factual claim must still "
    "come from the supplied context for this turn. "
    f"Keep the final answer to at most {settings.answer_max_words} words: "
    "lead with the direct answer, use short sentences and compact bullets "
    "for lists, no preamble, no restating the question. If the question has "
    "multiple parts, answer every part you can support and silently drop any "
    "part the context doesn't cover — never reject the whole question "
    "because one part is unsupported, and never call out what's missing."
)

# Two few-shot examples in the required promotional, grounded, <=150-word
# style. Facts asserted here only reflect what's actually in data/real_docs.
_FEW_SHOT_EXAMPLES = (
    "Example 1\n"
    "Question: What is the Essential Eight/8 and how does DeployIQ help?\n"
    "Answer: The ACSC Essential Eight is a set of eight mitigation "
    "strategies that reduce the likelihood of cyber compromise and limit "
    "incident impact, with maturity levels organisations progress through. "
    "DeployIQ manages Essential Eight compliance end-to-end: it runs a "
    "phased, AI-enabled implementation with human oversight, prioritising "
    "controls by risk, deploying them in governed, incremental rollouts, "
    "and continuously validating drift so evidence stays audit-ready.\n\n"
    "Example 2\n"
    "Question: What is Zero Trust and how does DeployIQ's platform support "
    "continuous compliance?\n"
    "Answer: Zero Trust is a security model that removes implicit trust, "
    "continuously verifying every access request by identity, device, "
    "context, and risk instead of relying on a perimeter. DeployIQ applies "
    "Zero Trust principles across identity, devices, data, and "
    "infrastructure, and its always-on orchestration cycle (understand, "
    "recommend, implement, validate, improve) keeps posture continuously "
    "validated as conditions change, producing evidence ready for audit."
)


def _format_history(query: str, history: list[ChatTurn] | None) -> str:
    # Same follow-up heuristic used for retrieval (see
    # retriever.needs_conversation_context) — a standalone question gets no
    # history block at all, so it's never nudged by an unrelated prior turn.
    if not history or not needs_conversation_context(query):
        logger.info(
            "history_check query=%r follow_up=False turns_available=%d turns_sent=0",
            query,
            len(history or []),
        )
        return ""

    recent = history[-_MAX_HISTORY_TURNS:]
    lines = [
        f"{'Visitor' if turn.role == 'user' else 'Assistant'}: {turn.content}"
        for turn in recent
    ]

    logger.info(
        "history_check query=%r follow_up=True turns_available=%d turns_sent=%d",
        query,
        len(history),
        len(recent),
    )

    return (
        "Conversation so far (for resolving references only, not a source of facts):\n"
        + "\n".join(lines)
        + "\n\n"
    )


def build_prompt(
    query: str,
    context_chunks: list[str],
    history: list[ChatTurn] | None = None,
) -> str:
    context = "\n\n---\n\n".join(context_chunks)
    history_block = _format_history(query, history)

    return (
        "Answer the user's question using ONLY the supplied context.\n\n"
        "Grounding rules:\n"
        "1. Do not use outside knowledge.\n"
        "2. Do not invent facts, policies, capabilities, prices, "
        "certifications, numbers, dates, or names.\n"
        "3. If the context does not contain information needed for one "
        "part of the question, silently omit that part — do not say the "
        "documents don't provide/cover/mention it, do not apologize for a "
        "gap, just answer the parts you can support.\n"
        "4. Exception: if the context itself explicitly states a status "
        "like 'not claimed', 'unavailable', or 'does not exist', report "
        "that exact status — it's a real documented fact, not a gap.\n"
        "5. Do not add recommendations, contact instructions, or other "
        "information unless directly supported by the context.\n"
        "6. Never assume, estimate, or extrapolate a specific number, date, "
        "price, or name that isn't literally stated in the context, even if "
        "it seems like a reasonable guess — omit it rather than commenting "
        "on it being unspecified.\n"
        "7. Use the conversation so far (if any) only to figure out what "
        "the question refers to, never as a fact source.\n"
        f"8. Keep the final answer to at most {settings.answer_max_words} "
        "words, with Deploy IQ framed as the active subject of the answer.\n\n"
        f"{_FEW_SHOT_EXAMPLES}\n\n"
        f"{history_block}"
        f"Context:\n{context}\n\n"
        f"Question: {query}\n\n"
        "Answer:"
    )


def call_llm(
    query: str,
    context_chunks: list[str],
    history: list[ChatTurn] | None = None,
) -> str:
    """Generate a grounded RAG answer with the configured Azure deployment."""

    response = _client.chat.completions.create(
        model=settings.azure_deployment_name,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_prompt(query, context_chunks, history),
            },
        ],
        temperature=settings.azure_openai_temperature,
        # gpt-5-mini uses max_completion_tokens (not max_tokens); reasoning
        # tokens are spent before visible text, so this must stay generous.
        max_completion_tokens=settings.llm_max_completion_tokens,
        # Main latency lever for a reasoning model — lower effort means
        # fewer reasoning tokens spent before the visible answer.
        reasoning_effort=settings.llm_reasoning_effort,
    )

    usage = getattr(response, "usage", None)
    content = response.choices[0].message.content if response.choices else None

    logger.info(
        "llm_call model=%s non_empty=%s prompt_tokens=%s completion_tokens=%s",
        settings.azure_deployment_name,
        bool(content and content.strip()),
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
    )

    return content or ""
