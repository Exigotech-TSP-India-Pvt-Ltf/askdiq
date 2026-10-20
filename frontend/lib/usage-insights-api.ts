/**
 * Additive contract for the Usage & Cost and Evaluation (Live) chart
 * additions. Written contract:
 * `docs/technical/frontend/contracts/dashboard-aggregations.md` (sections 4 and 5).
 *
 * No new endpoints: the fields are added, optional, to responses the pages
 * already fetch (`GET /admin/usage/summary` and `GET /metrics/evaluation/live`),
 * so the pages keep working against today's backend and show an empty state
 * for any missing field.
 */
import type { UsageSummary } from "@/lib/types";

// ---------------------------------------------------------------------------
// Usage: GET /admin/usage/summary (+ optional fields)
// ---------------------------------------------------------------------------

export const COST_STAGES = [
  "query_embed",
  "guardrail_embed",
  "prompt",
  "cached_prompt",
  "reasoning",
  "visible",
  "ingestion",
] as const;
export type CostStage = (typeof COST_STAGES)[number];

export const COST_STAGE_LABEL: Record<CostStage, string> = {
  query_embed: "Query embedding",
  guardrail_embed: "Guardrail embedding",
  prompt: "Prompt (uncached)",
  cached_prompt: "Prompt (cached)",
  reasoning: "Reasoning",
  visible: "Visible output",
  ingestion: "Ingestion",
};

/** USD per stage for one bucket. */
export type CostStagePoint = { t: string } & Record<CostStage, number>;

export interface CostByStageSeries {
  bucket: "hour" | "day";
  points: CostStagePoint[];
}

export interface CostForecastPoint {
  /** `YYYY-MM-DD` (UTC). */
  t: string;
  /** Cumulative month-to-date cost; null for future days. */
  actual_usd: number | null;
  /** Linear projection; null for days before today. Today carries both values so the lines join. */
  forecast_usd: number | null;
}

export interface CostForecast {
  currency: "USD";
  /** `YYYY-MM` (UTC calendar month). */
  month: string;
  mtd_usd: number;
  forecast_month_end_usd: number | null;
  budget_usd: number | null;
  method: "linear";
  /** One point per day of the month. */
  daily: CostForecastPoint[];
}

export interface CacheStats {
  cached_prompt_tokens: number;
  prompt_tokens: number;
  /** cached_prompt_tokens / prompt_tokens, 0..1; null when prompt_tokens is 0. */
  hit_ratio: number | null;
}

export interface UsageInsightFields {
  cost_by_stage_series: CostByStageSeries;
  forecast: CostForecast;
  cache: CacheStats;
}

export type UsageSummaryWithInsights = UsageSummary & Partial<UsageInsightFields>;

// ---------------------------------------------------------------------------
// Evaluation (Live): GET /metrics/evaluation/live (+ optional fields)
// ---------------------------------------------------------------------------

export interface EvalRunRef {
  run_id: string;
  /** ISO 8601 UTC. */
  started_at: string;
  n_cases: number;
}

export interface QualityDimension {
  key: string;
  label: string;
  /** 0..1; null = not measured in that run. */
  current: number | null;
  previous: number | null;
}

export interface QualityDimensions {
  current_run: EvalRunRef | null;
  previous_run: EvalRunRef | null;
  dimensions: QualityDimension[];
}

export interface PassRatePoint {
  run_id: string;
  started_at: string;
  /** 0..1. */
  pass_rate: number;
  n_cases: number;
}

export interface EvaluationInsightFields {
  quality_dimensions: QualityDimensions;
  /** Oldest first, at most 30 runs. */
  pass_rate_trend: PassRatePoint[];
}

/** The subset of today's live report the fallback radar reads (all real measurements). */
export interface LiveReportCore {
  n_cases?: number;
  pass_rate?: number;
  retrieval_recall?: number;
  groundedness_accuracy?: number;
  judge?: { faithfulness?: number };
  citation?: { precision?: number };
  safety?: { passed?: number };
}

export type LiveReportWithInsights = LiveReportCore & Partial<EvaluationInsightFields>;

const JUDGE_SCALE = 5;

/**
 * Radar rows (scores in %): the server's current vs previous dimensions when
 * sent, else the current run's own measurements with no previous series.
 * Empty when there are no scored cases.
 */
export function qualityRadarRows(report: LiveReportWithInsights | null | undefined): {
  rows: { metric: string; current: number; previous: number }[];
  hasPrevious: boolean;
} {
  if (!report) return { rows: [], hasPrevious: false };
  const dims = report.quality_dimensions?.dimensions;
  if (dims && dims.length > 0) {
    const hasPrevious = dims.some((d) => d.previous !== null);
    return {
      rows: dims.map((d) => ({
        metric: d.label,
        current: (d.current ?? 0) * 100,
        previous: (d.previous ?? 0) * 100,
      })),
      hasPrevious,
    };
  }
  if (!report.n_cases) return { rows: [], hasPrevious: false };
  const pairs: [string, number | undefined][] = [
    ["Retrieval recall", report.retrieval_recall],
    ["Groundedness", report.groundedness_accuracy],
    [
      "Faithfulness",
      report.judge?.faithfulness === undefined ? undefined : report.judge.faithfulness / JUDGE_SCALE,
    ],
    ["Citation precision", report.citation?.precision],
    ["Safety", report.safety?.passed],
    ["Pass rate", report.pass_rate],
  ];
  return {
    rows: pairs
      .filter((p): p is [string, number] => typeof p[1] === "number")
      .map(([metric, v]) => ({ metric, current: v * 100, previous: 0 })),
    hasPrevious: false,
  };
}

/** Deep link for a run marker (the live evaluation page reads `?run=`). */
export function evalRunHref(runId: string): string {
  return `/admin/evaluation-live?run=${encodeURIComponent(runId)}`;
}
