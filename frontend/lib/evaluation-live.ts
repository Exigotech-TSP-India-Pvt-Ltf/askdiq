import { api } from "@/lib/api";
import type { Audience } from "@/lib/types";

/** Inclusive UTC days of the global range (empty = the latest records). */
export interface EvaluationRange {
  from?: string;
  to?: string;
}

/**
 * Query-cache key of `GET /metrics/evaluation/live` (page, sidebar prefetch
 * and live updates share it). `compare` is not part of it: the response
 * always carries the comparison.
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
