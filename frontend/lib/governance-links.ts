/**
 * Deep links inside the Governance page. State lives in the URL so incident
 * markers, KPI cards and shared links open the right tab, pre-filtered.
 */
import type { LatencyBucket, LogLevel, LogService, TraceOutcome, TraceStatus } from "@/lib/governance-api";

export const GOVERNANCE_PATH = "/admin/governance";
export const GOVERNANCE_TAB_IDS = ["Overall", "Monitoring", "Traces", "Logs"] as const;
export type GovernanceTab = (typeof GOVERNANCE_TAB_IDS)[number];

export function isGovernanceTab(value: string | null): value is GovernanceTab {
  return value !== null && (GOVERNANCE_TAB_IDS as readonly string[]).includes(value);
}

/** URL params owned by one tab; cleared when switching tabs. */
export const TAB_PARAMS = ["request_id", "status", "outcome", "latency_bucket", "level", "service", "q", "at"] as const;

function href(params: Record<string, string | undefined>): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) qs.set(k, v);
  return `${GOVERNANCE_PATH}?${qs.toString()}`;
}

export interface LogsLink {
  level?: readonly LogLevel[];
  service?: LogService;
  q?: string;
  /** ISO start of an hour: shows that hour only. */
  at?: string;
}

export function logsHref({ level, service, q, at }: LogsLink = {}): string {
  return href({ tab: "Logs", level: level?.join(","), service, q, at });
}

export interface TracesLink {
  status?: TraceStatus;
  outcome?: TraceOutcome;
  latency_bucket?: LatencyBucket;
  q?: string;
}

export function tracesHref({ status, outcome, latency_bucket, q }: TracesLink = {}): string {
  return href({ tab: "Traces", status, outcome, latency_bucket, q });
}

export function tabHref(tab: GovernanceTab): string {
  return href({ tab });
}

/** Comma-separated URL param → allowed values only (never trusts the URL). */
export function parseList<T extends string>(raw: string | null, allowed: readonly T[]): T[] {
  if (!raw) return [];
  const set = new Set(allowed as readonly string[]);
  return Array.from(new Set(raw.split(",").map((v) => v.trim()))).filter((v): v is T => set.has(v));
}

export function parseOne<T extends string>(raw: string | null, allowed: readonly T[]): T | undefined {
  return raw !== null && (allowed as readonly string[]).includes(raw) ? (raw as T) : undefined;
}

const HOUR_MS = 60 * 60 * 1000;

/** `at` (ISO hour) → `{ from, to }` ISO strings for the API, or null if invalid. */
export function hourWindow(at: string | null): { from: string; to: string } | null {
  if (!at) return null;
  const start = new Date(at);
  if (Number.isNaN(start.getTime())) return null;
  return { from: start.toISOString(), to: new Date(start.getTime() + HOUR_MS).toISOString() };
}

const DAY_MS = 24 * HOUR_MS;

/**
 * API bounds for the global range. Presets are open-ended (no `to`), so
 * auto-refresh keeps seeing new rows; a custom range ends after its last
 * (inclusive) local day.
 */
const MINUTE_MS = 60_000;

export function rangeBounds(range: { kind: string; from: Date; to: Date } | undefined): { from?: string; to?: string } {
  if (!range) return {};
  // A rolling preset starts on a whole minute: the same view keeps one cache
  // key (and the backend serves it from its precomputed first page).
  if (range.kind !== "custom") return { from: new Date(Math.floor(range.from.getTime() / MINUTE_MS) * MINUTE_MS).toISOString() };
  return { from: range.from.toISOString(), to: new Date(range.to.getTime() + DAY_MS).toISOString() };
}
