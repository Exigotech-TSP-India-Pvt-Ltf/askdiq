"use client";

/**
 * Shared bits of the Azure Insights panels: source-state pills ("Not
 * connected", "as of", throttled / cap reached) and pure helpers the panels
 * and their tests use.
 */
import React from "react";
import { StudioPill } from "@/components/ui/StudioCard";
import { RelativeTime } from "@/components/ui/RelativeTime";
import type { StudioTone } from "@/components/ui/styles";
import { formatCount, formatMs, formatPct } from "@/lib/governance-format";
import type { AzureMetricsSummary, AzureSourceState, DeploymentRole, ReconciliationCell } from "@/lib/azure-metrics-api";

export const NOT_CONNECTED_MESSAGE =
  "Not connected: Azure Insights is off (AZURE_INSIGHTS_ENABLED). See foundry-usage-data-review.md to enable it (free, read-only).";

export const ROLE_LABEL: Record<DeploymentRole, string> = {
  llm: "AskDIQ LLM",
  embedding: "AskDIQ embeddings",
  other: "Not used by AskDIQ",
};

const STATE_PILL: Partial<Record<AzureSourceState, { tone: StudioTone; label: string }>> = {
  error: { tone: "danger", label: "Last poll failed" },
  throttled: { tone: "warn", label: "Throttled by Azure" },
  budget_exhausted: { tone: "warn", label: "Monthly call cap reached" },
  never_run: { tone: "info", label: "Waiting for the first poll" },
};

/** True when the panel has nothing to show but the "Not connected" state. */
export function isNotConnected(summary: AzureMetricsSummary | undefined): boolean {
  if (!summary || summary.connected) return false;
  return !summary.daily.some((r) => r.requests.azure !== null || r.tokens_in.azure !== null);
}

/** "12.5%" / "—": a cell's drift. */
export function formatDrift(cell: ReconciliationCell): string {
  return cell.drift_pct === null ? "—" : formatPct(cell.drift_pct);
}

export function formatCell(value: number | null, kind: "count" | "ms" | "pct" = "count"): string {
  if (value === null) return "—";
  if (kind === "ms") return formatMs(value);
  if (kind === "pct") return formatPct(value);
  return formatCount(value);
}

/** Status pills next to a panel title: connection, poll state, data age. */
export function AzureStatePills({ summary }: { summary: AzureMetricsSummary | undefined }) {
  if (!summary) return null;
  if (!summary.connected) {
    return (
      <span data-testid="azure-not-connected">
        <StudioPill tone="warn">Not connected</StudioPill>
      </span>
    );
  }
  const pill = STATE_PILL[summary.state];
  return (
    <span className="flex flex-wrap items-center gap-2">
      {pill ? <StudioPill tone={pill.tone}>{pill.label}</StudioPill> : null}
      <span className="text-[11px] text-fg-subtle" data-testid="azure-as-of">
        <RelativeTime value={summary.as_of} prefix="as of " fallback="no data yet" />
      </span>
    </span>
  );
}

export function RoleChip({ role }: { role: DeploymentRole }) {
  const tone = role === "other" ? "text-fg-subtle" : "text-brand-600 dark:text-brand-300";
  return <span className={`text-[10px] font-semibold uppercase tracking-wide ${tone}`}>{ROLE_LABEL[role]}</span>;
}

/** "2031-03-10" -> "10 Mar" (UTC). */
export function dayLabel(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return day;
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

export const AzureNote = ({ children }: { children: React.ReactNode }) => (
  <p className="text-xs text-fg-subtle">{children}</p>
);
