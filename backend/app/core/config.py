"""Centralized application configuration."""

import re
from functools import lru_cache
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder identifiers used only by the local mock IdP (AUTH_DEV_MOCK=true)
# when the real ENTRA_* values are not set. Never valid against real Entra.
MOCK_TENANT_ID = "00000000-0000-4000-8000-00000000a51d"
MOCK_CLIENT_ID = "00000000-0000-4000-8000-0000000c11e7"
MOCK_CLIENT_SECRET = "mock-idp-client-secret"  # noqa: S105 - dev-only mock value
MIN_SESSION_SECRET_BYTES = 32
# Plain address (recipients) or "Name <address>" (sender); no spaces in the address.
_EMAIL_RE = re.compile(r"^[^@\s<>,]+@[^@\s<>,]+\.[^@\s<>,]+$|<[^@\s<>,]+@[^@\s<>,]+>$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    # Database
    database_url: str
    # Sized for ~20 concurrent users each holding a connection for the
    # duration of a slow (multi-second) /query LLM call — without enough
    # headroom here, concurrent users would queue behind each other for a
    # free connection instead of getting an answer.
    db_pool_size: int = 20
    db_max_overflow: int = 10
    # Last-resort wait for a free pooled connection (seconds); generous so a
    # burst queues and succeeds instead of failing.
    db_pool_timeout_seconds: float = Field(default=120.0, gt=0)
    # Postgres ends a session left "idle in transaction" this long (ms; 0 =
    # off): a safety net so a lost request can never pin locks or a slot.
    db_idle_in_transaction_timeout_ms: int = Field(default=60_000, ge=0)
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
    # Metadata keys a /query `filters` object may use. Unknown keys and non-scalar
    # values are ignored (a stray key such as Swagger's "additionalProp1" used to
    # filter out every chunk and turn a valid question into an empty answer).
    retrieval_filter_keys: list[str] = ["source_type", "corpus", "ingestion_source"]
    # Clarifying questions (app/harness/clarify.py): a short, underspecified
    # question whose evidence is spread over several distinct topics with
    # similar scores lets the LLM ask "which one?" with 2-4 options taken from
    # the retrieved document titles/sections, instead of guessing.
    clarify_enabled: bool = True
    # At most this many clarification turns in a row per session; after that
    # the assistant always answers with its best interpretation.
    clarify_max_consecutive: int = 2
    # Only questions with at most this many content words are considered.
    clarify_max_query_words: int = 4
    # Topics whose best score is within this margin of the top score compete.
    clarify_score_margin: float = 0.06
    # Topics needed before a clarification is offered, and the most shown.
    clarify_min_options: int = 2
    clarify_max_options: int = 4
    # The model asks as few questions as needed, at most this many, in one turn.
    clarify_max_questions: int = 3
    # A short question that may need clarifying is retrieved wider, so the
    # options cover the knowledge base's real distinct intents.
    clarify_retrieval_top_k: int = 10
    clarify_retrieval_max_documents: int = 6
    # Honour `ip_address` in request bodies (/query, /sessions, feedback): the
    # website frontend calls server-to-server and forwards each end user's IP,
    # which is stored against that user. Only valid IPv4/IPv6 values are used.
    allow_client_ip_override: bool = True
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
    # Most recent sessions returned by GET /sessions (newest activity first).
    session_list_max_items: int = Field(default=200, ge=1, le=1000)

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
    # Runtime branding. The DB row overrides these.
    brand_company_name: str = "Exigo Tech"
    # Public GET /branding: in-process cache TTL and browser max-age.
    branding_cache_ttl_seconds: int = Field(default=60, ge=1)
    # Upper bound on one branding DB read (config defaults are served on timeout).
    branding_load_timeout_seconds: float = Field(default=5.0, gt=0)
    branding_http_max_age_seconds: int = Field(default=300, ge=0)
    # Brand asset uploads (PNG, WebP or sanitized SVG).
    branding_asset_max_bytes: int = Field(default=512 * 1024, ge=1024)
    branding_asset_max_dimension: int = Field(default=4096, ge=16)

    # Token Pricing Defaults (USD per million tokens)
    price_embed_per_million: float = 0.02
    price_prompt_per_million: float = 0.25
    price_output_per_million: float = 2.00
    # gpt-5-mini cached input is 10% of the input rate ($0.025 vs $0.25 per 1M;
    # Azure Retail Prices API, Global deployment).
    price_cached_per_million: float = 0.025
    # Settings > Models runtime overrides (DB row over the values above and
    # AZURE_DEPLOYMENT_NAME / EMBEDDING_MODEL): per-worker refresh interval and
    # the bound on one read (the last good values are kept on failure).
    model_settings_refresh_seconds: int = Field(default=60, ge=1)
    model_settings_load_timeout_seconds: float = Field(default=5.0, gt=0)

    # Audience & Usage Analytics Settings
    audience_cache_ttl_seconds: int = 30
    usage_summary_cache_ttl_seconds: int = 15
    # Dashboard aggregates (app.core.cache.dashboard_cache): served as-is while
    # younger than FRESH, served at once and refreshed in the background until
    # STALE, recomputed while the caller waits after that. FRESH=0 disables.
    dashboard_cache_fresh_seconds: float = Field(default=15.0, ge=0)
    dashboard_cache_stale_seconds: float = Field(default=600.0, ge=0)
    # Precomputed dashboard aggregates (app.dashboard.snapshots, table
    # dashboard_snapshots). The refresher runs every REFRESH s (0 = off) in the
    # jobs worker process, recomputing watched snapshots when data changed and
    # every snapshot at least every IDLE_REFRESH s; a read of a snapshot older
    # than MAX_AGE also triggers a background recompute. Unused ones are
    # dropped after KEEP_HOURS.
    dashboard_snapshot_refresh_seconds: float = Field(default=30.0, ge=0)
    # false = in-process cache only (no dashboard_snapshots reads / writes).
    dashboard_snapshots_persist: bool = True
    dashboard_snapshot_idle_refresh_seconds: float = Field(default=900.0, ge=1)
    dashboard_snapshot_max_age_seconds: float = Field(default=1800.0, ge=1)
    dashboard_snapshot_keep_hours: float = Field(default=168.0, gt=0)
    # Real-time: the jobs worker checks the data stamp (new chats, feedback,
    # evaluations, alerts) every WATCH s (0 = off); on a change it NOTIFYs the
    # console live stream at once and refreshes the watched snapshots, at most
    # once per MIN_CHANGE_REFRESH s.
    dashboard_watch_seconds: float = Field(default=2.0, ge=0)
    dashboard_min_change_refresh_seconds: float = Field(default=3.0, ge=0)
    # Console live stream: events within this window are sent as one frame.
    console_stream_coalesce_seconds: float = Field(default=0.5, ge=0)
    usage_page_max_limit: int = 200
    usage_max_offset: int = 10_000
    usage_export_max_rows: int = 10_000
    usage_max_range_days: int = 400
    usage_agg_max_concurrency: int = 4
    # Monthly spend budget (USD) drawn on the dashboard cost bullet and the
    # usage forecast. Unset (None) = no budget line.
    monthly_cost_budget_usd: float | None = Field(default=None, ge=0)
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
    # Response security headers. HSTS is only sent when
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
    # Online evaluations waiting for a slot; beyond this new ones are skipped.
    online_eval_max_pending: int = Field(default=200, ge=1)
    # Default bound for `gather_scoped()` parallel aggregation.
    agg_max_concurrency: int = 4
    # Pooled connections parallel aggregation (all requests together) may never
    # take, so sign-in checks, /health and plain reads always find one.
    agg_pool_reserve: int = Field(default=5, ge=0)
    # Shared state: "auto" = memory when ENV=test, otherwise postgres.
    state_backend: str = "auto"
    # Actors exempt from the per-user /query limit (load tests only).
    rate_limit_exempt_actors: list[str] = []
    # LISTEN/NOTIFY channel for cross-worker events (cache invalidation, SSE).
    events_channel: str = "askdiq_events"
    events_reconnect_max_seconds: float = 30.0
    # HTTP efficiency.
    gzip_minimum_size: int = 1024
    # 0 = browsers revalidate every read (ETag -> 304): the console keeps its own
    # cache, and a refetch after a live update must never get an old copy.
    http_cache_max_age_seconds: int = 0
    sse_heartbeat_seconds: float = 15.0
    sse_retry_ms: int = 3000
    # Each SSE response ends after this long and the browser reconnects (retry),
    # so open streams can never block a server reload or graceful shutdown.
    sse_max_stream_seconds: float = 120.0

    # --- Governance endpoints & live stream (PR-10) ---
    # Above this many matching rows, list totals switch to a planner estimate.
    governance_exact_count_cap: int = Field(default=100_000, ge=1)
    governance_export_max_rows: int = Field(default=100_000, ge=1)
    # Upper bounds (ms) of the fast / moderate / slow trace latency buckets.
    governance_latency_buckets_ms: list[int] = [1000, 3000, 10000]
    # Health status thresholds (value >= threshold -> that status).
    governance_health_window_minutes: int = Field(default=60, ge=1)
    governance_health_error_rate_degraded_pct: float = 2.0
    governance_health_error_rate_incident_pct: float = 10.0
    governance_health_p95_degraded_ms: float = 8000.0
    governance_health_p95_incident_ms: float = 20000.0
    governance_health_quota_degraded_pct: float = 80.0
    governance_health_quota_incident_pct: float = 95.0
    governance_incidents_window_hours: int = Field(default=24, ge=1)
    governance_incidents_limit: int = Field(default=10, ge=1, le=100)
    governance_stream_interval_seconds: float = Field(default=5.0, gt=0)
    # Upper bound on one SSE producer snapshot (overview queries).
    governance_stream_snapshot_timeout_seconds: float = Field(default=60.0, gt=0)
    governance_quota_sample_seconds: float = Field(default=60.0, gt=0)
    governance_quota_series_points: int = Field(default=60, ge=1)
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

    # --- Resource telemetry (app.governance.resources / metrics_sampler) ---
    metrics_enabled: bool = True
    # Each API worker samples CPU / memory / event-loop lag / DB pool this often
    # and the live stream pushes the aggregated snapshot at the same pace.
    metrics_sample_interval_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    # Rolling history kept (DB, all workers) for sparklines and alert windows.
    metrics_history_minutes: int = Field(default=15, ge=5, le=120)
    # A worker without a sample for this many intervals is treated as gone.
    metrics_worker_stale_intervals: int = Field(default=3, ge=2, le=20)

    # --- Alerts (rules evaluated by the job worker; optional email) ---
    alerts_enabled: bool = True
    # Database-backed rules run at most once per interval across all workers.
    alerts_evaluate_interval_seconds: int = Field(default=30, ge=15)
    # Each API process samples its own signals (circuit breaker, Azure quota,
    # 429s, event bus, database reachability) this often.
    alerts_sample_interval_seconds: int = Field(default=30, ge=5)
    # A process-sampled alert not re-reported for this long is resolved.
    alerts_local_resolve_minutes: int = Field(default=5, ge=1)
    # A new alert with the same dedupe key is not emailed again within this.
    alert_cooldown_minutes: int = Field(default=60, ge=0)
    alert_retention_days: int = Field(default=90, ge=1)
    # Minimum severity that is emailed: info | warning | critical.
    alert_email_min_severity: Literal["info", "warning", "critical"] = "info"
    alert_email_max_attempts: int = Field(default=5, ge=1, le=20)
    alert_notify_resolved: bool = True
    # IANA zone shown next to UTC in alert emails.
    alert_timezone: str = Field(
        default="Australia/Sydney", pattern=r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$"
    )
    # Rule thresholds (the health thresholds above are shared with Governance).
    alert_min_requests: int = Field(default=20, ge=1)
    alert_latency_min_samples: int = Field(default=10, ge=1)
    alert_llm_failure_window_minutes: int = Field(default=15, ge=1)
    alert_llm_failures_threshold: int = Field(default=5, ge=1)
    alert_azure_429_threshold: int = Field(default=3, ge=1)
    # Azure-side 429s (Azure Monitor metrics, every caller of the resource) in
    # the last window of stored PT1M points (azure_monitor_429_spike).
    alert_azure_monitor_429_threshold: int = Field(default=10, ge=1)
    alert_azure_monitor_429_window_minutes: int = Field(default=15, ge=5, le=120)
    alert_budget_warning_pct: float = Field(default=80.0, gt=0)
    alert_budget_critical_pct: float = Field(default=100.0, gt=0)
    alert_feedback_window_minutes: int = Field(default=60, ge=5)
    alert_feedback_min_ratings: int = Field(default=5, ge=1)
    alert_feedback_negative_pct: float = Field(default=25.0, ge=0, le=100)
    alert_failure_window_minutes: int = Field(default=60, ge=5)
    alert_online_eval_violations_threshold: int = Field(default=3, ge=1)
    alert_event_bus_grace_seconds: int = Field(default=60, ge=5)
    # Resource rules: the condition must hold for the whole sustain window
    # (a single spike never alerts).
    alert_resource_sustain_seconds: int = Field(default=120, ge=10)
    alert_cpu_warning_pct: float = Field(default=80.0, gt=0, le=100)
    alert_cpu_critical_pct: float = Field(default=95.0, gt=0, le=100)
    alert_memory_warning_pct: float = Field(default=85.0, gt=0, le=100)
    alert_memory_critical_pct: float = Field(default=95.0, gt=0, le=100)
    alert_loop_lag_warning_ms: float = Field(default=200.0, gt=0)
    alert_loop_lag_critical_ms: float = Field(default=1000.0, gt=0)
    alert_db_pool_saturation_pct: float = Field(default=90.0, gt=0, le=100)
    # Leak heuristic: RSS rising (almost) every sample by at least this much.
    alert_rss_growth_mb: float = Field(default=256.0, gt=0)
    alert_rss_growth_min_samples: int = Field(default=24, ge=3)
    # Azure Insights rules (evaluated only while AZURE_INSIGHTS_ENABLED, from
    # the stored Azure data): peak 1-min tokens / requests or regional quota
    # at or above this % of the Azure-reported limits; a source failing this
    # many consecutive runs.
    alert_azure_quota_pct: float = Field(default=85.0, gt=0, le=100)
    alert_azure_quota_window_minutes: int = Field(default=15, ge=1, le=1440)
    alert_azure_sync_failures: int = Field(default=3, ge=1)
    # SMTP delivery: off unless SMTP_HOST, ALERT_EMAIL_FROM and ALERT_EMAIL_TO
    # are all set. SMTP_SSL = implicit TLS (465); otherwise STARTTLS (587).
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_timeout_seconds: float = Field(default=15.0, gt=0)
    alert_email_from: str = ""
    # Comma-separated recipients.
    alert_email_to: str = ""

    # --- Azure Insights (app/azure_insights): free Foundry / Azure data ---
    # Off = nothing is polled and the console shows "Not connected". When on,
    # every id below is required (a separate app registration from ENTRA_*,
    # read-only roles only). The secret has no default and is never logged.
    azure_insights_enabled: bool = False
    azure_tenant_id: str | None = None
    azure_client_id: str | None = None
    azure_client_secret: SecretStr | None = None
    azure_subscription_id: str | None = None
    azure_resource_group: str | None = None
    azure_openai_resource_name: str | None = None
    azure_openai_region: str | None = None
    # Second retail currency (USD is always fetched: model prices are USD).
    azure_pricing_currency: str = Field(default="AUD", pattern=r"^[A-Z]{3}$")
    azure_deployment_type: Literal["Global", "DataZone", "Regional"] = "Global"
    # Retail lookups need no credentials; this region is used while
    # AZURE_OPENAI_REGION is unset (Global meters are listed there).
    azure_retail_fallback_region: str = Field(
        default="eastus2", pattern=r"^[a-z0-9]{2,40}$"
    )
    # Model names priced by the retail sync (prompt/cached/output, embeddings).
    azure_retail_llm_model: str = "gpt-5-mini"
    azure_retail_embedding_model: str = "text-embedding-3-small"
    # Poll intervals (seconds); every result is stored with its history.
    azure_metrics_interval_seconds: int = Field(default=900, ge=300)
    azure_costs_interval_seconds: int = Field(default=86_400, ge=21_600)
    azure_capacity_interval_seconds: int = Field(default=21_600, ge=3_600)
    azure_health_interval_seconds: int = Field(default=600, ge=300)
    azure_service_health_interval_seconds: int = Field(default=1_800, ge=600)
    azure_retail_interval_seconds: int = Field(default=604_800, ge=86_400)
    # Hard stop at 10% of the 1M free metrics calls per month (never billed).
    azure_metrics_monthly_call_cap: int = Field(default=100_000, ge=0, le=1_000_000)
    azure_service_health_enabled: bool = False
    azure_advisor_enabled: bool = False
    azure_http_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    # Interactive "Sync official prices" calls per admin per minute.
    azure_retail_sync_rate_limit_per_minute: int = Field(default=5, ge=1)
    # Governance > Monitoring "Azure vs AskDIQ": rows whose drift (|Azure -
    # AskDIQ| / Azure) is at or above this percentage are highlighted.
    azure_reconciliation_drift_pct: float = Field(default=10.0, gt=0, le=1000)

    # Advisor recommendations (AZURE_ADVISOR_ENABLED): daily by default.
    azure_advisor_interval_seconds: int = Field(default=86_400, ge=21_600)

    # --- Auth: SSO (Entra OIDC), sessions and RBAC (PR-06) ---
    # Required unless AUTH_DEV_MOCK=true (see `_validate_auth`).
    # Each also accepts the generic OIDC_* names other Exigo apps use, so one .env works for both.
    # ENTRA_TENANT_ID may be omitted when OIDC_DISCOVERY_URL contains the tenant.
    entra_tenant_id: str | None = Field(
        default=None, validation_alias=AliasChoices("ENTRA_TENANT_ID", "OIDC_TENANT_ID")
    )
    entra_client_id: str | None = Field(
        default=None, validation_alias=AliasChoices("ENTRA_CLIENT_ID", "OIDC_CLIENT_ID")
    )
    entra_client_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ENTRA_CLIENT_SECRET", "OIDC_CLIENT_SECRET"),
    )
    # Optional: defaults to APP_BASE_URL + /auth/callback.
    entra_redirect_uri: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ENTRA_REDIRECT_URI", "OIDC_REDIRECT_URI"),
    )
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
    # Optional explicit discovery document URL, e.g.
    # https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration
    # Empty = built from ENTRA_AUTHORITY_HOST + ENTRA_TENANT_ID. Ignored when AUTH_DEV_MOCK=true.
    # The issuer in the document is still pinned to ENTRA_TENANT_ID.
    oidc_discovery_url: str = ""
    oidc_http_timeout_seconds: float = Field(default=10.0, gt=0)
    oidc_state_ttl_seconds: int = Field(default=600, ge=60)
    oidc_metadata_cache_seconds: int = Field(default=3600, ge=60)
    oidc_clock_skew_seconds: int = Field(default=60, ge=0, le=300)
    # Per-client-IP cap on /auth/sso/login + /auth/sso/callback (OWASP A07).
    auth_login_rate_limit_per_minute: int = Field(default=10, ge=1)

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
            if not self.entra_tenant_id and self.oidc_discovery_url:
                found = re.search(
                    r"^https://[^/]+/([^/]+)/v2\.0/\.well-known/openid-configuration$",
                    self.oidc_discovery_url.strip(),
                )
                if found:
                    self.entra_tenant_id = found.group(1)
            missing = [
                name.upper()
                for name in (
                    "entra_tenant_id",
                    "entra_client_id",
                    "entra_client_secret",
                )
                if not _has_value(getattr(self, name))
            ]
            if missing:
                raise ValueError(
                    f"Missing SSO settings: {', '.join(missing)} "
                    "(ENTRA_* or OIDC_* names; the tenant may come from "
                    "OIDC_DISCOVERY_URL; or set AUTH_DEV_MOCK=true with ENV=development)."
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

    @model_validator(mode="after")
    def _validate_alert_email(self) -> Self:
        # Header-injection guard: addresses end up in From/To headers.
        for name in ("alert_email_from", "alert_email_to", "smtp_host"):
            if re.search(r"[\r\n]", getattr(self, name)):
                raise ValueError(f"{name.upper()} must not contain line breaks.")
        bad = [a for a in self.alert_email_recipients if not _EMAIL_RE.match(a)]
        if bad or (
            self.alert_email_from and not _EMAIL_RE.search(self.alert_email_from)
        ):
            raise ValueError(
                "ALERT_EMAIL_FROM / ALERT_EMAIL_TO must be email addresses."
            )
        return self

    @model_validator(mode="after")
    def _validate_azure_insights(self) -> Self:
        """When enabled, every id must be present and URL-safe (they are
        interpolated into ARM paths); disabled, nothing is checked."""
        if not self.azure_insights_enabled:
            return self
        missing = [
            name.upper()
            for name in _AZURE_INSIGHTS_REQUIRED
            if not _has_value(getattr(self, name))
        ]
        if missing:
            raise ValueError(
                f"AZURE_INSIGHTS_ENABLED=true but missing: {', '.join(missing)} "
                "(see foundry-usage-data-review.md, 'How to enable')."
            )
        bad = [
            name.upper()
            for name, pattern in _AZURE_ID_PATTERNS.items()
            if not re.fullmatch(pattern, str(getattr(self, name)).strip())
        ]
        if bad:
            raise ValueError(f"Invalid Azure Insights identifiers: {', '.join(bad)}.")
        return self

    @property
    def azure_insights_configured(self) -> bool:
        """Enabled (the validator guarantees every id is then present)."""
        return self.azure_insights_enabled

    @property
    def azure_account_resource_id(self) -> str | None:
        """ARM id of the Cognitive Services (Foundry) account, or None."""
        if not self.azure_insights_configured:
            return None
        return (
            f"/subscriptions/{self.azure_subscription_id}"
            f"/resourceGroups/{self.azure_resource_group}"
            "/providers/Microsoft.CognitiveServices/accounts/"
            f"{self.azure_openai_resource_name}"
        )

    @property
    def azure_retail_region(self) -> str:
        return (self.azure_openai_region or self.azure_retail_fallback_region).strip()

    @property
    def alert_email_recipients(self) -> list[str]:
        return [a.strip() for a in self.alert_email_to.split(",") if a.strip()]

    @property
    def alert_email_configured(self) -> bool:
        """Email delivery is on only when host, sender and recipients are set."""
        return bool(
            self.smtp_host.strip()
            and self.alert_email_from.strip()
            and self.alert_email_recipients
        )

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


_AZURE_INSIGHTS_REQUIRED = (
    "azure_tenant_id",
    "azure_client_id",
    "azure_client_secret",
    "azure_subscription_id",
    "azure_resource_group",
    "azure_openai_resource_name",
    "azure_openai_region",
)
_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_AZURE_ID_PATTERNS = {
    "azure_tenant_id": rf"{_GUID}|[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+",
    "azure_client_id": _GUID,
    "azure_subscription_id": _GUID,
    "azure_resource_group": r"[-\w.()]{1,90}",
    "azure_openai_resource_name": r"[A-Za-z0-9][A-Za-z0-9-]{1,63}",
    "azure_openai_region": r"[a-z0-9]{2,40}",
}


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
