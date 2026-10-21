/**
 * Full evaluation runs (`/metrics/evaluation/runs`): start, cancel, history,
 * one run's results and the pre-run estimate. Keys sit under
 * `metrics/evaluation/runs`, which the console live stream refreshes on the
 * `evaluation.runs` scope (progress) and `evaluation.live` (a finished run).
 */
import { ApiError } from "@/lib/api";

const PROXY_BASE = "/api/proxy/metrics/evaluation/runs";
export const RUNS_PAGE_SIZE = 10;

export type EvalRunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface EvalRun {
  id: string;
  status: EvalRunStatus;
  started_by: string;
  options: { judge?: boolean; case_types?: string[] | null };
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  cases_total: number;
  cases_done: number;
  cases_passed: number;
  cancel_requested: boolean;
  pass_rate: number | null;
  duration_seconds: number | null;
  cost_usd: number | null;
  error: string | null;
}

export interface EvalRunDetail extends EvalRun {
  summary: Record<string, any> | null;
  case_results: Record<string, any>[] | null;
}

export interface EvalRunPage {
  items: EvalRun[];
  total: number;
  limit: number;
  offset: number;
  active: EvalRun | null;
}

export interface EvalRunEstimate {
  case_count: number;
  judge: boolean;
  avg_input_tokens_per_case: number;
  avg_output_tokens_per_case: number;
  avg_seconds_per_case: number;
  price_prompt_per_million: number;
  price_output_per_million: number;
  estimated_cost_usd: number;
  estimated_duration_seconds: number;
  basis: "history" | "default";
  sample_runs: number;
}

export interface EvalRunStarted {
  run_id: string;
  status: "queued";
  run: EvalRun;
}

export const evaluationRunsKey = (offset = 0, limit = RUNS_PAGE_SIZE) =>
  ["metrics/evaluation/runs", "page", offset, limit] as const;
export const evaluationRunKey = (id: string) => ["metrics/evaluation/runs", "run", id] as const;
export const evaluationRunEstimateKey = (judge: boolean) =>
  ["metrics/evaluation/runs", "estimate", judge ? "judge" : "plain"] as const;

export const isActiveRun = (run: Pick<EvalRun, "status"> | null | undefined): boolean =>
  run?.status === "queued" || run?.status === "running";

async function call<T>(
  path: string,
  token: string,
  { method = "GET", body, signal }: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const res = await fetch(`${PROXY_BASE}${path}`, {
    method,
    signal,
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText || `Request failed (${res.status})`;
    try {
      const payload = (await res.json()) as { detail?: unknown; title?: unknown };
      if (typeof payload.detail === "string") detail = payload.detail;
      else if (typeof payload.title === "string") detail = payload.title;
    } catch {
      // no JSON body
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

export function fetchEvaluationRuns(
  token: string,
  offset = 0,
  limit = RUNS_PAGE_SIZE,
  signal?: AbortSignal,
): Promise<EvalRunPage> {
  return call<EvalRunPage>(`?limit=${limit}&offset=${offset}`, token, { signal });
}

export function fetchEvaluationRun(token: string, id: string, signal?: AbortSignal): Promise<EvalRunDetail> {
  return call<EvalRunDetail>(`/${encodeURIComponent(id)}`, token, { signal });
}

export function fetchEvaluationRunEstimate(
  token: string,
  judge: boolean,
  signal?: AbortSignal,
): Promise<EvalRunEstimate> {
  return call<EvalRunEstimate>(`/estimate?judge=${judge}`, token, { signal });
}

export function startEvaluationRun(token: string, judge: boolean): Promise<EvalRunStarted> {
  return call<EvalRunStarted>("", token, { method: "POST", body: { judge } });
}

export function cancelEvaluationRun(token: string, id: string): Promise<EvalRun> {
  return call<EvalRun>(`/${encodeURIComponent(id)}/cancel`, token, { method: "POST" });
}

/** "45 s", "3 min", "1 h 05 min". */
export function formatDuration(seconds: number | null | undefined): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
}

export function formatUsd(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return value < 0.01 && value > 0 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}

export const STATUS_LABEL: Record<EvalRunStatus, string> = {
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
};
