import { api } from "@/lib/api";
import type { Audience } from "@/lib/types";

/** Inclusive UTC days of the global range (empty = the latest records). */
export interface EvaluationRange {
  from?: string;
  to?: string;
}

/**
 * Query-cache key of `GET /metrics/evaluation/live` (the full report, cases
 * included). `compare` is not part of it: the response always carries the
 * comparison. The Evaluation page reads the smaller views below instead.
 */
export const evaluationLiveKey = (audience: Audience, range: EvaluationRange = {}) =>
  ["metrics/evaluation/live", audience, range.from ?? "", range.to ?? ""] as const;

export function fetchEvaluationLive(
  token: string,
  audience: Audience,
  range: EvaluationRange,
  signal?: AbortSignal,
): Promise<Record<string, any>> {
  return api.evaluationReportLive(token, audience, false, signal, range);
}

function viewQuery(audience: Audience, range: EvaluationRange, extra: Record<string, string | number> = {}): string {
  const params = new URLSearchParams({ audience });
  if (range.from) params.set("from", range.from);
  if (range.to) params.set("to", range.to);
  for (const [k, v] of Object.entries(extra)) params.set(k, String(v));
  return params.toString();
}

// ---------------------------------------------------------------------------
// Evaluation page views: small reads of the same precomputed report (a few
// KB each, so they stay in the persisted cache). Keys sit under
// `metrics/evaluation`, which the console live stream refreshes.
// ---------------------------------------------------------------------------

/** The report without its case lists, plus `outcomes` ({passed, failed}). */
export const evaluationSummaryKey = (audience: Audience, range: EvaluationRange = {}) =>
  ["metrics/evaluation/live/summary", audience, range.from ?? "", range.to ?? ""] as const;

export function fetchEvaluationSummary(
  token: string,
  audience: Audience,
  range: EvaluationRange,
  signal?: AbortSignal,
): Promise<Record<string, any>> {
  return api.evaluationLiveView(token, "summary", viewQuery(audience, range), signal);
}

export type OutcomeFilter = "all" | "passed" | "failed";
export type GroundedFilter = "all" | "grounded" | "ungrounded";

export interface CaseQuery {
  outcome: OutcomeFilter;
  grounded: GroundedFilter;
  q: string;
  offset: number;
  limit: number;
}

export const DEFAULT_CASE_QUERY: CaseQuery = { outcome: "all", grounded: "all", q: "", offset: 0, limit: 25 };

export interface CasePage {
  items: Record<string, any>[];
  total: number;
  offset: number;
  limit: number;
  as_of?: string | null;
}

const needle = (q: string) => q.trim().toLowerCase();

/** One explorer page (slim rows, filtered and searched on the server). */
export const evaluationCasesKey = (audience: Audience, range: EvaluationRange, c: CaseQuery) =>
  [
    "metrics/evaluation/live/cases",
    audience,
    range.from ?? "",
    range.to ?? "",
    c.outcome,
    c.grounded,
    needle(c.q),
    c.offset,
    c.limit,
  ] as const;

export function fetchEvaluationCases(
  token: string,
  audience: Audience,
  range: EvaluationRange,
  c: CaseQuery,
  signal?: AbortSignal,
): Promise<CasePage> {
  const extra: Record<string, string | number> = { offset: c.offset, limit: c.limit };
  if (c.outcome !== "all") extra.outcome = c.outcome;
  if (c.grounded !== "all") extra.grounded = c.grounded;
  if (needle(c.q)) extra.q = needle(c.q);
  return api.evaluationLiveView(token, "cases", viewQuery(audience, range, extra), signal) as Promise<CasePage>;
}

/** One full case of the report (the record drawer). */
export const evaluationCaseKey = (audience: Audience, range: EvaluationRange, id: string) =>
  ["metrics/evaluation/live/case", audience, range.from ?? "", range.to ?? "", id] as const;

export function fetchEvaluationCase(
  token: string,
  audience: Audience,
  range: EvaluationRange,
  id: string,
  signal?: AbortSignal,
): Promise<Record<string, any>> {
  return api.evaluationLiveView(token, `cases/${encodeURIComponent(id)}`, viewQuery(audience, range), signal);
}
