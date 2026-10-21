"use client";

import { useCallback, useMemo } from "react";
import { ChartCard, toChartTable } from "@/components/charts/ChartCard";
import {
  ChartFrame,
  TooltipBody,
  niceMax,
  scaleLinear,
  useChartMarks,
  type ChartSize,
} from "@/components/charts/primitives";
import { AxisTitles, XLabels, YGrid, axisDescription, plotArea } from "@/components/charts/cartesian";
import { bucketLabel } from "@/lib/dashboard-api";
import { useChartTheme } from "@/lib/use-chart-theme";
import { useAzureCosts, type AzureCosts, type BilledDay, type CostDays } from "@/lib/azure-costs-api";

const BAR_GAP = 0.3;
const CHART_HEIGHT = 220;
const AXES = { xAxisLabel: "Date (UTC)", yAxisLabel: "Cost per day" } as const;

const STATUS_LABEL: Record<BilledDay["status"], string> = {
  final: "Final",
  estimated: "Billed (may be rerated)",
  pending: "Not billed yet",
  no_data: "Before the first poll",
};

/** Currency formatter that tolerates unknown codes (falls back to "XXX 1.23"). */
export function moneyFormat(currency: string, digits = 2): (v: number) => string {
  try {
    const f = new Intl.NumberFormat("en-AU", { style: "currency", currency, maximumFractionDigits: digits });
    return (v) => f.format(v);
  } catch {
    return (v) => `${currency} ${v.toFixed(digits)}`;
  }
}

export function formatVariance(pct: number | null): string {
  if (pct === null) return "—";
  return `${pct > 0 ? "+" : pct < 0 ? "−" : ""}${Math.abs(pct).toFixed(1)}%`;
}

/** Message for a payload that cannot show numbers (null when it can). */
export function unavailableMessage(costs: AzureCosts): string | null {
  switch (costs.state) {
    case "not_connected":
      return "Not connected: Azure Insights is off (AZURE_INSIGHTS_ENABLED). See foundry-usage-data-review.md, “How to enable”.";
    case "unsupported":
      return `Unsupported subscription: ${costs.message ?? "Cost Management is not available for this subscription type."}`;
    default:
      if (costs.billed_through === null && costs.state === "never_run") {
        return "Waiting for the first daily billed-cost poll (03:00, Australia/Sydney).";
      }
      return null;
  }
}

export interface BilledVsEstimatedProps {
  token: string | null;
  /** The shared date range as inclusive UTC days. */
  days: CostDays;
}

/**
 * Usage & Cost → Overview "Billed vs estimated" (Azure Insights Phase 3):
 * month-to-date KPIs, daily bars of AskDIQ's estimate next to Azure's billed
 * cost (dashed = not billed yet; Azure lags 8–72 h) and a final / estimated
 * badge. Reads the precomputed `/admin/azure/costs` snapshot only.
 */
export function BilledVsEstimated({ token, days }: BilledVsEstimatedProps) {
  const { costs, isLoading, error, refresh } = useAzureCosts(token, days);
  return <BilledVsEstimatedCard costs={costs} isLoading={isLoading} error={error} onRetry={refresh} />;
}

export interface BilledVsEstimatedCardProps {
  costs: AzureCosts | null;
  isLoading: boolean;
  error: string | null;
  onRetry: () => void;
}

export function BilledVsEstimatedCard({ costs, isLoading, error, onRetry }: BilledVsEstimatedCardProps) {
  const unavailable = costs ? unavailableMessage(costs) : null;
  const billedCur = costs?.currency ?? costs?.estimate_currency ?? "USD";
  const estCur = costs?.estimate_currency ?? "USD";
  const fmtBilled = useMemo(() => moneyFormat(billedCur), [billedCur]);
  const fmtEst = useMemo(() => moneyFormat(estCur), [estCur]);
  const rows = costs?.days ?? [];

  return (
    <ChartCard
      variant="aurora"
      title="Billed vs estimated"
      subtitle="Azure Cost Management billed cost next to AskDIQ's own estimate"
      window={costs ? `${bucketLabel(costs.range.from_date)} – ${bucketLabel(costs.range.to_date)}` : undefined}
      loading={isLoading && !costs}
      error={costs ? null : error}
      onRetry={onRetry}
      empty={unavailable !== null}
      emptyMessage={unavailable ?? undefined}
      meta={costs && !unavailable ? <StatusBadge costs={costs} /> : undefined}
      resetKey={costs}
      bodyClassName="w-full"
      table={
        costs && !unavailable
          ? toChartTable(
              rows.map((d) => ({
                day: d.date,
                estimate: fmtEst(d.estimate),
                billed: d.billed === null ? "—" : fmtBilled(d.billed),
                variance: formatVariance(d.variance_pct),
                status: STATUS_LABEL[d.status],
              })),
              [
                { key: "day", label: "Day (UTC)" },
                { key: "estimate", label: `Estimate (${estCur})`, numeric: true },
                { key: "billed", label: `Billed (${billedCur})`, numeric: true },
                { key: "variance", label: "Variance", numeric: true },
                { key: "status", label: "Status" },
              ],
            )
          : undefined
      }
    >
      {costs && (
        <div className="flex flex-col gap-4" data-testid="billed-vs-estimated">
          <Kpis costs={costs} fmtBilled={fmtBilled} fmtEst={fmtEst} />
          <BilledBars rows={rows} fmtBilled={fmtBilled} fmtEst={fmtEst} />
          <p className="text-[11px] leading-snug text-fg-subtle">
            {costs.lag_note} Dashed bars are days Azure has not billed yet
            {costs.billed_through ? ` (billed through ${bucketLabel(costs.billed_through)})` : ""}.
            {costs.currency_mismatch &&
              ` The bill is in ${costs.currency} and the estimate in ${costs.estimate_currency}: no ${costs.currency} retail prices are stored yet, so no variance is computed (no FX conversion).`}
            {costs.repriced && ` The estimate is re-priced with Azure retail prices in ${costs.estimate_currency}.`}
            {costs.estimate_note ? ` ${costs.estimate_note}` : ""}
          </p>
        </div>
      )}
    </ChartCard>
  );
}

function StatusBadge({ costs }: { costs: AzureCosts }) {
  const final = costs.totals.final;
  const failing = costs.state === "error" || costs.state === "throttled" || costs.state === "budget_exhausted";
  const text = failing
    ? `Last poll: ${costs.state.replace("_", " ")}`
    : final
      ? "Final"
      : "Estimated · lags 8–72 h";
  const tone = failing
    ? "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300"
    : final
      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300"
      : "bg-surface-muted text-fg-muted";
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${tone}`}
      title={failing ? (costs.message ?? undefined) : costs.lag_note}
      data-testid="billed-status-badge"
    >
      {text}
    </span>
  );
}

function Kpis({
  costs,
  fmtBilled,
  fmtEst,
}: {
  costs: AzureCosts;
  fmtBilled: (v: number) => string;
  fmtEst: (v: number) => string;
}) {
  const mtd = costs.month_to_date;
  const pct = mtd.variance_pct;
  const within = pct === null || Math.abs(pct) <= costs.drift_threshold_pct;
  const month = bucketLabel(`${mtd.month}-01`).replace(/^\d+\s*/, "");
  return (
    <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <Kpi
        label="Billed (month to date)"
        value={mtd.billed === null ? "—" : fmtBilled(mtd.billed)}
        hint={costs.billed_through ? `Azure · through ${bucketLabel(costs.billed_through)}` : `Azure · ${month}`}
      />
      <Kpi label="AskDIQ estimate" value={fmtEst(mtd.estimate)} hint="live, per answer" />
      <Kpi
        label="Variance"
        value={formatVariance(pct)}
        valueClass={
          pct === null ? "" : within ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300"
        }
        hint={
          pct === null
            ? costs.currency_mismatch
              ? "currency differs"
              : "no billed days yet"
            : `${within ? "within" : "above"} ${costs.drift_threshold_pct}% · ${mtd.billed_days} billed day${mtd.billed_days === 1 ? "" : "s"}`
        }
      />
    </dl>
  );
}

function Kpi({ label, value, hint, valueClass = "" }: { label: string; value: string; hint: string; valueClass?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white/60 p-3 dark:border-dark-border dark:bg-white/5">
      <dt className="text-[11px] font-semibold uppercase tracking-wider text-fg-muted">{label}</dt>
      <dd className={`mt-1 text-xl font-bold tabular-nums text-ink dark:text-slate-100 ${valueClass}`}>{value}</dd>
      <dd className="text-[11px] text-fg-subtle">{hint}</dd>
    </div>
  );
}

function BilledBars({
  rows,
  fmtBilled,
  fmtEst,
}: {
  rows: readonly BilledDay[];
  fmtBilled: (v: number) => string;
  fmtEst: (v: number) => string;
}) {
  const ct = useChartTheme();
  const estColor = ct.categorical[0];
  const billedColor = ct.categorical[5 % ct.categorical.length];
  const max = useMemo(() => niceMax(Math.max(0, ...rows.map((r) => Math.max(r.estimate, r.billed ?? 0)))), [rows]);
  const describe = useCallback(
    (r: BilledDay) =>
      `${bucketLabel(r.date)}: estimate ${fmtEst(r.estimate)}, billed ${r.billed === null ? "not yet" : fmtBilled(r.billed)}, ${STATUS_LABEL[r.status]}`,
    [fmtBilled, fmtEst],
  );
  const { active, getMarkProps } = useChartMarks(rows, describe, undefined, ct.dimOpacity);

  const layout = ({ width, height }: ChartSize) => {
    const area = plotArea(width, height, AXES, { labels: rows.map((r) => bucketLabel(r.date)), placement: "band" });
    const band = (area.x1 - area.x0) / Math.max(1, rows.length);
    const v = scaleLinear([0, max || 1], [area.y1, area.y0]);
    const barW = Math.max(1, (band * (1 - BAR_GAP)) / 2);
    const x0 = (i: number) => area.x0 + band * i + (band * BAR_GAP) / 2;
    return { area, band, v, barW, x0 };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { band, v, x0 } = layout(size);
    const r = rows[active];
    return {
      x: x0(active) + (band * (1 - BAR_GAP)) / 2,
      y: v(Math.max(r.estimate, r.billed ?? 0)),
      content: (
        <TooltipBody
          title={bucketLabel(r.date)}
          caption={STATUS_LABEL[r.status]}
          rows={[
            { label: "AskDIQ estimate", value: fmtEst(r.estimate), color: estColor },
            { label: "Azure billed", value: r.billed === null ? "not billed yet" : fmtBilled(r.billed), color: billedColor },
            { label: "Variance", value: formatVariance(r.variance_pct) },
          ]}
        />
      ),
    };
  };

  return (
    <ChartFrame
      label="Daily billed vs estimated cost"
      height={CHART_HEIGHT}
      tooltip={tooltip}
      description={axisDescription(AXES)}
      footer={
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-fg-muted" data-testid="billed-legend">
          <LegendSwatch color={estColor} label="AskDIQ estimate (live)" />
          <LegendSwatch color={billedColor} label="Azure billed (lags 8–72 h)" />
          <LegendSwatch color={billedColor} dashed label="Not billed yet" />
        </div>
      }
    >
      {(size) => {
        const { area, band, v, barW, x0 } = layout(size);
        return (
          <>
            <AxisTitles {...AXES} area={area} height={size.height} ct={ct} />
            <YGrid area={area} max={max || 1} ct={ct} format={fmtEst} />
            <XLabels
              labels={rows.map((r) => bucketLabel(r.date))}
              xAt={(i) => area.x0 + band * i + band / 2}
              area={area}
              ct={ct}
            />
            {rows.map((r, i) => {
              const pending = r.status === "pending" || r.status === "no_data";
              const estTop = v(r.estimate);
              const billedTop = v(r.billed ?? 0);
              const placeholderTop = v(Math.max(r.billed ?? 0, r.estimate));
              return (
                <g key={r.date} data-status={r.status}>
                  <rect
                    x={x0(i)}
                    y={estTop}
                    width={barW}
                    height={Math.max(0, area.y1 - estTop)}
                    rx={2}
                    fill={estColor}
                    opacity={active === null || active === i ? 1 : ct.dimOpacity}
                  />
                  {pending ? (
                    <rect
                      data-testid="billed-pending"
                      x={x0(i) + barW}
                      y={placeholderTop}
                      width={barW}
                      height={Math.max(1, area.y1 - placeholderTop)}
                      rx={2}
                      fill={r.billed ? billedColor : "none"}
                      fillOpacity={0.35}
                      stroke={billedColor}
                      strokeDasharray="3 3"
                    />
                  ) : (
                    <rect
                      x={x0(i) + barW}
                      y={billedTop}
                      width={barW}
                      height={Math.max(0, area.y1 - billedTop)}
                      rx={2}
                      fill={billedColor}
                      opacity={active === null || active === i ? 1 : ct.dimOpacity}
                    />
                  )}
                  <rect
                    {...getMarkProps(i)}
                    x={x0(i)}
                    y={area.y0}
                    width={barW * 2}
                    height={area.y1 - area.y0}
                    fill="transparent"
                  />
                </g>
              );
            })}
          </>
        );
      }}
    </ChartFrame>
  );
}

function LegendSwatch({ color, label, dashed = false }: { color: string; label: string; dashed?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden="true"
        className="inline-block h-2.5 w-2.5 rounded-sm"
        style={dashed ? { border: `1px dashed ${color}` } : { background: color }}
      />
      {label}
    </span>
  );
}
