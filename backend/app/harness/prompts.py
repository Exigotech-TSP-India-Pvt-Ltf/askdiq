"""Azure OpenAI generation wrapper used by the RAG harness."""

import logging

import tiktoken
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

# The tokenizer matches the gpt-5-mini model family.
_TOKEN_ENCODING = tiktoken.get_encoding("o200k_base")
_MAX_HISTORY_TURNS = 6


SYSTEM_PROMPT = (
    "You are DeployIQ's website assistant. Answer only from the supplied "
    "context; never guess or add unsupported facts, estimates, or advice. "
    "Silently omit unsupported parts, but report an absence when the context "
    "explicitly states it. Use conversation history only to resolve "
    "references, never as evidence. Answer every supported part, lead with "
    "the direct answer, and keep DeployIQ as the active subject where "
    f"supported. Use concise wording and at most {settings.answer_max_words} "
    "words."
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
    header = "Conversation so far (references only, not evidence):\n"
    lines = [
        f"{'Visitor' if turn.role == 'user' else 'Assistant'}: {turn.content}"
        for turn in recent
    ]
    history_block = header + "\n".join(lines) + "\n\n"

    while (
        lines
        and len(_TOKEN_ENCODING.encode(history_block))
        > settings.llm_history_max_tokens
    ):
        if len(lines) > 1:
            lines.pop(0)
        else:
            prefix, _, content = lines[0].partition(": ")
            available = max(
                0,
                settings.llm_history_max_tokens
                - len(_TOKEN_ENCODING.encode(header + prefix + ": \n\n")),
            )
            content_tokens = _TOKEN_ENCODING.encode(content)
            content = _TOKEN_ENCODING.decode(
                content_tokens[:available], errors="ignore"
            )
            lines[0] = prefix + ": " + content
            history_block = header + "\n".join(lines) + "\n\n"
            while (
                len(_TOKEN_ENCODING.encode(history_block))
                > settings.llm_history_max_tokens
            ):
                content_tokens = _TOKEN_ENCODING.encode(content)
                if not content_tokens:
                    lines.clear()
                    history_block = ""
                    break
                content = _TOKEN_ENCODING.decode(
                    content_tokens[:-1], errors="ignore"
                )
                lines[0] = prefix + ": " + content
                history_block = header + "\n".join(lines) + "\n\n"
            break

        history_block = header + "\n".join(lines) + "\n\n"

    turns_sent = len(lines)
    history_tokens = len(_TOKEN_ENCODING.encode(history_block)) if lines else 0

    logger.info(
        "history_check query=%r follow_up=True turns_available=%d turns_sent=%d history_tokens=%d",
        query,
        len(history),
        turns_sent,
        history_tokens,
    )

    return history_block if lines else ""


def _format_context(context_chunks: list[str]) -> str:
    chunks: list[str] = []

    for chunk in context_chunks:
        if not chunk:
            continue
        separator = "\n\n---\n\n" if chunks else ""
        existing = "\n\n---\n\n".join(chunks)
        remaining = settings.llm_context_max_tokens - len(
            _TOKEN_ENCODING.encode(existing + separator)
        )
        if remaining <= 0:
            break
        chunk_tokens = _TOKEN_ENCODING.encode(chunk)
        candidate = chunk
        if len(chunk_tokens) > remaining:
            candidate = _TOKEN_ENCODING.decode(
                chunk_tokens[:remaining], errors="ignore"
            )
        combined = "\n\n---\n\n".join([*chunks, candidate])
        while (
            candidate
            and len(_TOKEN_ENCODING.encode(combined))
            > settings.llm_context_max_tokens
        ):
            chunk_tokens = _TOKEN_ENCODING.encode(candidate)
            candidate = _TOKEN_ENCODING.decode(
                chunk_tokens[:-1], errors="ignore"
            )
            combined = "\n\n---\n\n".join([*chunks, candidate])
        if candidate:
            chunks.append(candidate)
        if len(chunk_tokens) > remaining or not candidate:
            break

    return "\n\n---\n\n".join(chunks)


def build_prompt(
    query: str,
    context_chunks: list[str],
    history: list[ChatTurn] | None = None,
) -> str:
    context = _format_context(context_chunks)
    history_block = _format_history(query, history)

    return (
        "Answer the question using only the context. Silently omit "
        "unsupported parts.\n\n"
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

    prompt = build_prompt(query, context_chunks, history)
    response = _client.chat.completions.create(
        model=settings.azure_deployment_name,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": prompt,
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
        "llm_call model=%s non_empty=%s prompt_tokens=%s completion_tokens=%s context_tokens=%d",
        settings.azure_deployment_name,
        bool(content and content.strip()),
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
        len(_TOKEN_ENCODING.encode(_format_context(context_chunks))),
    )

    return content or ""
