"use client";

import { useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import { ChartCard, toChartTable } from "@/components/charts/ChartCard";
import { Radar } from "@/components/charts/Radar";
import { LineTrend } from "@/components/charts/LineTrend";
import { useChartTheme } from "@/lib/use-chart-theme";
import {
  evalRunHref,
  qualityRadarRows,
  type LiveReportWithInsights,
  type PassRatePoint,
} from "@/lib/usage-insights-api";

const pct = (v: number) => `${v.toFixed(1)}%`;
const runDate = new Intl.DateTimeFormat("en-AU", { day: "numeric", month: "short", timeZone: "UTC" });

function runLabel(p: PassRatePoint): string {
  const d = new Date(p.started_at);
  return Number.isNaN(d.getTime()) ? p.run_id : runDate.format(d);
}

interface Props {
  report: LiveReportWithInsights | null;
  loading: boolean;
}

/** Quality dimensions, current vs previous run (planchanges4 F3.5). */
export function QualityRadarCard({ report, loading }: Props) {
  const ct = useChartTheme();
  const { rows, hasPrevious } = useMemo(() => qualityRadarRows(report), [report]);
  const series = [
    { key: "current" as const, label: "Current run", color: ct.categorical[0] },
    ...(hasPrevious ? [{ key: "previous" as const, label: "Previous run", color: ct.categorical[1] }] : []),
  ];
  return (
    <ChartCard
      title="Quality scorecard"
      subtitle={hasPrevious ? "Current vs previous run (%)" : "Current run (%); no previous run reported"}
      variant="aurora"
      titleStyle="eyebrow"
      loading={loading && !report}
      empty={rows.length === 0}
      emptyMessage="No scored cases yet"
      table={toChartTable(rows, [
        { key: "metric", label: "Metric" },
        { key: "current", label: "Current", numeric: true, format: (v) => pct(Number(v)) },
        ...(hasPrevious ? [{ key: "previous" as const, label: "Previous", numeric: true, format: (v: unknown) => pct(Number(v)) }] : []),
      ])}
    >
      <Radar data={rows} axisKey="metric" series={series} max={100} formatValue={pct} label="Quality dimensions" />
    </ChartCard>
  );
}

/** Pass rate per run; a marker opens that run (newplan §15.4 "Marker to run"). */
export function PassRateTrendCard({ report, loading }: Props) {
  const router = useRouter();
  const trend = report?.pass_rate_trend;
  const rows = useMemo(
    () => (trend ?? []).map((p) => ({ label: runLabel(p), run_id: p.run_id, pass_rate: p.pass_rate * 100, n_cases: p.n_cases })),
    [trend],
  );
  const select = useCallback((row: { run_id: string }) => router.push(evalRunHref(row.run_id)), [router]);
  return (
    <ChartCard
      title="Pass-rate trend"
      subtitle="One marker per evaluation run. Click a marker to open the run"
      variant="aurora"
      titleStyle="eyebrow"
      loading={loading && !report}
      empty={rows.length === 0}
      emptyMessage={trend ? "No evaluation runs yet" : "Run history is not reported by the server"}
      table={toChartTable(rows, [
        { key: "label", label: "Run" },
        { key: "run_id", label: "Run id" },
        { key: "pass_rate", label: "Pass rate", numeric: true, format: (v) => pct(Number(v)) },
        { key: "n_cases", label: "Cases", numeric: true },
      ])}
    >
      <LineTrend
        data={rows}
        xKey="label"
        series={[{ key: "pass_rate", label: "Pass rate" }]}
        markers
        max={100}
        onSelect={select}
        formatValue={pct}
        label="Pass rate per run"
        xAxisLabel="Run date (UTC)"
        yAxisLabel="Pass rate (%)"
      />
    </ChartCard>
  );
}
