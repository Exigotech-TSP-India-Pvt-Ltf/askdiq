/**
 * `GET /admin/azure/costs`: Azure billed cost vs AskDIQ's estimate
 * (backend `app/azure_insights/billed.py`, `cost_schemas.py`).
 *
 * Served from precomputed snapshots (never calls Azure on page load). The
 * daily background poll announces `azure.costs.billed` on `/admin/live`;
 * `lib/live-updates.ts` maps every `azure.*` scope to the `admin/azure`
 * query-key prefix, so the card refetches in place.
 */
import { ApiError } from "@/lib/api";
import { prefetch, runWhenIdle, useApiQuery } from "@/lib/query-cache";
import { RANGE_PRESETS, presetRange, rangeUtcDays } from "@/lib/range-context";

const PROXY_BASE = "/api/proxy/admin/azure";
/** Billed cost changes once a day; the estimate part moves with live chats. */
export const AZURE_COSTS_STALE_MS = 60_000;

export type AzureCostsState =
  | "ok"
  | "not_connected"
  | "never_run"
  | "unsupported"
  | "error"
  | "throttled"
  | "budget_exhausted";
export type BilledDayStatus = "final" | "estimated" | "pending" | "no_data";

export interface BilledDay {
  date: string;
  billed: number | null;
  estimate: number;
  estimate_usd: number;
  variance: number | null;
  variance_pct: number | null;
  status: BilledDayStatus;
  closed: boolean;
}

export interface CostTotals {
  billed: number | null;
  estimate: number;
  estimate_billed_days: number;
  variance: number | null;
  variance_pct: number | null;
  billed_days: number;
  final: boolean;
}

export interface AzureCosts {
  state: AzureCostsState;
  message: string | null;
  range: { preset: string | null; from_date: string; to_date: string };
  currency: string | null;
  estimate_currency: string;
  repriced: boolean;
  currency_mismatch: boolean;
  estimate_note?: string | null;
  days: BilledDay[];
  totals: CostTotals;
  month_to_date: CostTotals & { month: string };
  billed_through: string | null;
  drift_threshold_pct: number;
  drift_min: number;
  lag_note: string;
  last_success_at: string | null;
  next_run_at: string | null;
  as_of: string;
}

/** Inclusive UTC days of the shared range; empty = the backend default (30 days). */
export interface CostDays {
  from?: string;
  to?: string;
}

export const azureCostsKeys = {
  costs: (days: CostDays = {}) => ["admin/azure/costs", days.from ?? "", days.to ?? ""] as const,
};

export async function fetchAzureCosts(token: string, days: CostDays, signal?: AbortSignal): Promise<AzureCosts> {
  const qs = new URLSearchParams();
  if (days.from) qs.set("from_date", days.from);
  if (days.to) qs.set("to_date", days.to);
  const query = qs.toString();
  const res = await fetch(`${PROXY_BASE}/costs${query ? `?${query}` : ""}`, {
    signal,
    headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
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
  return (await res.json()) as AzureCosts;
}

/** Warms the card for `days` (route prefetch / idle preload). */
export function prefetchAzureCosts(token: string | null, days: CostDays): Promise<unknown> {
  if (!token) return Promise.resolve(undefined);
  return prefetch(azureCostsKeys.costs(days), (signal) => fetchAzureCosts(token, days, signal), AZURE_COSTS_STALE_MS);
}

export function useAzureCosts(token: string | null, days: CostDays) {
  const query = useApiQuery<AzureCosts>(
    token ? azureCostsKeys.costs(days) : null,
    (signal) => fetchAzureCosts(token ?? "", days, signal),
    { staleTime: AZURE_COSTS_STALE_MS, keepPreviousData: true },
  );
  const error = query.error;
  return {
    costs: query.data ?? null,
    isLoading: !token || query.isLoading,
    error: error ? (error instanceof Error && error.message ? error.message : "Failed to load billed cost.") : null,
    refresh: query.refetch,
  };
}

/** While idle, warms the card for every shared range preset (one click away). */
export function warmAzureCosts(token: string | null): () => void {
  if (!token) return () => undefined;
  return runWhenIdle(() => {
    for (const preset of RANGE_PRESETS) void prefetchAzureCosts(token, rangeUtcDays(presetRange(preset)));
  });
}
