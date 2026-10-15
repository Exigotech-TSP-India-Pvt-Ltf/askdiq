"use client";

import { useMemo } from "react";
import { ChartCard, toChartTable } from "@/components/charts/ChartCard";
import { StackedArea } from "@/components/charts/StackedArea";
import { LineTrend } from "@/components/charts/LineTrend";
import { RadialGauge } from "@/components/charts/RadialGauge";
import { bucketLabel } from "@/lib/dashboard-api";
import { useChartTheme } from "@/lib/use-chart-theme";
import {
  COST_STAGES,
  COST_STAGE_LABEL,
  type CostStage,
  type UsageSummaryWithInsights,
} from "@/lib/usage-insights-api";

const usd = new Intl.NumberFormat("en", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const usdFine = new Intl.NumberFormat("en", { style: "currency", currency: "USD", maximumFractionDigits: 4 });
const integer = new Intl.NumberFormat("en", { maximumFractionDigits: 0 });

export interface CostInsightsProps {
  summary: UsageSummaryWithInsights | null;
  isLoading: boolean;
  error: string | null;
  onRetry: () => void;
}

/**
 * Usage & Cost chart additions (planchanges4 F3.5, newplan §15.4): cost by
 * stage, the linear month-end forecast and the prompt cache-hit ratio. All
 * three read optional fields of `/admin/usage/summary`; each shows its own
 * empty state when the server does not send them.
 */
export function CostInsights({ summary, isLoading, error, onRetry }: CostInsightsProps) {
  const ct = useChartTheme();
  const loading = isLoading && !summary;
  const err = summary ? null : error;
  const stageSeries = summary?.cost_by_stage_series;
  const forecast = summary?.forecast;
  const cache = summary?.cache;

  const stageRows = useMemo(
    () => (stageSeries?.points ?? []).map((p) => ({ ...p, label: bucketLabel(p.t, stageSeries?.bucket) })),
    [stageSeries],
  );
  const stageDefs = COST_STAGES.map((key: CostStage, i) => ({
    key,
    label: COST_STAGE_LABEL[key],
    color: ct.categorical[i % ct.categorical.length],
  }));

  const forecastRows = useMemo(
    () =>
      (forecast?.daily ?? []).map((p) => ({
        label: bucketLabel(p.t),
        actual: p.actual_usd,
        forecast: p.forecast_usd,
      })),
    [forecast],
  );

  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-3" data-testid="cost-insights">
      <ChartCard
        variant="aurora"
        title="Cost by stage"
        subtitle="USD per pipeline stage"
        loading={loading}
        error={err}
        onRetry={onRetry}
        empty={stageRows.length === 0}
        emptyMessage={stageSeries ? "No cost in this window" : "Cost by stage over time is not reported by the server"}
        table={toChartTable(stageRows, [
          { key: "label", label: "Bucket" },
          ...COST_STAGES.map((key) => ({ key, label: COST_STAGE_LABEL[key], numeric: true, format: (v: unknown) => usdFine.format(Number(v)) })),
        ])}
      >
        <StackedArea data={stageRows} xKey="label" series={stageDefs} formatValue={(v) => usd.format(v)} label="Cost by stage" />
      </ChartCard>

      <ChartCard
        variant="aurora"
        title="Month-end forecast"
        subtitle="Cumulative cost and linear projection from month to date"
        window={forecast?.month}
        loading={loading}
        error={err}
        onRetry={onRetry}
        empty={forecastRows.length === 0}
        emptyMessage="The month-end forecast is not reported by the server"
        table={
          forecast
            ? toChartTable(
                forecastRows.map((r) => ({
                  label: r.label,
                  actual: r.actual === null ? "—" : usd.format(r.actual),
                  forecast: r.forecast === null ? "—" : usd.format(r.forecast),
                })),
                [
                  { key: "label", label: "Day" },
                  { key: "actual", label: "Actual (cumulative)", numeric: true },
                  { key: "forecast", label: "Forecast", numeric: true },
                ],
              )
            : undefined
        }
        actions={
          forecast?.forecast_month_end_usd != null ? (
            <span className="rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-semibold tabular-nums text-fg-muted">
              {usd.format(forecast.forecast_month_end_usd)} forecast
            </span>
          ) : undefined
        }
      >
        <LineTrend
          data={forecastRows}
          xKey="label"
          series={[
            { key: "actual", label: "Actual" },
            { key: "forecast", label: "Forecast", dashed: true, color: ct.categorical[1] },
          ]}
          reference={forecast?.budget_usd != null ? { value: forecast.budget_usd, label: `Budget ${usd.format(forecast.budget_usd)}` } : undefined}
          formatValue={(v) => usd.format(v)}
          label="Month-end cost forecast"
        />
      </ChartCard>

      <ChartCard
        variant="aurora"
        title="Cache-hit ratio"
        subtitle="Share of prompt tokens served from the cache"
        loading={loading}
        error={err}
        onRetry={onRetry}
        empty={!cache}
        emptyMessage="Cache statistics are not reported by the server"
        table={
          cache
            ? toChartTable(
                [{ cached: cache.cached_prompt_tokens, prompt: cache.prompt_tokens, ratio: cache.hit_ratio === null ? "—" : `${Math.round(cache.hit_ratio * 100)}%` }],
                [
                  { key: "cached", label: "Cached prompt tokens", numeric: true },
                  { key: "prompt", label: "Prompt tokens", numeric: true },
                  { key: "ratio", label: "Hit ratio", numeric: true },
                ],
              )
            : undefined
        }
      >
        {cache && (
          <RadialGauge
            label="Cache-hit ratio"
            value={cache.hit_ratio === null ? null : cache.hit_ratio * 100}
            max={100}
            higherIsBetter
            formatValue={(v) => `${Math.round(v)}%`}
            caption={`${integer.format(cache.cached_prompt_tokens)} of ${integer.format(cache.prompt_tokens)} tokens`}
          />
        )}
      </ChartCard>
    </div>
  );
}
