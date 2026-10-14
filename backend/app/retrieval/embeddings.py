"""Azure OpenAI text embedding wrapper (async only)."""

from contextvars import ContextVar

from openai import AsyncAzureOpenAI

from app.core.config import get_settings

settings = get_settings()

_embedding_client = AsyncAzureOpenAI(
    api_key=settings.azure_openai_api_key,
    azure_endpoint=settings.azure_openai_endpoint,
    api_version=settings.azure_openai_api_version,
)


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
    task_type: str | None = None,
    stage: str | None = None,
) -> list[list[float]]:
    """Generate embeddings using Azure OpenAI and capture stage tokens."""
    if not texts:
        return []

    from app.core.quota import quota_monitor

    try:
        response = await _embedding_client.embeddings.create(
            model=settings.embedding_model,
            input=texts,
        )
    except Exception as exc:
        is_429 = getattr(exc, "status_code", None) == 429 or "429" in str(exc)
        quota_monitor.record_embedding(0, is_error=True, is_429=is_429)
        raise

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

    return [item.embedding for item in response.data]


async def embed_text(
    text: str,
    task_type: str | None = None,
    stage: str | None = None,
) -> list[float]:
    """Generate a single embedding using Azure OpenAI."""
    embeddings = await embed_texts([text], task_type=task_type, stage=stage)
    return embeddings[0]


def embed_texts_sync(
    texts: list[str],
    task_type: str | None = None,
    stage: str | None = None,
) -> list[list[float]]:
    """Synchronous wrapper for embed_texts."""
    if not texts:
        return []
    import asyncio
    import concurrent.futures

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                asyncio.run, embed_texts(texts, task_type=task_type, stage=stage)
            ).result()
    return asyncio.run(embed_texts(texts, task_type=task_type, stage=stage))


def embed_text_sync(
    text: str,
    task_type: str | None = None,
    stage: str | None = None,
) -> list[float]:
    """Synchronous wrapper for embed_text."""
    return embed_texts_sync([text], task_type=task_type, stage=stage)[0]
