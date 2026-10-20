/**
 * Azure Insights reads (`/admin/azure/*`): free Azure Monitor platform
 * metrics of the Foundry / Azure OpenAI account, reconciled with what
 * AskDIQ recorded. The backend serves them from snapshots recomputed after
 * every background poll, so a page view never calls Azure; the console live
 * stream announces `azure.*` and every `admin/azure/...` key refetches.
 */
import { ApiError } from "@/lib/api";

const PROXY_BASE = "/api/proxy/admin";

/** Days of per-day reconciliation the console shows (the backend default). */
export const AZURE_SUMMARY_DAYS = 7;

export type AzureSourceState =
  "ok" | "error" | "throttled" | "budget_exhausted" | "unsupported" | "never_run" | "not_configured";
export type DeploymentRole = "llm" | "embedding" | "other";
export type MetricsRange = "1h" | "6h" | "24h" | "7d" | "30d" | "90d";
export type MetricsGrain = "PT1M" | "PT1H";

export interface ReconciliationCell {
  azure: number | null;
  askdiq: number | null;
  delta: number | null;
  drift_pct: number | null;
  drifted: boolean;
}

export interface ReconciliationRow {
  deployment: string;
  role: DeploymentRole;
  /** UTC day (YYYY-MM-DD); null for the last-24-hours row. */
  day: string | null;
  requests: ReconciliationCell;
  tokens_in: ReconciliationCell;
  tokens_out: ReconciliationCell;
  throttled: ReconciliationCell;
  latency_avg_ms: ReconciliationCell;
  latency_max_ms: ReconciliationCell;
  ttft_avg_ms: number | null;
  availability_pct: number | null;
  estimated_cost_usd: number | null;
  not_seen_tokens: number | null;
  drifted: boolean;
}

export interface DeploymentTokens {
  deployment: string;
  role: DeploymentRole;
  azure_tokens: number | null;
  azure_tokens_in: number | null;
  azure_tokens_out: number | null;
  askdiq_tokens: number | null;
  not_seen_tokens: number | null;
  not_seen_pct: number | null;
}

export interface AzureMetricsSummary {
  connected: boolean;
  state: AzureSourceState;
  as_of: string | null;
  last_point_at: string | null;
  days: number;
  drift_threshold_pct: number;
  llm_deployment: string;
  embedding_deployment: string;
  last_24h: ReconciliationRow[];
  daily: ReconciliationRow[];
  tokens: DeploymentTokens[];
  preview_cost: { enabled: boolean; empty_since: string | null; last_seen: string | null };
  generated_at: string;
}

export interface SeriesPoint {
  t: string;
  total: number | null;
  average: number | null;
  maximum: number | null;
}

export interface AzureMetricSeries {
  connected: boolean;
  as_of: string | null;
  metric: string;
  deployment: string;
  range: MetricsRange;
  grain: MetricsGrain;
  since: string;
  until: string;
  series: { deployment: string; status_code: string; points: SeriesPoint[] }[];
  generated_at: string;
}

export interface SeriesQuery {
  metric: string;
  deployment: string;
  range: MetricsRange;
  grain: MetricsGrain;
}

/** The Usage & Cost sparkline: hourly tokens per deployment, last 7 days. */
export const TOKENS_SERIES: SeriesQuery = { metric: "TokenTransaction", deployment: "*", range: "7d", grain: "PT1H" };

export const azureKeys = {
  summary: (days: number = AZURE_SUMMARY_DAYS) => ["admin/azure/metrics/summary", days] as const,
  series: (q: SeriesQuery) => ["admin/azure/metrics/series", q.metric, q.deployment, q.range, q.grain] as const,
};

async function getJson<T>(path: string, token: string | null, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${PROXY_BASE}${path}`, {
    signal,
    headers: { Accept: "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
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

export const azureApi = {
  summary: (token: string | null, days: number = AZURE_SUMMARY_DAYS, signal?: AbortSignal) =>
    getJson<AzureMetricsSummary>(`/azure/metrics/summary?days=${days}`, token, signal),
  series: (token: string | null, q: SeriesQuery, signal?: AbortSignal) => {
    const qs = new URLSearchParams({ metric: q.metric, deployment: q.deployment, range: q.range, grain: q.grain });
    return getJson<AzureMetricSeries>(`/azure/metrics/series?${qs.toString()}`, token, signal);
  },
};

/** The summary when the payload has its shape (an older backend or a stub may not). */
export function validSummary(data: unknown): AzureMetricsSummary | undefined {
  if (!data || typeof data !== "object") return undefined;
  const s = data as Partial<AzureMetricsSummary>;
  return Array.isArray(s.daily) && Array.isArray(s.last_24h) && Array.isArray(s.tokens) ? (s as AzureMetricsSummary) : undefined;
}
