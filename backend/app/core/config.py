"""Centralized application configuration."""

from functools import lru_cache
from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder identifiers used only by the local mock IdP (AUTH_DEV_MOCK=true)
# when the real ENTRA_* values are not set. Never valid against real Entra.
MOCK_TENANT_ID = "00000000-0000-4000-8000-00000000a51d"
MOCK_CLIENT_ID = "00000000-0000-4000-8000-0000000c11e7"
MOCK_CLIENT_SECRET = "mock-idp-client-secret"  # noqa: S105 - dev-only mock value
MIN_SESSION_SECRET_BYTES = 32


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
    # Service key the Next.js proxy sends in `X-API-Key` (see app/auth/deps.py).
    # It proves "request came from our proxy" only; user access needs a session.
    # Empty by default so the app fails closed until a real value is set.
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
    # Runtime branding (newplan section 14). The DB row overrides these.
    brand_company_name: str = "Exigo Tech"
    # Public GET /branding: in-process cache TTL and browser max-age.
    branding_cache_ttl_seconds: int = Field(default=60, ge=1)
    branding_http_max_age_seconds: int = Field(default=300, ge=0)
    # Brand asset uploads (PNG, WebP or sanitized SVG).
    branding_asset_max_bytes: int = Field(default=512 * 1024, ge=1024)
    branding_asset_max_dimension: int = Field(default=4096, ge=16)

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

    # --- Platform: security & resilience (PR-03) ---
    # /docs, /redoc and /openapi.json are disabled when ENV=production unless
    # this is explicitly turned on.
    enable_api_docs: bool = False
    # Response security headers (newplan section 12.2). HSTS is only sent when
    # ENV=production, since TLS terminates at nginx there.
    security_csp: str = "default-src 'none'; frame-ancestors 'none'"
    security_hsts: str = "max-age=63072000; includeSubDomains"
    security_referrer_policy: str = "strict-origin-when-cross-origin"
    security_permissions_policy: str = "camera=(), microphone=(), geolocation=()"
    security_coop: str = "same-origin"
    security_frame_options: str = "DENY"
    # Explicit CORS allow-lists (no wildcards).
    cors_allow_headers: list[str] = [
        "Content-Type",
        "X-API-Key",
        "X-Request-ID",
        "X-Client-Id",
        "If-None-Match",
    ]
    cors_allow_methods: list[str] = ["GET", "POST", "PATCH", "DELETE", "OPTIONS"]
    # Default timeout (seconds) for outbound HTTP clients without a dedicated
    # setting (embeddings, httpx). Chat-completion clients keep
    # AZURE_OPENAI_REQUEST_TIMEOUT as their LLM-specific timeout.
    outbound_timeout_seconds: float = 30.0
    # LLM circuit breaker: opens after N transient failures inside the window,
    # then lets a single half-open probe through after the reset timeout.
    llm_breaker_failure_threshold: int = 5
    llm_breaker_window_seconds: float = 60.0
    llm_breaker_reset_seconds: float = 30.0
    # Max seconds lifespan shutdown waits for supervised background tasks.
    task_drain_timeout_seconds: float = 10.0

    # --- Platform: jobs, shared state & HTTP (PR-04) ---
    # Uvicorn workers per replica; the shared state below makes N > 1 safe.
    web_concurrency: int = 1
    # Validate pooled connections on checkout (stale sockets behind LBs/NAT).
    db_pool_pre_ping: bool = True
    db_pool_recycle_seconds: int = 1800
    # `SET LOCAL statement_timeout` applied by `read_only_session()`.
    dashboard_statement_timeout_ms: int = 5000
    # Global back-pressure on the LLM path: 429 + Retry-After when saturated.
    llm_max_concurrency: int = 64
    llm_saturated_retry_after_seconds: int = 2
    online_eval_max_concurrency: int = 4
    # Default bound for `gather_scoped()` parallel aggregation.
    agg_max_concurrency: int = 4
    # Shared state: "auto" = memory when ENV=test, otherwise postgres.
    state_backend: str = "auto"
    # Actors exempt from the per-user /query limit (load tests only).
    rate_limit_exempt_actors: list[str] = []
    # LISTEN/NOTIFY channel for cross-worker events (cache invalidation, SSE).
    events_channel: str = "askdiq_events"
    events_reconnect_max_seconds: float = 30.0
    # HTTP efficiency.
    gzip_minimum_size: int = 1024
    http_cache_max_age_seconds: int = 15
    sse_heartbeat_seconds: float = 15.0
    sse_retry_ms: int = 3000
    # Background jobs (Postgres SKIP LOCKED queue).
    jobs_worker_enabled: bool = True
    jobs_max_concurrency: int = 4
    jobs_lock_timeout_seconds: int = 300
    jobs_poll_interval_seconds: float = 1.0
    jobs_max_attempts: int = 5
    jobs_backoff_base_seconds: float = 2.0
    jobs_backoff_max_seconds: float = 600.0
    jobs_shutdown_timeout_seconds: float = 30.0
    jobs_retention_days: int = 7
    state_cleanup_interval_seconds: int = 60
    # Feedback / RLHF loop (PR-11).
    feedback_trend_days: int = Field(default=30, ge=1, le=365)
    # Retrieval confidence below this marks a low-rated turn as a knowledge gap.
    feedback_gap_confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    feedback_gap_limit: int = Field(default=10, ge=1, le=100)
    feedback_export_batch_size: int = Field(default=500, ge=10, le=5000)

    # --- Insights (PR-12) ---
    # Turns per classifier LLM call.
    insights_batch_size: int = Field(default=10, ge=1, le=50)
    # Classifier tokens allowed per local day; over budget, jobs are deferred.
    insights_daily_token_budget: int = Field(default=200_000, ge=0)
    # IANA zone for "day" boundaries (budget window, daily rollup).
    insights_timezone: str = Field(
        default="Australia/Sydney", pattern=r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$"
    )
    # Enqueue `insights.classify` after each persisted assistant message.
    insights_classify_on_write: bool = True
    # Truncation applied (after PII masking) before text reaches the classifier.
    insights_user_max_chars: int = 1000
    insights_answer_max_chars: int = 600
    # Empty = use AZURE_DEPLOYMENT_NAME.
    insights_model: str = ""
    insights_reasoning_effort: str = "minimal"
    insights_completion_tokens_per_turn: int = 120

    # --- Auth: SSO (Entra OIDC), sessions and RBAC (PR-06) ---
    # Required unless AUTH_DEV_MOCK=true (see `_validate_auth`).
    entra_tenant_id: str | None = None
    entra_client_id: str | None = None
    entra_client_secret: SecretStr | None = None
    entra_redirect_uri: str | None = None
    entra_authority_host: str = "https://login.microsoftonline.com"
    app_base_url: str = "http://localhost:3000"
    # Keys the session-id hash (HMAC-SHA256); no default, must be set in .env.
    session_secret: SecretStr
    session_ttl_minutes: int = Field(default=480, ge=5)
    session_idle_minutes: int = Field(default=60, ge=1)
    session_cookie_name: str = "askdiq_session"
    session_cookie_secure: bool = False
    # `last_seen_at` is written at most this often per session.
    session_touch_interval_seconds: int = Field(default=60, ge=1)
    # Comma-separated; these emails become active superadmins on first login.
    superadmin_emails: str = ""
    access_contact_email: str = ""
    # Local-only mock IdP; refused unless ENV=development.
    auth_dev_mock: bool = False
    # Mock IdP authority base; empty = APP_BASE_URL + "/mock-idp".
    auth_mock_base_url: str = ""
    oidc_http_timeout_seconds: float = Field(default=10.0, gt=0)
    oidc_state_ttl_seconds: int = Field(default=600, ge=60)
    oidc_metadata_cache_seconds: int = Field(default=3600, ge=60)
    oidc_clock_skew_seconds: int = Field(default=60, ge=0, le=300)

    @model_validator(mode="after")
    def _validate_auth(self) -> Self:
        if self.auth_dev_mock:
            if self.env != "development":
                raise ValueError(
                    "AUTH_DEV_MOCK=true is only allowed when ENV=development; "
                    "refusing to start."
                )
            self.entra_tenant_id = self.entra_tenant_id or MOCK_TENANT_ID
            self.entra_client_id = self.entra_client_id or MOCK_CLIENT_ID
            if not self.entra_client_secret:
                self.entra_client_secret = SecretStr(MOCK_CLIENT_SECRET)
        else:
            missing = [
                name.upper()
                for name in (
                    "entra_tenant_id",
                    "entra_client_id",
                    "entra_client_secret",
                    "entra_redirect_uri",
                )
                if not _has_value(getattr(self, name))
            ]
            if missing:
                raise ValueError(
                    f"Missing SSO settings: {', '.join(missing)} "
                    "(or set AUTH_DEV_MOCK=true with ENV=development)."
                )
        if not self.entra_redirect_uri:
            self.entra_redirect_uri = f"{self.app_base_url.rstrip('/')}/auth/callback"
        if not self.session_secret.get_secret_value():
            raise ValueError("SESSION_SECRET must not be empty.")
        if self.env == "production" and (
            len(self.session_secret.get_secret_value().encode())
            < MIN_SESSION_SECRET_BYTES
        ):
            raise ValueError(
                f"SESSION_SECRET must be at least {MIN_SESSION_SECRET_BYTES} bytes "
                "in production."
            )
        return self

    @property
    def superadmin_email_set(self) -> frozenset[str]:
        return frozenset(
            e.strip().lower() for e in self.superadmin_emails.split(",") if e.strip()
        )

    @property
    def oidc_authority(self) -> str:
        """Authority base: real Entra, or the APP_BASE_URL-relative mock IdP."""
        if self.auth_dev_mock:
            base = (
                self.auth_mock_base_url or f"{self.app_base_url.rstrip('/')}/mock-idp"
            )
            return base.rstrip("/")
        return self.entra_authority_host.rstrip("/")

    @property
    def app_origin(self) -> str:
        """`scheme://host[:port]` of APP_BASE_URL (CSRF Origin check)."""
        parts = urlsplit(self.app_base_url)
        return f"{parts.scheme}://{parts.netloc}".lower()


def _has_value(value: object) -> bool:
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value())
    return bool(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def configure_logging() -> None:
    """Configure root logging and structlog with background QueueListener."""
    from app.core.logging_config import configure_logging as _cfg  # noqa: PLC0415 - avoids config<->logging import cycle

    settings = get_settings()
    _cfg(settings.log_level)
