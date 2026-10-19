import time
from contextvars import ContextVar
from http import HTTPStatus

from openai import AsyncAzureOpenAI

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.quota import quota_monitor
from app.model_settings.service import current_model_settings

logger = get_logger(__name__)
settings = get_settings()

_embedding_client = AsyncAzureOpenAI(
    api_key=settings.azure_openai_api_key,
    azure_endpoint=settings.azure_openai_endpoint,
    api_version=settings.azure_openai_api_version,
    timeout=settings.outbound_timeout_seconds,
    max_retries=settings.azure_openai_max_retries,
)


def get_embedding_client() -> AsyncAzureOpenAI:
    """Shared Azure embeddings client."""
    return _embedding_client


# Per-request sink for query-embedding time, read by the latency middleware.
embedding_timing_sink: ContextVar[list[float] | None] = ContextVar(
    "embedding_timing_sink", default=None
)

# Per-request sink for embedding tokens tagged by stage: (stage, token_count)
# Stages: "query", "guardrail", "ingest"
embedding_tokens_sink: ContextVar[list[tuple[str, int]] | None] = ContextVar(
    "embedding_tokens_sink", default=None
)
current_embedding_stage: ContextVar[str] = ContextVar(
    "current_embedding_stage", default="query"
)

try:
    import tiktoken

    _EMBED_ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception:
    _EMBED_ENCODING = None


async def embed_texts(
    texts: list[str],
    task_type: str | None = None,  # noqa: ARG001 - public kwarg, callers pass it
    stage: str | None = None,
) -> list[list[float]]:
    """Generate embeddings using Azure OpenAI and capture stage tokens."""
    if not texts:
        return []

    model = current_model_settings().embedding_model
    t_start = time.perf_counter()
    try:
        response = await _embedding_client.embeddings.create(
            model=model,
            input=texts,
        )
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        is_429 = status_code == HTTPStatus.TOO_MANY_REQUESTS or "429" in str(exc)
        quota_monitor.record_embedding(0, is_error=True, is_429=is_429)
        if is_429:
            logger.error(
                "embedding_throttle_429",
                model=model,
                texts_count=len(texts),
                error=str(exc),
            )
        raise

    embed_ms = (time.perf_counter() - t_start) * 1000
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
    if prompt_tokens is None:
        if _EMBED_ENCODING is not None:
            prompt_tokens = sum(len(_EMBED_ENCODING.encode(t)) for t in texts)
        else:
            prompt_tokens = sum(len(t.split()) for t in texts) * 4 // 3

    tok_int = int(prompt_tokens)
    quota_monitor.record_embedding(tok_int)

    effective_stage = stage or current_embedding_stage.get()
    sink = embedding_tokens_sink.get()
    if sink is not None:
        sink.append((effective_stage, tok_int))

    logger.info(
        "embedding_batch_complete",
        stage=effective_stage,
        texts_count=len(texts),
        tokens=tok_int,
        latency_ms=round(embed_ms, 2),
    )

    return [item.embedding for item in response.data]


async def embed_text(
    text: str,
    task_type: str | None = None,
    stage: str | None = None,
) -> list[float]:
    """Generate a single embedding using Azure OpenAI."""
    embeddings = await embed_texts([text], task_type=task_type, stage=stage)
    return embeddings[0]
