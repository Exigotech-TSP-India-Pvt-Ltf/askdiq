"""Azure OpenAI generation wrapper used by the RAG harness."""

import asyncio
import time
from http import HTTPStatus

import tiktoken
from openai import AsyncAzureOpenAI

from app.core.circuit import CircuitOpenError, get_llm_breaker
from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.quota import quota_monitor
from app.model_settings.service import current_model_settings
from app.retrieval.retriever import needs_conversation_context
from app.schemas.query import ChatTurn, SourceChunk

logger = get_logger(__name__)

settings = get_settings()

_client = AsyncAzureOpenAI(
    api_key=settings.azure_openai_api_key,
    azure_endpoint=settings.azure_openai_endpoint,
    api_version=settings.azure_openai_api_version,
    # No fixed `azure_deployment`: each call routes by its `model`, so the
    # Settings > Models override (and INSIGHTS_MODEL) take effect at runtime.
    timeout=settings.azure_openai_request_timeout,
    max_retries=settings.azure_openai_max_retries,
)


def get_chat_client() -> AsyncAzureOpenAI:
    """Shared Azure chat client (one connection pool for every LLM caller)."""
    return _client


# The tokenizer matches the gpt-5-mini model family.
_TOKEN_ENCODING = tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    return len(_TOKEN_ENCODING.encode(text))


_MAX_HISTORY_TURNS = 6


def get_system_prompt() -> str:
    brand = settings.brand_name
    return (
        f"You are {brand}'s website assistant. Answer only from the supplied "
        "context; never guess or add unsupported facts, estimates, or advice. "
        "Each context item includes its source document and section metadata for reference. "
        "Silently omit unsupported parts, but report an absence when the context "
        "explicitly states it. Use conversation history only to resolve "
        "references, never as evidence. Answer every supported part, lead with "
        f"the direct answer, and keep {brand} as the active subject where "
        "supported. Use concise wording and at most "
        f"{settings.answer_max_words} words."
    )


SYSTEM_PROMPT = get_system_prompt()


def _format_history(query: str, history: list[ChatTurn] | None) -> str:
    # Same follow-up heuristic used for retrieval (see
    # retriever.needs_conversation_context) — a standalone question gets no
    # history block at all, so it's never nudged by an unrelated prior turn.
    if not history or not needs_conversation_context(query):
        logger.info(
            "history_check query_len=%d follow_up=False turns_available=%d turns_sent=0",
            len(query),
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
        and len(_TOKEN_ENCODING.encode(history_block)) > settings.llm_history_max_tokens
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
                content = _TOKEN_ENCODING.decode(content_tokens[:-1], errors="ignore")
                lines[0] = prefix + ": " + content
                history_block = header + "\n".join(lines) + "\n\n"
            break

        history_block = header + "\n".join(lines) + "\n\n"

    turns_sent = len(lines)
    history_tokens = len(_TOKEN_ENCODING.encode(history_block)) if lines else 0

    logger.info(
        "history_check query_len=%d follow_up=True turns_available=%d turns_sent=%d history_tokens=%d",
        len(query),
        len(history),
        turns_sent,
        history_tokens,
    )

    return history_block if lines else ""


def _chunk_to_text(chunk: str | SourceChunk | dict) -> str:
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, dict):
        content = chunk.get("content", "")
        metadata = chunk.get("metadata", {})
    else:
        content = getattr(chunk, "content", str(chunk))
        metadata = getattr(chunk, "metadata", {}) or {}

    source_name = metadata.get("source_name") or metadata.get("file_name") or ""
    section = metadata.get("section_heading") or metadata.get("heading") or ""

    parts = []
    if source_name:
        parts.append(f"Document: {source_name}")
    if section:
        parts.append(f"Section: {section}")

    if parts:
        header = f"[{' | '.join(parts)}]\n"
        return f"{header}{content}"
    return content


def _format_context(context_chunks: list[str | SourceChunk]) -> str:
    chunks: list[str] = []

    for raw_chunk in context_chunks:
        if not raw_chunk:
            continue
        chunk = _chunk_to_text(raw_chunk)
        if not chunk.strip():
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
            and len(_TOKEN_ENCODING.encode(combined)) > settings.llm_context_max_tokens
        ):
            chunk_tokens = _TOKEN_ENCODING.encode(candidate)
            candidate = _TOKEN_ENCODING.decode(chunk_tokens[:-1], errors="ignore")
            combined = "\n\n---\n\n".join([*chunks, candidate])
        if candidate:
            chunks.append(candidate)
        if len(chunk_tokens) > remaining or not candidate:
            break

    return "\n\n---\n\n".join(chunks)


def _build_prompt(
    query: str,
    context_chunks: list[str | SourceChunk],
    history: list[ChatTurn] | None = None,
    clarify_block: str = "",
) -> tuple[str, int]:
    """Return (prompt, context_token_count) from a single context pass.

    ``clarify_block`` (app.harness.clarify.clarify_instruction) lets the model
    ask a clarifying question instead of answering an ambiguous one.
    """
    context = _format_context(context_chunks)
    history_block = _format_history(query, history)

    prompt = (
        "Answer the question using only the context. Silently omit "
        "unsupported parts.\n\n"
        f"{history_block}"
        f"Context:\n{context}\n\n"
        f"{clarify_block}"
        f"Question: {query}\n\n"
        "Answer:"
    )
    return prompt, len(_TOKEN_ENCODING.encode(context))


def build_prompt(
    query: str,
    context_chunks: list[str | SourceChunk],
    history: list[ChatTurn] | None = None,
) -> str:
    return _build_prompt(query, context_chunks, history)[0]


async def call_llm(
    query: str,
    context_chunks: list[str | SourceChunk],
    history: list[ChatTurn] | None = None,
    clarify_block: str = "",
) -> tuple[str, dict[str, int] | None]:
    """Generate a grounded RAG answer with the configured Azure deployment.

    ``clarify_block`` is passed only when the query qualifies for a
    clarifying question (see app.harness.clarify); the reply may then be a
    ``CLARIFY: {...}`` line instead of an answer.

    Returns ``(answer_text, usage)`` where ``usage`` is the *actual*
    token accounting reported by Azure OpenAI in the response
    (``prompt_tokens``/``completion_tokens``/``total_tokens``) — not an
    estimate — so callers can persist/bill on real numbers. ``usage`` is
    ``None`` if the API response didn't include one (shouldn't normally
    happen, but defensive).
    """

    # Tokenizing is CPU work — keep it off the event loop.
    prompt, context_tokens = await asyncio.to_thread(
        _build_prompt, query, context_chunks, history, clarify_block
    )

    model = current_model_settings().llm_deployment
    t_start = time.perf_counter()
    try:
        # Fails fast with CircuitOpenError while Azure is unhealthy; the
        # orchestrator turns that into its graceful `llm_failed` outcome.
        async with get_llm_breaker().guard():
            response = await _client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": get_system_prompt()},
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                reasoning_effort=settings.llm_reasoning_effort,
            )
    except CircuitOpenError:
        raise
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        is_429 = status_code == HTTPStatus.TOO_MANY_REQUESTS or "429" in str(exc)
        quota_monitor.record_chat(0, is_error=True, is_429=is_429)
        if is_429:
            logger.error(
                "llm_throttle_429",
                model=model,
                error=str(exc),
            )
        raise

    llm_latency_ms = (time.perf_counter() - t_start) * 1000
    usage_obj = getattr(response, "usage", None)
    content = response.choices[0].message.content if response.choices else None

    if usage_obj is not None:

        def _safe_int(val: object) -> int:
            if isinstance(val, (int, float)):
                return int(val)
            if isinstance(val, str) and val.isdigit():
                return int(val)
            return 0

        p_tok = _safe_int(getattr(usage_obj, "prompt_tokens", 0))
        c_tok = _safe_int(getattr(usage_obj, "completion_tokens", 0))
        t_tok = _safe_int(getattr(usage_obj, "total_tokens", 0)) or (p_tok + c_tok)

        c_details = getattr(usage_obj, "completion_tokens_details", None)
        reasoning_tokens = (
            _safe_int(getattr(c_details, "reasoning_tokens", 0))
            if c_details is not None
            else 0
        )
        visible_tokens = max(0, c_tok - reasoning_tokens)

        p_details = getattr(usage_obj, "prompt_tokens_details", None)
        cached_tokens = (
            _safe_int(getattr(p_details, "cached_tokens", 0))
            if p_details is not None
            else 0
        )

        usage = {
            "prompt_tokens": p_tok,
            "completion_tokens": c_tok,
            "total_tokens": t_tok,
            "reasoning_tokens": reasoning_tokens,
            "visible_tokens": visible_tokens,
            "cached_tokens": cached_tokens,
        }
        quota_monitor.record_chat(t_tok)
    else:
        usage = None
        quota_monitor.record_chat(0)

    logger.info(
        "llm_call_completed",
        model=model,
        latency_ms=round(llm_latency_ms, 2),
        non_empty=bool(content and content.strip()),
        prompt_tok=usage["prompt_tokens"] if usage else 0,
        comp_tok=usage["completion_tokens"] if usage else 0,
        reasoning_tok=usage["reasoning_tokens"] if usage else 0,
        cached_tok=usage["cached_tokens"] if usage else 0,
        context_tok=context_tokens,
    )

    return content or "", usage
