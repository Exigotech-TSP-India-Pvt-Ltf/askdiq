"""Centralized application configuration."""

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
    # SQL statement logging (incl. 1536-float vector params) — off by default.
    db_echo: bool = False

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
    # Bound the RAG evidence/history included in each generation prompt.
    llm_context_max_tokens: int = 2400
    llm_history_max_tokens: int = 400

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
    retrieval_candidate_multiplier: int = 1
    retrieval_candidate_min: int = 8
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

    # Concurrency: every blocking call (embedding, LLM, rerank, post-checks) runs
    # in asyncio's default thread pool, whose stock size (cpu+4) is exhausted by
    # a few slow LLM calls and starves everything else.
    thread_pool_workers: int = 100
    # CPU-bound ONNX reranks run at most this many at once; more only
    # oversubscribes cores and raises every request's latency.
    rerank_max_concurrency: int = 4
    # Pairs scored per ONNX call. 1 avoids padding every pair to the longest
    # one in the batch: measured 983 -> 601 ms for 12 chunks (i7-10510U).
    rerank_batch_size: int = 1

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

    # App & Environment
    env: str = "development"
    log_level: str = "info"
    # Testing & development IP addresses (excluded from user analytics, manageable via dashboard)
    dev_test_ips: str = "127.0.0.1,::1,localhost,0.0.0.0"
    # Applies to the retrieval + guardrail pipeline only (see
    # /metrics/latency's `pipeline_ex_llm`) — a real LLM call regularly
    # takes multiple seconds, so it's excluded and reported separately.
    latency_budget_ms: int = 200

    # CORS configuration
    cors_allowed_origins: list[str] = ["http://localhost:3000"]

    # Feature Rollout Flags
    ingest_server_conversion: bool = True
    online_eval_enabled: bool = False
    online_eval_sample_rate: float = 0.10

    # White-labelling & Branding Defaults
    brand_name: str = "DeployIQ"
    brand_assistant_name: str = "AskDIQ"
    brand_support_email: str = "support@deployiq.com"

    # Token Pricing Defaults (USD per million tokens)
    price_embed_per_million: float = 0.02
    price_prompt_per_million: float = 0.25
    price_output_per_million: float = 2.00
    # Real Azure cached-input rate is 50% discount ($0.125/1M vs $0.25/1M prompt)
    price_cached_per_million: float = 0.125

    # Audience & Usage Analytics Settings
    audience_cache_ttl_seconds: int = 30
    usage_summary_cache_ttl_seconds: int = 15
    usage_page_max_limit: int = 200
    usage_max_offset: int = 10_000
    usage_export_max_rows: int = 10_000
    usage_max_range_days: int = 400
    usage_agg_max_concurrency: int = 4
    test_ip_max_entries: int = 500

    # Azure Quota Limits (Verified from live Azure headers: 1M TPM, 1,000 RPM)
    azure_openai_tpm_limit: int = 1_000_000
    azure_openai_rpm_limit: int = 1_000
    azure_openai_embedding_tpm_limit: int = 1_000_000
    azure_openai_embedding_rpm_limit: int = 1_000
    azure_openai_max_retries: int = 2
    ingest_max_file_bytes: int = 10 * 1024 * 1024  # 10 MB
    ingest_convert_concurrency: int = 4
    ingest_max_chars: int = 500_000
    pii_allowlist: list[str] = []

    # --- Testing ---
    # Used only by the pytest suite (tests/conftest.py). When set, tests run
    # against this database instead of `database_url`.
    test_database_url: str | None = None
    # Appended as `_<suffix>` to the test database name so parallel workers
    # can each use their own database.
    test_db_suffix: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


def configure_logging() -> None:
    """Configure root logging and structlog with background QueueListener."""
    from app.core.logging_config import configure_logging as _cfg  # noqa: PLC0415 - avoids config<->logging import cycle

    settings = get_settings()
    _cfg(settings.log_level)
