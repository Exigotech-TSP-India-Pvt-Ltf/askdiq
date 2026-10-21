"use client";

import React from "react";
import { AlertCircle, CheckCircle2, GitCompare, MinusCircle, type LucideIcon } from "lucide-react";
import { ChartCard, toChartTable } from "@/components/charts/ChartCard";
import { DeltaBadge } from "@/components/charts/KpiTrendCard";
import { Radar } from "@/components/charts/Radar";
import { StudioCardHeader, studioCardClass } from "@/components/ui/StudioCard";
import { studioTile } from "@/components/ui/styles";
import { useChartTheme } from "@/lib/use-chart-theme";

export interface CompareDelta {
  benchmark: number | null;
  live: number | null;
  diff: number | null;
}

export type CompareMetricKey = "pass_rate" | "faithfulness" | "retrieval_recall" | "citation_precision";

export type CompareDeltas = Partial<Record<CompareMetricKey, CompareDelta>>;

interface CompareMatrixProps {
  comparisonData: {
    deltas?: CompareDeltas;
    dual_radar?: Array<{ metric: string; benchmark: number | null; live: number | null }>;
  };
}

interface MetricDef {
  key: CompareMetricKey;
  label: string;
  /** Shares (0..1) shown as %; else a 1-5 score. */
  share: boolean;
}

export const COMPARE_METRICS: readonly MetricDef[] = [
  { key: "pass_rate", label: "Overall pass rate", share: true },
  { key: "faithfulness", label: "Faithfulness (1-5)", share: false },
  { key: "retrieval_recall", label: "Retrieval recall", share: true },
  { key: "citation_precision", label: "Citation precision", share: true },
];

/** Differences under these read as "in line" (0.5 percentage points, 0.05 on the 1-5 scale). */
const FLAT_SHARE = 0.005;
const FLAT_SCORE = 0.05;

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function formatCompareValue(v: number | null | undefined, share: boolean): string {
  if (!isNum(v)) return "Not measured";
  return share ? `${(v * 100).toFixed(1)}%` : v.toFixed(1);
}

/** Difference text with its sign: percentage points for shares, score points on the 1-5 scale. */
export function formatCompareDiff(diff: number, share: boolean): string {
  const body = share ? `${(diff * 100).toFixed(1)} pp` : diff.toFixed(2);
  return diff > 0 ? `+${body}` : body;
}

export type DriftDirection = "better" | "worse" | "flat" | "unknown";

export interface DriftRow {
  key: CompareMetricKey;
  label: string;
  direction: DriftDirection;
  /** Signed difference (live - benchmark), or "Not measured". */
  value: string;
  detail: string;
}

/**
 * Drift rows from the same `deltas` the KPI cards show (VA-12): the sign,
 * the wording and the colour all come from live - benchmark, so the card
 * can never contradict the numbers above it. A side the server did not
 * measure gives "Not measured", never a made-up figure.
 */
export function buildDriftRows(deltas: CompareDeltas = {}): DriftRow[] {
  return COMPARE_METRICS.map(({ key, label, share }) => {
    const d = deltas[key];
    const bench = d?.benchmark;
    const live = d?.live;
    if (!isNum(bench) || !isNum(live)) {
      const missing = !isNum(bench) && !isNum(live) ? "either side" : !isNum(bench) ? "the benchmark" : "live users";
      return { key, label, direction: "unknown", value: "Not measured", detail: `No value for ${missing}.` };
    }
    const diff = isNum(d?.diff) ? d.diff : live - bench;
    const range = `${formatCompareValue(bench, share)} benchmark, ${formatCompareValue(live, share)} live`;
    const value = formatCompareDiff(diff, share);
    if (Math.abs(diff) < (share ? FLAT_SHARE : FLAT_SCORE)) {
      return { key, label, direction: "flat", value, detail: `Live is in line with the benchmark (${range}).` };
    }
    const better = diff > 0;
    return {
      key,
      label,
      direction: better ? "better" : "worse",
      value,
      detail: `Live is ${better ? "higher" : "lower"} than the benchmark (${range}).`,
    };
  });
}

const DRIFT_TONE: Record<DriftDirection, { icon: LucideIcon; iconClass: string; valueClass: string }> = {
  better: { icon: CheckCircle2, iconClass: "text-status-ok", valueClass: "text-status-ok-fg" },
  worse: { icon: AlertCircle, iconClass: "text-status-warn", valueClass: "text-status-warn-fg" },
  flat: { icon: CheckCircle2, iconClass: "text-fg-subtle", valueClass: "text-fg-muted" },
  unknown: { icon: MinusCircle, iconClass: "text-fg-subtle", valueClass: "text-fg-subtle" },
};

function CompareKpi({ def, delta }: { def: MetricDef; delta: CompareDelta | undefined }) {
  const diff = delta?.diff;
  return (
    <div className={`${studioCardClass()} flex flex-col gap-3 p-5`} data-testid={`compare-kpi-${def.key}`}>
      <div className="flex items-start justify-between gap-2">
        <p className="line-clamp-2 text-sm font-medium text-fg-subtle" title={def.label}>
          {def.label}
        </p>
        {isNum(diff) ? (
          <DeltaBadge
            delta={diff}
            goodDirection="up"
            flatBelow={def.share ? FLAT_SHARE : FLAT_SCORE}
            format={(d) => (def.share ? `${(d * 100).toFixed(1)} pp` : d.toFixed(2))}
            title="Live minus benchmark"
          />
        ) : null}
      </div>
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <p className="text-[11px] text-fg-subtle">Benchmark</p>
          <p className="text-lg font-semibold tabular-nums text-fg-muted">{formatCompareValue(delta?.benchmark, def.share)}</p>
        </div>
        <div className="min-w-0 text-right">
          <p className="text-[11px] text-fg-subtle">Live users</p>
          <p className="text-2xl font-semibold tabular-nums text-fg">{formatCompareValue(delta?.live, def.share)}</p>
        </div>
      </div>
    </div>
  );
}

export function EvaluationCompareMatrix({ comparisonData }: CompareMatrixProps) {
  const ct = useChartTheme();
  const deltas = comparisonData?.deltas ?? {};
  // A radar point needs both sides; a metric with a missing side is named instead of drawn at 0.
  const radarAll = comparisonData?.dual_radar ?? [];
  const radarData = radarAll.flatMap((r) =>
    isNum(r.benchmark) && isNum(r.live) ? [{ metric: r.metric, benchmark: r.benchmark, live: r.live }] : [],
  );
  const radarMissing = radarAll.filter((r) => !isNum(r.benchmark) || !isNum(r.live)).map((r) => r.metric);
  const drift = buildDriftRows(deltas);
  // Benchmark: brand teal, solid. Live: amber, dashed (VA-11: two series that read apart).
  const benchColor = ct.categorical[0];
  const liveColor = ct.multiSeries[3];

  return (
    <div className="space-y-6">
      <section className={`${studioCardClass()} p-5`} aria-labelledby="compare-title">
        <StudioCardHeader
          title="Live production vs benchmark suite"
          subtitle="Real user traffic against the latest benchmark report"
          icon={GitCompare}
          titleId="compare-title"
          divider={false}
          className="flex-wrap"
          aside={
            <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-muted" aria-label="Series">
              <li className="flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: benchColor }} aria-hidden="true" />
                Benchmark
              </li>
              <li className="flex items-center gap-1.5">
                <span className="h-0 w-3 border-t-2 border-dashed" style={{ borderColor: liveColor }} aria-hidden="true" />
                Live users
              </li>
            </ul>
          }
        />
      </section>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {COMPARE_METRICS.map((def) => (
          <CompareKpi key={def.key} def={def} delta={deltas[def.key]} />
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <ChartCard
          title="Quality radar"
          subtitle={
            radarMissing.length
              ? `Benchmark vs live users (%). Not measured: ${radarMissing.join(", ")}`
              : "Benchmark vs live users (%)"
          }
          empty={radarData.length === 0}
          emptyMessage="No metric is measured on both sides"
          bodyClassName="h-72 w-full"
          table={toChartTable(radarData, [
            { key: "metric", label: "Metric" },
            { key: "benchmark", label: "Benchmark (%)", numeric: true },
            { key: "live", label: "Live users (%)", numeric: true },
          ])}
        >
          <Radar
            data={radarData}
            axisKey="metric"
            series={[
              { key: "benchmark", label: "Benchmark", color: benchColor },
              { key: "live", label: "Live users", color: liveColor, dashed: true },
            ]}
            label="Benchmark vs live quality radar"
            ringLabels
          />
        </ChartCard>

        <ChartCard
          title="Drift vs benchmark"
          subtitle="Live minus benchmark, from the same numbers as the cards above"
          bodyClassName="w-full"
        >
          <ul className="space-y-3" data-testid="drift-rows">
            {drift.map((row) => {
              const tone = DRIFT_TONE[row.direction];
              const Icon = tone.icon;
              return (
                <li
                  key={row.key}
                  data-direction={row.direction}
                  className={`${studioTile} flex items-center justify-between gap-3 p-3`}
                >
                  <div className="flex min-w-0 items-start gap-2.5">
                    <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${tone.iconClass}`} strokeWidth={1.75} aria-hidden="true" />
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-fg">{row.label}</p>
                      <p className="text-[11px] text-fg-muted">{row.detail}</p>
                    </div>
                  </div>
                  <span className={`shrink-0 whitespace-nowrap text-xs font-semibold tabular-nums ${tone.valueClass}`}>
                    {row.value}
                  </span>
                </li>
              );
            })}
          </ul>
        </ChartCard>
      </div>
    </div>
  );
}
