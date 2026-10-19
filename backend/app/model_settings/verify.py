"""Live checks run before a model change is saved (Settings > Models).

A wrong deployment name would break every answer (LLM) or every retrieval
(embeddings), so each new name is called once first. The embedding check
also enforces the knowledge-base vector width (``EMBEDDING_DIM``).
"""

from __future__ import annotations

from fastapi import HTTPException

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.harness.prompts import get_chat_client
from app.retrieval.embeddings import get_embedding_client

logger = get_logger(__name__)

HTTP_UNPROCESSABLE = 422
_PROBE_TEXT = "ping"
# Room for a reasoning model's hidden tokens; the reply itself is ignored.
_PROBE_MAX_COMPLETION_TOKENS = 64


def _unusable(kind: str, name: str, exc: Exception) -> HTTPException:
    logger.warning(
        "model_verify_failed", kind=kind, model=name, error=type(exc).__name__
    )
    reason = getattr(exc, "message", None) or type(exc).__name__
    return HTTPException(
        HTTP_UNPROCESSABLE,
        detail=f"The {kind} deployment '{name}' could not be called: {reason}",
    )


async def verify_llm(deployment: str) -> None:
    try:
        await get_chat_client().chat.completions.create(
            model=deployment,
            messages=[{"role": "user", "content": _PROBE_TEXT}],
            max_completion_tokens=_PROBE_MAX_COMPLETION_TOKENS,
        )
    except Exception as exc:
        raise _unusable("LLM", deployment, exc) from exc


async def verify_embedding(model: str) -> None:
    try:
        response = await get_embedding_client().embeddings.create(
            model=model, input=[_PROBE_TEXT]
        )
    except Exception as exc:
        raise _unusable("embedding", model, exc) from exc
    width = len(response.data[0].embedding) if response.data else 0
    expected = get_settings().embedding_dim
    if width != expected:
        raise HTTPException(
            HTTP_UNPROCESSABLE,
            detail=(
                f"The embedding deployment '{model}' returns {width}-dimension "
                f"vectors; the knowledge base stores {expected}."
            ),
        )


async def verify_models(llm: str | None, embedding: str | None) -> None:
    """Verify each changed model name (``None`` = unchanged)."""
    if llm:
        await verify_llm(llm)
    if embedding:
        await verify_embedding(embedding)
