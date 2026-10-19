"use client";

import { useCallback, useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowUpRight } from "lucide-react";
import { ChartCard, toChartTable } from "@/components/charts/ChartCard";
import { StackedArea } from "@/components/charts/StackedArea";
import { PercentileBand, type PercentilePoint } from "@/components/charts/PercentileBand";
import { BulletChart, type BulletDatum } from "@/components/charts/BulletChart";
import { RadialGauge } from "@/components/charts/RadialGauge";
import { TreemapChart } from "@/components/charts/HeavyCharts";
import type { TreemapDatum } from "@/components/charts/types";
import { entityHref } from "@/lib/entity-links";
import { formatLatencyMs } from "@/lib/format";
import { useChartTheme } from "@/lib/use-chart-theme";
import type { DateRange } from "@/lib/range-context";
import {
  DASHBOARD_OUTCOMES,
  bucketLabel,
  type DashboardData,
  type DashboardOutcome,
} from "@/lib/dashboard-api";

const usd = new Intl.NumberFormat("en", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const integer = new Intl.NumberFormat("en", { maximumFractionDigits: 0 });

export interface WidgetProps {
  data: DashboardData | undefined;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  range: DateRange;
}

const errorText = (error: unknown) => (error ? (error instanceof Error ? error.message : "Couldn't load this chart") : null);

const OUTCOME_LABEL: Record<DashboardOutcome, string> = {
  answered: "Answered",
  ungrounded: "Ungrounded",
  blocked: "Blocked",
  conversational: "Conversational",
  llm_failed: "LLM failed",
  clarify: "Clarifying question",
};

/** `/admin/usage?tab=queries` limited to one bucket (day, or the day of an hour bucket). */
export function queriesHrefFor(t: string): string {
  const day = t.slice(0, 10);
  return `/admin/usage?tab=queries&from=${day}&to=${day}`;
}

// ---------------------------------------------------------------------------
// Queries by outcome
// ---------------------------------------------------------------------------

type OutcomeRow = { label: string; t: string } & Record<DashboardOutcome, number>;
type TotalRow = { label: string; t: string; queries: number };

export function OutcomesWidget({ data, loading, error, onRetry, range }: WidgetProps) {
  const ct = useChartTheme();
  const router = useRouter();
  const bucket = data?.window?.bucket ?? "day";
  const byOutcome = data?.queries_by_outcome;

  const rows = useMemo<OutcomeRow[]>(
    () => (byOutcome ?? []).map((p) => ({ ...p, label: bucketLabel(p.t, bucket) })),
    [byOutcome, bucket],
  );
  const totals = useMemo<TotalRow[]>(
    () => (data?.query_trend ?? []).map((p) => ({ label: bucketLabel(p.date), t: p.date, queries: p.queries })),
    [data?.query_trend],
  );
  const outcomeColor: Record<DashboardOutcome, string> = {
    answered: ct.categorical[0],
    conversational: ct.categorical[1],
    ungrounded: ct.status.warn,
    blocked: ct.status.danger,
    llm_failed: ct.categorical[4 % ct.categorical.length],
    clarify: ct.status.info,
  };
  const series = DASHBOARD_OUTCOMES.map((key) => ({ key, label: OUTCOME_LABEL[key], color: outcomeColor[key] }));
  const select = useCallback((row: { t: string }) => router.push(queriesHrefFor(row.t)), [router]);

  const usingOutcomes = rows.length > 0;
  const subtitle = usingOutcomes
    ? "Click a day to open those queries"
    : "Outcome split not reported by the server; total queries shown. Click a day to open them";

  return (
    <ChartCard
      variant="aurora"
      title="Queries by outcome"
      subtitle={subtitle}
      window={usingOutcomes ? range.label : "Last 7 days"}
      loading={loading}
      error={errorText(!data ? error : null)}
      onRetry={onRetry}
      empty={!usingOutcomes && totals.length === 0}
      emptyMessage="No queries in this window"
      table={
        usingOutcomes
          ? toChartTable(rows, [
              { key: "label", label: "Bucket" },
              ...DASHBOARD_OUTCOMES.map((key) => ({ key, label: OUTCOME_LABEL[key], numeric: true })),
            ])
          : toChartTable(totals, [
              { key: "label", label: "Day" },
              { key: "queries", label: "Queries", numeric: true },
            ])
      }
    >
      {usingOutcomes ? (
        <StackedArea data={rows} xKey="label" series={series} onSelect={select} label="Queries by outcome" />
      ) : (
        <StackedArea
          data={totals}
          xKey="label"
          series={[{ key: "queries", label: "All queries" }]}
          onSelect={select}
          label="Queries per day"
        />
      )}
    </ChartCard>
  );
}

// ---------------------------------------------------------------------------
// Latency p50 / p95
// ---------------------------------------------------------------------------

export function LatencyWidget({ data, loading, error, onRetry, range }: WidgetProps) {
  const router = useRouter();
  const bucket = data?.window?.bucket ?? "day";
  const points = useMemo<(PercentilePoint & { t: string })[]>(
    () =>
      (data?.latency_series ?? []).flatMap((p) =>
        p.p50_ms === null || p.p95_ms === null ? [] : [{ label: bucketLabel(p.t, bucket), t: p.t, p50: p.p50_ms, p95: p.p95_ms }],
      ),
    [data?.latency_series, bucket],
  );
  const select = useCallback(() => router.push("/admin/governance?tab=Monitoring"), [router]);
  return (
    <ChartCard
      variant="aurora"
      title="Latency p50 / p95"
      subtitle="End-to-end response time per bucket. Click to open Monitoring"
      window={range.label}
      loading={loading}
      error={errorText(!data ? error : null)}
      onRetry={onRetry}
      empty={points.length === 0}
      emptyMessage={data?.latency_series ? "No requests in this window" : "Latency percentiles are not reported by the server"}
      table={toChartTable(points, [
        { key: "label", label: "Bucket" },
        { key: "p50", label: "p50", numeric: true, format: (v) => formatLatencyMs(Number(v)) },
        { key: "p95", label: "p95", numeric: true, format: (v) => formatLatencyMs(Number(v)) },
      ])}
    >
      <PercentileBand data={points} onSelect={select} formatValue={(v) => formatLatencyMs(v)} label="Latency percentiles" />
    </ChartCard>
  );
}

// ---------------------------------------------------------------------------
// Cost month-to-date vs budget and forecast
// ---------------------------------------------------------------------------

export function CostWidget({ data, loading, error, onRetry }: WidgetProps) {
  const router = useRouter();
  const cost = data?.cost;
  const rows = useMemo<BulletDatum[]>(() => {
    if (!cost) return [];
    const max = Math.max(cost.mtd_usd, cost.budget_usd ?? 0, cost.forecast_month_end_usd ?? 0) * 1.1 || 1;
    return [
      {
        label: "Cost MTD",
        value: cost.mtd_usd,
        target: cost.budget_usd ?? undefined,
        forecast: cost.forecast_month_end_usd ?? undefined,
        max,
      },
    ];
  }, [cost]);
  const select = useCallback(() => router.push("/admin/usage"), [router]);
  return (
    <ChartCard
      variant="aurora"
      title="Cost vs budget"
      subtitle="Month to date, linear month-end forecast and budget"
      window={cost ? cost.month : undefined}
      loading={loading}
      error={errorText(!data ? error : null)}
      onRetry={onRetry}
      empty={!cost}
      emptyMessage="Month-to-date cost is not reported by the server"
      bodyClassName="w-full"
      table={
        cost
          ? toChartTable(
              [
                {
                  mtd: usd.format(cost.mtd_usd),
                  forecast: cost.forecast_month_end_usd === null ? "—" : usd.format(cost.forecast_month_end_usd),
                  budget: cost.budget_usd === null ? "Not set" : usd.format(cost.budget_usd),
                },
              ],
              [
                { key: "mtd", label: "Month to date", numeric: true },
                { key: "forecast", label: "Forecast", numeric: true },
                { key: "budget", label: "Budget", numeric: true },
              ],
            )
          : undefined
      }
    >
      {cost && (
        <div className="flex flex-col gap-3">
          <BulletChart data={rows} onSelect={select} formatValue={(v) => usd.format(v)} label="Cost month to date" height={56} />
          <dl className="grid grid-cols-3 gap-2 text-xs">
            <div>
              <dt className="text-fg-subtle">Month to date</dt>
              <dd className="font-semibold tabular-nums text-fg">{usd.format(cost.mtd_usd)}</dd>
            </div>
            <div>
              <dt className="text-fg-subtle">Forecast</dt>
              <dd className="font-semibold tabular-nums text-fg">
                {cost.forecast_month_end_usd === null ? "—" : usd.format(cost.forecast_month_end_usd)}
              </dd>
            </div>
            <div>
              <dt className="text-fg-subtle">Budget</dt>
              <dd className="font-semibold tabular-nums text-fg">
                {cost.budget_usd === null ? "Not set" : usd.format(cost.budget_usd)}
              </dd>
            </div>
          </dl>
        </div>
      )}
    </ChartCard>
  );
}

// ---------------------------------------------------------------------------
// Satisfaction gauge
// ---------------------------------------------------------------------------

export function SatisfactionWidget({ data, loading, error, onRetry, range }: WidgetProps) {
  const router = useRouter();
  const sat = data?.satisfaction;
  const select = useCallback(() => router.push("/admin/feedback"), [router]);
  const ratings = sat ? sat.thumbs_up + sat.thumbs_down : 0;
  return (
    <ChartCard
      variant="aurora"
      title="Satisfaction"
      subtitle="Thumbs-up share of rated answers. Click to open Feedback"
      window={range.label}
      loading={loading}
      error={errorText(!data ? error : null)}
      onRetry={onRetry}
      empty={!sat}
      emptyMessage="Satisfaction is not reported by the server"
      table={
        sat
          ? toChartTable(
              [{ up: sat.thumbs_up, down: sat.thumbs_down, ratio: sat.ratio === null ? "—" : `${Math.round(sat.ratio * 100)}%` }],
              [
                { key: "up", label: "Thumbs up", numeric: true },
                { key: "down", label: "Thumbs down", numeric: true },
                { key: "ratio", label: "Satisfaction", numeric: true },
              ],
            )
          : undefined
      }
    >
      {sat && (
        <RadialGauge
          label="Satisfaction"
          value={sat.ratio === null ? null : sat.ratio * 100}
          max={100}
          bands={{ warn: 70, danger: 50 }}
          higherIsBetter
          onSelect={select}
          formatValue={(v) => `${Math.round(v)}%`}
          caption={`${integer.format(ratings)} ratings`}
        />
      )}
    </ChartCard>
  );
}

// ---------------------------------------------------------------------------
// Top intents (mini treemap -> Analytics)
// ---------------------------------------------------------------------------

export function IntentsWidget({ data, loading, error, onRetry, range }: WidgetProps) {
  const router = useRouter();
  const intents = data?.top_intents;
  const tiles = useMemo<TreemapDatum[]>(() => (intents ?? []).map((i) => ({ name: i.label, value: i.count })), [intents]);
  const select = useCallback(
    (tile: TreemapDatum) => {
      const match = intents?.find((i) => i.label === tile.name);
      if (match) router.push(entityHref({ kind: "analytics", intent: match.intent }));
    },
    [intents, router],
  );
  return (
    <ChartCard
      variant="aurora"
      title="Top intents"
      subtitle="Click an intent to open it in Analytics"
      window={range.label}
      loading={loading}
      error={errorText(!data ? error : null)}
      onRetry={onRetry}
      empty={tiles.length === 0}
      emptyMessage={intents ? "No classified queries in this window" : "Intents are not reported by the server"}
      table={
        intents
          ? toChartTable(
              intents.map((i) => ({ ...i, pct: `${Math.round(i.share * 100)}%` })),
              [
                { key: "label", label: "Intent" },
                { key: "count", label: "Queries", numeric: true },
                { key: "pct", label: "Share", numeric: true },
              ],
            )
          : undefined
      }
    >
      <TreemapChart data={tiles} onSelect={select} label="Top intents" />
    </ChartCard>
  );
}

// ---------------------------------------------------------------------------
// Pipeline stages (existing data: 24 h averages)
// ---------------------------------------------------------------------------

const STAGES = [
  { key: "embedding_ms", label: "Embedding" },
  { key: "retrieval_ms", label: "Retrieval" },
  { key: "reranker_ms", label: "Reranker" },
  { key: "llm_ms", label: "LLM synthesis" },
] as const;

export function PipelineWidget({ data, loading, error, onRetry }: WidgetProps) {
  const ct = useChartTheme();
  const pipeline = data?.pipeline;
  const rows = STAGES.map((s, i) => ({ ...s, ms: pipeline?.[s.key] ?? null, color: ct.categorical[i % ct.categorical.length] }));
  const total = rows.reduce((acc, r) => acc + (r.ms ?? 0), 0);
  return (
    <ChartCard
      variant="aurora"
      title="RAG pipeline"
      subtitle="Average time per stage"
      window="Last 24 hours"
      loading={loading}
      error={errorText(!data ? error : null)}
      onRetry={onRetry}
      empty={!pipeline}
      emptyMessage="No pipeline timings yet"
      bodyClassName="w-full"
      actions={
        <Link
          href="/admin/governance?tab=Monitoring"
          className="group inline-flex items-center gap-1 rounded-lg text-[11px] font-semibold text-accent-fg hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
        >
          Monitoring
          <ArrowUpRight
            className="h-4 w-4 transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 motion-reduce:transition-none"
            strokeWidth={1.75}
            aria-hidden="true"
          />
        </Link>
      }
    >
      <ul className="space-y-2 text-xs">
        {rows.map((r) => (
          <li key={r.key} className="flex items-center justify-between rounded-xl border border-line bg-surface-muted/40 p-2.5">
            <span className="flex items-center gap-2 font-medium text-fg">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: r.color }} aria-hidden="true" />
              {r.label}
              {r.key === "reranker_ms" && pipeline?.reranker_enabled === false && (
                <span className="rounded bg-surface-muted px-1.5 py-0.5 text-[10px] text-fg-muted">Disabled</span>
              )}
            </span>
            <span className="font-mono font-semibold tabular-nums text-fg">
              {r.ms === null ? "No data" : formatLatencyMs(r.ms)}
            </span>
          </li>
        ))}
      </ul>
      {total > 0 && (
        <div className="mt-3 flex h-2 w-full overflow-hidden rounded-full bg-surface-muted" aria-hidden="true">
          {rows.map((r) => (
            <div key={r.key} className="h-full" style={{ width: `${((r.ms ?? 0) / total) * 100}%`, backgroundColor: r.color }} />
          ))}
        </div>
      )}
    </ChartCard>
  );
}
