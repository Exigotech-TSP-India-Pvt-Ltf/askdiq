/**
 * Typed client for `/admin/governance/*` (newplan §4.5, planchanges4 F4).
 *
 * Shapes mirror `backend/app/schemas/governance.py` and
 * `backend/app/api/routes/admin_governance.py` exactly. Every latency is in
 * milliseconds (`latency_unit: "ms"`); formatting to ms / s happens only in
 * `lib/governance-format.ts`.
 */
import { ApiError } from "@/lib/api";
import type { Audience } from "@/lib/types";

const PROXY_BASE = "/api/proxy/admin";

// ---------------------------------------------------------------------------
// Enums (StrEnum values on the backend)
// ---------------------------------------------------------------------------

export const LOG_LEVELS = ["CRITICAL", "ALERT", "ERROR", "WARNING", "INFO"] as const;
export type LogLevel = (typeof LOG_LEVELS)[number];

export const LOG_SERVICES = ["auth", "query", "ingest", "feedback", "admin", "system"] as const;
export type LogService = (typeof LOG_SERVICES)[number];

export const TRACE_STATUSES = ["ok", "ungrounded", "blocked", "failed", "unknown"] as const;
export type TraceStatus = (typeof TRACE_STATUSES)[number];

export const TRACE_OUTCOMES = ["answered", "ungrounded", "blocked", "conversational", "llm_failed", "clarify"] as const;
export type TraceOutcome = (typeof TRACE_OUTCOMES)[number];

export const LATENCY_BUCKETS = ["fast", "moderate", "slow", "very_slow"] as const;
export type LatencyBucket = (typeof LATENCY_BUCKETS)[number];

export const HEALTH_STATES = ["Healthy", "Degraded", "Incident"] as const;
export type HealthState = (typeof HEALTH_STATES)[number];

// ---------------------------------------------------------------------------
// Logs
// ---------------------------------------------------------------------------

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export interface GovernanceLogItem {
  id: string;
  created_at: string;
  level: LogLevel;
  service: LogService;
  action: string;
  summary: string;
  detail: Record<string, JsonValue>;
  /** Deprecated legacy JSON string; use `summary` / `detail`. */
  message?: string;
  user_id: string | null;
  ip_address: string | null;
  request_id: string | null;
}

export interface CursorPage<T> {
  items: T[];
  total: number;
  total_estimate: number | null;
  total_is_estimate: boolean;
  limit: number;
  next_cursor: string | null;
  has_more: boolean;
}

export interface GovernanceLogPage extends CursorPage<GovernanceLogItem> {
  level_counts: Record<string, number>;
  level_counts_capped: boolean;
}

export interface LogFilters {
  level?: readonly LogLevel[];
  service?: readonly LogService[];
  q?: string;
  from?: string;
  to?: string;
}

// ---------------------------------------------------------------------------
// Traces
// ---------------------------------------------------------------------------

export interface StageTiming {
  stage: string;
  ms: number;
  unit: string;
}

export interface TraceTokens {
  prompt: number | null;
  completion: number | null;
  reasoning: number | null;
  embedding: number | null;
  total: number | null;
}

export interface GovernanceTraceItem {
  request_id: string;
  user_id: string | null;
  conversation_id: string | null;
  ip_address: string | null;
  timestamp: string;
  status: TraceStatus;
  outcome: string | null;
  guardrail_flags: string[];
  total_ms: number;
  latency_unit: string;
  latency_bucket: LatencyBucket | null;
  slowest_stage: StageTiming | null;
  query: string | null;
  tokens: TraceTokens;
  cost_usd: number | null;
}

export type GovernanceTracePage = CursorPage<GovernanceTraceItem>;

export interface WaterfallStage {
  stage: string;
  start_ms: number | null;
  duration_ms: number | null;
}

export interface TraceWaterfall {
  unit: string;
  total_ms: number;
  stages: WaterfallStage[];
  other_ms: number;
}

export interface TraceSource {
  chunk_id: string;
  document_id: string | null;
  source_name: string | null;
  score: number | null;
}

export interface GovernanceTraceDetail extends GovernanceTraceItem {
  grounded: boolean | null;
  stages: TraceWaterfall;
  sources: TraceSource[];
  flags: string[];
}

export interface TraceFilters {
  status?: readonly TraceStatus[];
  outcome?: readonly TraceOutcome[];
  latency_bucket?: readonly LatencyBucket[];
  q?: string;
  from?: string;
  to?: string;
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

export interface HealthSignal {
  value: number | null;
  unit: string;
  status: HealthState;
  degraded_at: number;
  incident_at: number;
}

export interface HealthStatus {
  status: HealthState;
  window_minutes: number;
  signals: Record<string, HealthSignal>;
}

export interface Kpi {
  value: number | null;
  unit: string | null;
  /** 24 hourly points, oldest first, aligned with `GovernanceOverview.hours`. */
  sparkline: (number | null)[];
  window: string;
  source: string;
}

export const KPI_KEYS = [
  "requests",
  "error_rate",
  "latency_p50",
  "latency_p95",
  "active_users",
  "groundedness",
  "blocked_or_flagged",
  "cost_today",
] as const;
export type KpiKey = (typeof KPI_KEYS)[number];

export type OnlineQualityKpis = Partial<{
  sampled_count: number;
  violation_count: number;
  violation_rate: number;
  /** null until something has been sampled. */
  mean_faithfulness: number | null;
  secret_leak_count: number;
  pii_leak_count: number;
  unfaithful_count: number;
}>;

export interface GovernanceOverview {
  audience: string;
  window_hours: number;
  hours: string[];
  health: HealthStatus;
  kpis: Partial<Record<KpiKey, Kpi>> & Record<string, Kpi>;
  incidents: GovernanceLogItem[];
  online_quality_kpis: OnlineQualityKpis;
}

// ---------------------------------------------------------------------------
// Monitoring
// ---------------------------------------------------------------------------

export const STAGES = ["embedding", "retrieval", "reranker", "llm", "guardrail_pre", "guardrail_post"] as const;
export type Stage = (typeof STAGES)[number];

export interface StageLatency {
  avg_ms: number | null;
  sample_count: number;
  unit: string;
}

export interface StagePercentiles {
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  sample_count: number;
  unit: string;
}

export interface HourlyBucket {
  hour: string;
  requests: number;
  errors: number;
  error_rate: number;
  latency_samples: number;
  latency_avg_ms: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
}

export interface LabelledCount {
  value: number;
  window: string;
  source: string;
}

export interface QuotaMetrics {
  tpm_current?: number;
  tpm_limit?: number;
  tpm_headroom_pct?: number | null;
  rpm_current?: number;
  rpm_limit?: number;
  /** "azure": the deployment's real TPM/RPM (Azure Insights capacity poll); "settings": AZURE_OPENAI_*_LIMIT. */
  tpm_limit_source?: "azure" | "settings";
  rpm_limit_source?: "azure" | "settings";
  rpm_headroom_pct?: number | null;
  azure_429_count_60s?: number;
}

export interface AzureQuotaTelemetry {
  chat?: QuotaMetrics;
  embedding?: QuotaMetrics;
  any_high_utilization?: boolean;
}

export interface QuotaPoint {
  ts: string;
  utilization_pct: number;
  chat_tpm_headroom_pct: number | null;
  chat_rpm_headroom_pct: number | null;
  embedding_tpm_headroom_pct: number | null;
  embedding_rpm_headroom_pct: number | null;
  azure_429_count_60s: number;
}

export type ResourceScope = "host" | "container" | "process";

/** One point of the 15-minute resource history (one per sample interval). */
export interface ResourcePoint {
  ts: string;
  cpu_percent: number | null;
  memory_percent: number | null;
  rss_mb: number | null;
  loop_lag_p95_ms: number | null;
  db_pool_percent: number | null;
}

/** Aggregate of every live API worker (`resources` in monitoring and SSE). */
export interface ResourcesSnapshot {
  sampled_at: string | null;
  age_seconds: number | null;
  interval_seconds: number;
  workers_live: number;
  /** Container value when the container is limited, else the server's. */
  effective: {
    cpu_percent: number | null;
    cpu_scope: "host" | "container";
    memory_percent: number | null;
    memory_scope: "host" | "container";
  };
  host: { scope: "host"; cpu_percent: number | null; memory_percent: number | null; memory_total_mb: number | null; hosts: number };
  container: {
    scope: "container";
    cpu_percent: number | null;
    memory_percent: number | null;
    memory_mb: number | null;
    memory_limit_mb: number | null;
  } | null;
  process: { scope: "process"; rss_mb: number | null; cpu_percent: number | null };
  event_loop: { scope: "process"; lag_p95_ms: number | null; lag_max_ms: number | null };
  db_pool: { scope: "process"; in_use_percent: number | null; checked_out: number; capacity: number };
  active_queries: number;
  workers: Array<{
    worker_id: string;
    hostname: string;
    pid: number | null;
    last_seen: string;
    rss_mb: number | null;
    cpu_percent: number | null;
    loop_lag_p95_ms: number | null;
    db_pool_checked_out: number | null;
    active_queries: number | null;
  }>;
  history: ResourcePoint[];
}

export interface GovernanceMonitoring {
  window_hours: number;
  latency_unit: string;
  api_requests: number;
  queries_24h: number;
  active_users: number;
  active_queries: number;
  avg_latency_ms: number | null;
  total_latency_ms: number | null;
  p95_latency_ms: number | null;
  error_rate: number;
  error_counts: Record<string, number | string>;
  traces_total: number;
  stage_latency: Partial<Record<Stage, StageLatency>>;
  stage_percentiles: Partial<Record<Stage | "total", StagePercentiles>>;
  hourly: HourlyBucket[];
  guardrail_flag_breakdown: Record<string, number>;
  outcome_mix: Record<string, number>;
  counts: Record<string, LabelledCount>;
  process_cpu_percent: number | null;
  process_ram_mb: number | null;
  process_ram_percent: number | null;
  /** Shared resource telemetry; absent from older backends. */
  resources?: Partial<ResourcesSnapshot>;
  azure_quota_telemetry: AzureQuotaTelemetry;
  azure_quota_series: QuotaPoint[];
  online_quality_kpis: OnlineQualityKpis;
}

// ---------------------------------------------------------------------------
// SSE events (`GET /admin/governance/stream`)
// ---------------------------------------------------------------------------

export const STREAM_EVENTS = ["snapshot", "health", "incident", "monitoring", "resources"] as const;
export type StreamEventType = (typeof STREAM_EVENTS)[number];

export type GovernanceStreamEvent =
  | { type: "snapshot"; data: GovernanceOverview }
  | { type: "health"; data: { status: HealthState; previous: HealthState; health: HealthStatus } }
  | { type: "incident"; data: GovernanceLogItem }
  | { type: "monitoring"; data: { kpis: Record<string, number | null> } }
  | { type: "resources"; data: Partial<ResourcesSnapshot> };

// ---------------------------------------------------------------------------
// Query strings
// ---------------------------------------------------------------------------

type ParamValue = string | number | readonly string[] | undefined | null;

/** Builds a query string; arrays repeat the key (`level=ERROR&level=ALERT`). */
export function buildQuery(params: Record<string, ParamValue>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const v of value as readonly string[]) qs.append(key, v);
    } else {
      qs.set(key, String(value));
    }
  }
  const out = qs.toString();
  return out ? `?${out}` : "";
}

/** The backend reads `overview` as "no audience filter"; omit it like `lib/api.ts`. */
function audienceParam(audience: Audience | undefined): string | undefined {
  return audience && audience !== "overview" ? audience : undefined;
}

async function getJson<T>(path: string, token: string | null, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${PROXY_BASE}${path}`, {
    signal,
    headers: {
      Accept: "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText || `Request failed (${res.status})`;
    try {
      const body = (await res.json()) as { detail?: unknown; title?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (typeof body.title === "string") detail = body.title;
    } catch {
      // no JSON body
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

/** Shared query-cache keys (the page and the dashboard's intent prefetch use the same entry). */
export const governanceKeys = {
  overview: (audience: Audience) => ["admin/governance/overview", audience] as const,
  monitoring: (audience: Audience) => ["admin/governance/monitoring", audience] as const,
  trace: (requestId: string, audience: Audience) => ["admin/governance/trace", requestId, audience] as const,
  /** Identity of a Traces table view (filters + page size); the cursor stack resets when it changes. */
  tracesFilter: (p: {
    audience: Audience;
    status?: string;
    outcome?: string;
    latencyBucket?: string;
    q: string;
    from?: string;
    to?: string;
    pageSize: number;
  }) => [p.audience, p.status, p.outcome, p.latencyBucket, p.q, p.from, p.to, p.pageSize].join("|"),
  traces: (filterKey: string, cursor: string | null) => ["admin/governance/traces", filterKey, cursor] as const,
  logsFilter: (p: {
    audience: Audience;
    levels: readonly string[];
    service?: string;
    q: string;
    from?: string;
    to?: string;
    pageSize: number;
  }) => [p.audience, p.levels.join(","), p.service, p.q, p.from, p.to, p.pageSize].join("|"),
  logs: (filterKey: string, cursor: string | null) => ["admin/governance/logs", filterKey, cursor] as const,
};

export const governanceApi = {
  overview: (token: string | null, audience: Audience, signal?: AbortSignal) =>
    getJson<GovernanceOverview>(
      `/governance/overview${buildQuery({ audience: audienceParam(audience) })}`,
      token,
      signal,
    ),

  monitoring: (token: string | null, audience: Audience, signal?: AbortSignal) =>
    getJson<GovernanceMonitoring>(
      `/governance/monitoring${buildQuery({ audience: audienceParam(audience) })}`,
      token,
      signal,
    ),

  traces: (
    token: string | null,
    params: TraceFilters & { audience: Audience; cursor?: string | null; limit?: number },
    signal?: AbortSignal,
  ) =>
    getJson<GovernanceTracePage>(
      `/governance/traces/page${buildQuery({
        audience: audienceParam(params.audience),
        limit: params.limit,
        cursor: params.cursor,
        status: params.status,
        outcome: params.outcome,
        latency_bucket: params.latency_bucket,
        q: params.q?.trim(),
        from: params.from,
        to: params.to,
      })}`,
      token,
      signal,
    ),

  trace: (token: string | null, requestId: string, audience: Audience, signal?: AbortSignal) =>
    getJson<GovernanceTraceDetail>(
      `/governance/traces/${encodeURIComponent(requestId)}${buildQuery({ audience: audienceParam(audience) })}`,
      token,
      signal,
    ),

  logs: (
    token: string | null,
    params: LogFilters & { audience: Audience; cursor?: string | null; limit?: number },
    signal?: AbortSignal,
  ) =>
    getJson<GovernanceLogPage>(`/governance/logs${logsQuery(params)}`, token, signal),
};

function logsQuery(params: LogFilters & { audience: Audience; cursor?: string | null; limit?: number }): string {
  return buildQuery({
    audience: audienceParam(params.audience),
    limit: params.limit,
    cursor: params.cursor,
    level: params.level,
    service: params.service,
    q: params.q?.trim(),
    from: params.from,
    to: params.to,
  });
}

/** Same-origin URL of the streamed CSV export for the current log filters. */
export function logsExportUrl(params: LogFilters & { audience: Audience }): string {
  return `${PROXY_BASE}/governance/logs/export.csv${logsQuery(params)}`;
}

/** Same-origin URL of the SSE stream (cookies travel with EventSource). */
export function governanceStreamUrl(audience: Audience): string {
  return `${PROXY_BASE}/governance/stream${buildQuery({ audience: audienceParam(audience) })}`;
}
