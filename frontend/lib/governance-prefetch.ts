"use client";

/**
 * Governance prefetch: every tab's first view and the trace drawer are read
 * under the exact keys the views use, so a tab switch or a row click renders
 * from cache (the backend answers these from memory copies / precomputed
 * first pages).
 */
import { api } from "@/lib/api";
import { azureApi, azureKeys } from "@/lib/azure-api";
import { prefetchAzureSummary } from "@/lib/azure-prefetch";
import { prefetchAzurePanels } from "@/lib/azure-capacity-api";
import { governanceApi, governanceKeys, type GovernanceTraceItem } from "@/lib/governance-api";
import { rangeBounds } from "@/lib/governance-links";
import { prefetch, usePrefetchRows } from "@/lib/query-cache";
import type { Audience } from "@/lib/types";

/** Page sizes the Traces / Logs tables open with (TRACES_PAGE_SIZE / LOGS_PAGE_SIZE). */
export const FIRST_PAGE_SIZE = 10;
/** Trace details warmed ahead of the open row (drawer next / previous). */
const NEIGHBOURS = 2;

type Range = Parameters<typeof rangeBounds>[0];

export function prefetchTrace(token: string | null, requestId: string, audience: Audience): void {
  if (!token) return;
  void prefetch(governanceKeys.trace(requestId, audience), (signal) => governanceApi.trace(token, requestId, audience, signal));
}

/** The traces around `index` (drawer next / previous are instant). */
export function prefetchTraceNeighbours(
  token: string | null,
  items: readonly GovernanceTraceItem[],
  index: number,
  audience: Audience,
): void {
  for (let d = 1; d <= NEIGHBOURS; d++) {
    for (const i of [index + d, index - d]) {
      if (i >= 0 && i < items.length) prefetchTrace(token, items[i].request_id, audience);
    }
  }
}

export function prefetchMonitoring(token: string | null, audience: Audience): void {
  if (!token) return;
  void prefetch(governanceKeys.monitoring(audience), (signal) => governanceApi.monitoring(token, audience, signal));
  void prefetch(["admin/usage/llm"], () => api.adminGet(token, "/usage/llm"));
  void prefetch(["admin/usage/embedding"], () => api.adminGet(token, "/usage/embedding"));
  void prefetch(azureKeys.status, (signal) => azureApi.status(signal));
  prefetchAzureSummary(token);
  void Promise.allSettled(prefetchAzurePanels(token));
}

export function prefetchTracesFirstPage(token: string | null, audience: Audience, range: Range): void {
  if (!token) return;
  const { from, to } = rangeBounds(range);
  const filterKey = governanceKeys.tracesFilter({ audience, q: "", from, to, pageSize: FIRST_PAGE_SIZE });
  void prefetch(governanceKeys.traces(filterKey, null), (signal) =>
    governanceApi.traces(token, { audience, limit: FIRST_PAGE_SIZE, from, to }, signal),
  );
}

export function prefetchLogsFirstPage(token: string | null, audience: Audience, range: Range): void {
  if (!token) return;
  const { from, to } = rangeBounds(range);
  const filterKey = governanceKeys.logsFilter({ audience, levels: [], q: "", from, to, pageSize: FIRST_PAGE_SIZE });
  void prefetch(governanceKeys.logs(filterKey, null), (signal) =>
    governanceApi.logs(token, { audience, limit: FIRST_PAGE_SIZE, level: [], from, to }, signal),
  );
}

/** Every tab's first view (idle after the page opens, and on tab hover). */
export function prefetchGovernanceTab(tab: string, token: string | null, audience: Audience, range: Range): void {
  if (tab === "Monitoring") prefetchMonitoring(token, audience);
  else if (tab === "Traces") prefetchTracesFirstPage(token, audience, range);
  else if (tab === "Logs") prefetchLogsFirstPage(token, audience, range);
}

/**
 * Once idle, warms the drawer of every trace on screen with ONE request
 * (`GET /governance/traces/batch`); `version` = the page's `updatedAt`.
 */
export function usePrefetchTraceRows(
  token: string | null,
  items: readonly GovernanceTraceItem[] | null,
  audience: Audience,
  version = 0,
): void {
  usePrefetchRows(
    token && items ? items.map((t) => t.request_id) : null,
    (id) => governanceKeys.trace(id, audience),
    { batch: (ids, signal) => governanceApi.tracesBatch(token, ids, audience, signal) },
    { version },
  );
}
