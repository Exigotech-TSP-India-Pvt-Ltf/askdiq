"use client";

/** Warms the Azure Insights summary (Governance > Monitoring tab intent / idle). */
import { azureApi, azureKeys } from "@/lib/azure-metrics-api";
import { prefetch } from "@/lib/query-cache";

export function prefetchAzureSummary(token: string | null): void {
  if (!token) return;
  void prefetch(azureKeys.summary(), (signal) => azureApi.summary(token, undefined, signal));
}
