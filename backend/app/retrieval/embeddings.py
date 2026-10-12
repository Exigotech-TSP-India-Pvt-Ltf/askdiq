"""Azure OpenAI text embedding wrapper."""

from contextvars import ContextVar

from openai import AzureOpenAI

from app.core.config import get_settings


settings = get_settings()

_embedding_client = AzureOpenAI(
    api_key=settings.azure_openai_api_key,
    azure_endpoint=settings.azure_openai_endpoint,
    api_version=settings.azure_openai_api_version,
)


# Per-request sink for query-embedding time, read by the latency middleware.
embedding_timing_sink: ContextVar[list[float] | None] = ContextVar(
    "embedding_timing_sink", default=None
)


def embed_texts_sync(
    texts: list[str],
    task_type: str | None = None,
) -> list[list[float]]:
    """Generate embeddings using Azure OpenAI."""

    response = _embedding_client.embeddings.create(
        model=settings.embedding_model,
        input=texts,
    )

    return [item.embedding for item in response.data]


def embed_text_sync(
    text: str,
    task_type: str | None = None,
) -> list[float]:
    """Generate a single embedding using Azure OpenAI."""

    return embed_texts_sync([text])[0]