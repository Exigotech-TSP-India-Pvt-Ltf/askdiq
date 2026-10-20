/**
 * Azure Insights (free Microsoft Foundry / Azure data) API client.
 *
 * Mirrors `backend/app/azure_insights/schemas.py`. Every read is a stored
 * snapshot (never a live Azure call); keys start with `admin/azure` so the
 * console live stream (`azure.*` scopes, `lib/live-updates.ts`) refetches
 * them after each background poll.
 */
import { ApiError } from "@/lib/api";
import type { PriceField } from "@/lib/settings-api";

const API_BASE_URL = "/api/proxy";

export type AzureSourceState =
  "ok" | "error" | "throttled" | "budget_exhausted" | "unsupported" | "never_run" | "not_configured";

export interface AzureSourceStatus {
  source: string;
  label: string;
  scheduled: boolean;
  needs_credentials: boolean;
  interval_seconds: number;
  state: AzureSourceState;
  last_run_at: string | null;
  last_success_at: string | null;
  last_error_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  consecutive_failures: number;
  next_run_at: string | null;
  calls_this_month: number;
  call_cap: number | null;
}

export interface AzureStatus {
  connected: boolean;
  reason: "not_configured" | null;
  region: string | null;
  deployment_type: string;
  metrics_calls_this_month: number;
  metrics_call_cap: number;
  sources: AzureSourceStatus[];
  generated_at: string;
}

export type PriceRole = "prompt" | "cached" | "output" | "embed";

export interface OfficialPrice {
  role: PriceRole;
  field: PriceField;
  model_key: string;
  meter_name: string | null;
  found: boolean;
  usd_per_million: number | null;
  local_per_million: number | null;
  effective_date: string | null;
}

export interface OfficialPrices {
  source: "azure_retail";
  region: string;
  deployment_type: string;
  local_currency: string;
  synced_at: string | null;
  effective_date: string | null;
  prices: OfficialPrice[];
}

export const azureKeys = {
  status: "admin/azure/status",
  officialPrices: "admin/azure/retail-prices",
} as const;

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...init.headers },
  });
  if (!res.ok) {
    let detail = res.statusText || `Request failed (${res.status})`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // no JSON body
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

export const azureApi = {
  status: (signal?: AbortSignal) => request<AzureStatus>("/admin/azure/status", { signal }),
  officialPrices: (signal?: AbortSignal) => request<OfficialPrices>("/admin/models/prices/official", { signal }),
  /** Fetches the official prices from Azure now (public API) and returns them; saves nothing. */
  syncOfficialPrices: () => request<OfficialPrices>("/admin/models/prices/sync", { method: "POST" }),
};

/** Saved prices further than this from the official price get a drift warning. */
export const PRICE_DRIFT_THRESHOLD = 0.01;

/** Relative difference of `saved` from `official` (null when there is no official price). */
export function priceDrift(saved: number, official: number | null): number | null {
  if (official === null) return null;
  if (official === 0) return saved === 0 ? 0 : Number.POSITIVE_INFINITY;
  return Math.abs(saved - official) / official;
}

export function hasDrift(saved: number, official: number | null): boolean {
  const drift = priceDrift(saved, official);
  return drift !== null && drift > PRICE_DRIFT_THRESHOLD;
}
