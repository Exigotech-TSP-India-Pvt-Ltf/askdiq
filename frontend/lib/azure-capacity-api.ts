"use client";

/**
 * Azure Insights Phase 4: capacity / regional quota, Azure health (Resource
 * Health + Service Health) and Advisor. Every read is a precomputed snapshot
 * (`/admin/azure/*`, ETag / 304); the backend polls Azure on a schedule and
 * announces `azure.*` on the live stream, which refetches these keys
 * (`admin/azure/...`). Pages never wait on Azure.
 */
import { ApiError } from "@/lib/api";
import { prefetch } from "@/lib/query-cache";

const PROXY_BASE = "/api/proxy/admin/azure";

export type LimitSource = "azure" | "settings";

export interface DeploymentCapacity {
  name: string;
  model_name?: string | null;
  model_version?: string | null;
  sku_name?: string | null;
  sku_capacity?: number | null;
  current_capacity?: number | null;
  tpm_limit?: number | null;
  rpm_limit?: number | null;
  provisioning_state?: string | null;
  role?: "chat" | "embedding" | null;
}

export interface RegionalQuotaItem {
  name: string;
  label: string;
  current_value?: number | null;
  limit_value?: number | null;
  unit?: string | null;
  pct?: number | null;
}

export type RegionalState = "ok" | "forbidden" | "error" | "never_run";

export interface RegionalQuota {
  state: RegionalState;
  location?: string | null;
  error_code?: string | null;
  fetched_at?: string | null;
  items?: RegionalQuotaItem[];
}

export interface AzureCapacity {
  connected?: boolean;
  fetched_at?: string | null;
  deployments?: DeploymentCapacity[];
  regional?: RegionalQuota;
  generated_at?: string;
}

export interface ResourceHealth {
  availability_state: string;
  summary?: string | null;
  reason_type?: string | null;
  since: string;
  checked_at: string;
  ended_at?: string | null;
  resolution_eta?: string | null;
  stale?: boolean;
}

export interface ServiceEvent {
  tracking_id: string;
  event_type?: string | null;
  status?: string | null;
  level?: string | null;
  title?: string | null;
  summary?: string | null;
  services?: string[];
  regions?: string[];
  impact_start?: string | null;
  last_update?: string | null;
  active: boolean;
}

export interface HealthBannerState {
  show: boolean;
  level?: "warning" | "critical" | null;
  title?: string | null;
  message?: string | null;
  href?: string;
  tracking_id?: string | null;
}

export interface AzureHealth {
  connected?: boolean;
  service_health_enabled?: boolean;
  banner?: HealthBannerState;
  resource?: ResourceHealth | null;
  history?: ResourceHealth[];
  events?: ServiceEvent[];
}

export interface AdvisorRecommendation {
  rec_id: string;
  category?: string | null;
  impact?: string | null;
  impacted_value?: string | null;
  problem?: string | null;
  solution?: string | null;
  learn_more_link?: string | null;
  retirement: boolean;
  retirement_date?: string | null;
}

export interface AzureAdvisor {
  connected?: boolean;
  enabled?: boolean;
  recommendations?: AdvisorRecommendation[];
}

async function getJson<T>(path: string, token: string | null, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${PROXY_BASE}${path}`, {
    signal,
    headers: { Accept: "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (!res.ok) throw new ApiError(res.statusText || `Request failed (${res.status})`, res.status);
  return (await res.json()) as T;
}

/** Query keys: the `azure.` live scope refreshes every `admin/azure` key. */
export const azureKeys = {
  capacity: () => ["admin/azure/capacity"] as const,
  health: () => ["admin/azure/health"] as const,
  advisor: () => ["admin/azure/advisor"] as const,
};

export const azureApi = {
  capacity: (token: string | null, signal?: AbortSignal) => getJson<AzureCapacity>("/capacity", token, signal),
  health: (token: string | null, signal?: AbortSignal) => getJson<AzureHealth>("/health", token, signal),
  advisor: (token: string | null, signal?: AbortSignal) => getJson<AzureAdvisor>("/advisor", token, signal),
};

/** Warms the console-wide banner (every admin page shows it). */
export function prefetchAzureHealth(token: string | null): Promise<unknown> | undefined {
  if (!token) return undefined;
  return prefetch(azureKeys.health(), (signal) => azureApi.health(token, signal));
}

/** Warms the Governance → Monitoring Azure panels. */
export function prefetchAzurePanels(token: string | null): Array<Promise<unknown>> {
  if (!token) return [];
  return [
    prefetch(azureKeys.capacity(), (signal) => azureApi.capacity(token, signal)),
    prefetch(azureKeys.health(), (signal) => azureApi.health(token, signal)),
    prefetch(azureKeys.advisor(), (signal) => azureApi.advisor(token, signal)),
  ];
}

const COMPACT = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

/** "250K" style limit numbers. */
export function compactLimit(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? COMPACT.format(value) : "—";
}

interface LimitMetrics {
  tpm_limit?: number | null;
  rpm_limit?: number | null;
  tpm_limit_source?: LimitSource | string | null;
  rpm_limit_source?: LimitSource | string | null;
}

/**
 * Gauge caption naming the denominator: "limit from Azure" when the
 * deployment's real TPM/RPM (rateLimits) is known, else the configured default.
 */
export function quotaLimitCaption(
  telemetry: { chat?: LimitMetrics; embedding?: LimitMetrics } | undefined,
  dimension: "tpm" | "rpm",
): string {
  const unit = dimension.toUpperCase();
  const parts: string[] = [];
  let fromAzure = false;
  for (const [label, metrics] of [
    ["chat", telemetry?.chat],
    ["embedding", telemetry?.embedding],
  ] as const) {
    const limit = metrics?.[`${dimension}_limit`];
    if (typeof limit !== "number") continue;
    if (metrics?.[`${dimension}_limit_source`] === "azure") fromAzure = true;
    parts.push(`${label} ${compactLimit(limit)}`);
  }
  if (parts.length === 0) return "Busiest of chat and embedding";
  return `${fromAzure ? "Limit from Azure" : "Configured limit"} · ${unit} ${parts.join(" / ")}`;
}

/** True when any gauge denominator came from Azure. */
export function usesAzureLimits(telemetry: { chat?: LimitMetrics; embedding?: LimitMetrics } | undefined): boolean {
  return [telemetry?.chat, telemetry?.embedding].some(
    (m) => m?.tpm_limit_source === "azure" || m?.rpm_limit_source === "azure",
  );
}

export const REGIONAL_STATE_COPY: Record<RegionalState, string> = {
  ok: "",
  forbidden:
    "Not permitted: grant the optional Cognitive Services Usages Reader role at subscription scope to see the regional quota.",
  error: "The last regional quota read failed; it is retried on the next poll.",
  never_run: "Not polled yet.",
};
