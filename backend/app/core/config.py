"""Centralized application configuration."""

import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    # Database
    database_url: str
    # Sized for ~20 concurrent users each holding a connection for the
    # duration of a slow (multi-second) /query LLM call — without enough
    # headroom here, concurrent users would queue behind each other for a
    # free connection instead of getting an answer.
    db_pool_size: int = 20
    db_max_overflow: int = 10

    # Azure OpenAI
    azure_openai_api_key: str
    azure_openai_endpoint: str
    azure_openai_api_version: str
    azure_deployment_name: str
    # gpt-5-mini is a reasoning model and ONLY accepts temperature=1.0 —
    # any other value makes the API call throw. Ground via the prompt instead.
    azure_openai_temperature: float = 1.0
    azure_openai_request_timeout: float = 60.0
    # Generous cap: reasoning tokens are spent BEFORE visible text, so too
    # low a cap silently returns an empty answer.
    llm_max_completion_tokens: int = 1400
    # Reasoning effort ("minimal" | "low" | "medium" | "high") is the main
    # latency lever for a reasoning model: fewer internal reasoning tokens
    # before the visible answer means a materially faster response. Set to
    # the lowest level — still enforced by the grounding rules/guardrails,
    # so answer quality safety net is unchanged.
    llm_reasoning_effort: str = "minimal"
    answer_max_words: int = 150

    # Embeddings — must match the deployed Azure model and the pgvector
    # column width (see alembic f9462f0a85c6).
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # Auth
    api_jwt_secret: str
    api_jwt_algorithm: str = "HS256"
    # Single shared API key required in the `X-API-Key` header on every
    # protected route (see app/governance/auth.py). Empty by default so the
    # app fails closed until a real value is set in .env.
    api_key_name: str = ""

    # Guardrails
    # Semantic cosine-similarity threshold (STEP 7), not lexical overlap.
    groundedness_threshold: float = 0.45
    max_query_length: int = 2000

    # Retrieval
    # Number of chunks retrieved per query — fixed server-side (not a client
    # request parameter) so retrieval cost/behavior can't be pushed up by a
    # caller; tune via .env if needed.
    default_top_k: int = 5
    enable_query_decomposition: bool = True
    compound_query_top_k: int = 9
    enable_reranker: bool = True
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    # Broad cosine-recall pool size before rerank/selection: max(top_k * multiplier, minimum).
    # Kept small on purpose — the cross-encoder reranker is CPU-bound and its
    # cost scales with candidate count; this was the single largest
    # contributor to /query latency (~9.7s p50 at multiplier=5/min=25).
    retrieval_candidate_multiplier: int = 3
    retrieval_candidate_min: int = 12
    # Cross-document quota: round-robin across at most this many top-scoring documents.
    cross_doc_quota_max_documents: int = 3
    # Below this raw cosine score, retrieval is treated as off-topic. Kept
    # low deliberately: real visitors type terse/abbreviated questions (e.g.
    # "what is human in loop?" measured ~0.22 vs. true off-topic "what's the
    # weather?" ~0.10) — gating too high silently blocks on-topic questions
    # before the LLM+groundedness check (the real safety net) ever sees them.
    min_retrieval_score: float = 0.15
    # Extra LLM generation attempts (beyond the first) on empty/failed/ungrounded output.
    # Each retry is a full LLM call (~5-8s) — capped at 1 (not 2) so a run of
    # bad luck can't stack 3 LLM calls into one request's tail latency.
    max_llm_retries: int = 1

    # Chat sessions — history is stored per-session in the database (see
    # app.core.sessions) so different sessions never see each other's history.
    # Minutes of inactivity before a session expires and a new one starts.
    session_timeout_minutes: int = 60
    # How many prior messages (user+assistant combined) to load as context
    # for a follow-up question in the same session.
    session_history_max_messages: int = 20

    # Per-user request throttling on /query (the expensive LLM path), so one
    # user hammering the endpoint can't degrade it for everyone else.
    rate_limit_per_minute: int = 10
    # Separate, looser per-IP cap on top of the per-user one — catches abuse
    # spread across multiple accounts from the same source. Higher than the
    # per-user limit since several genuine users can share one IP (office/
    # NAT'd network).
    rate_limit_per_minute_per_ip: int = 30

    # Client IP resolution (see app.core.request_meta.get_client_ip). The
    # X-Forwarded-For header is trivially spoofable by any direct caller, so
    # it's only honored when this app actually sits behind a trusted reverse
    # proxy/load balancer that sets it itself — otherwise the direct TCP peer
    # address is used. Flip to True only when deployed behind such a proxy.
    trust_proxy_headers: bool = False

    # App
    env: str = "development"
    log_level: str = "info"
    # Applies to the retrieval + guardrail pipeline only (see
    # /metrics/latency's `pipeline_ex_llm`) — a real LLM call regularly
    # takes multiple seconds, so it's excluded and reported separately.
    latency_budget_ms: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()


def configure_logging() -> None:
    """Configure root logging once at process startup (STEP 9)."""
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
