"use client";

/**
 * Usage & Cost > Overview "Azure-reported tokens": tokens Azure Monitor
 * counted per deployment over the last 7 UTC days (hourly sparkline), with
 * the share AskDIQ did not record ("not seen by AskDIQ": other callers,
 * playground use, failed or retried calls), and per day as recorded vs not
 * seen. Snapshot reads only; refreshed live after each metrics poll.
 */
import React, { useMemo } from "react";
import { ChartCard, toChartTable } from "@/components/charts/ChartCard";
import { Sparkline, type SparkPoint } from "@/components/charts/Sparkline";
import { StackedBar } from "@/components/charts/StackedBar";
import { StudioPill } from "@/components/ui/StudioCard";
import { useAuth } from "@/lib/auth-context";
import { useApiQuery } from "@/lib/query-cache";
import { useChartTheme } from "@/lib/use-chart-theme";
import { formatCount, formatPct, formatUsd } from "@/lib/governance-format";
import {
  AZURE_SUMMARY_DAYS,
  TOKENS_SERIES,
  azureApi,
  azureKeys,
  validSummary,
  type AzureMetricSeries,
  type AzureMetricsSummary,
  type DeploymentTokens,
} from "@/lib/azure-metrics-api";
import {
  AzureStatePills,
  NOT_CONNECTED_MESSAGE,
  RoleChip,
  dayLabel,
  isNotConnected,
} from "@/components/admin/azure/azure-format";

export interface DailyTokens {
  day: string;
  label: string;
  recorded: number;
  notSeen: number;
}

/** Per UTC day (oldest first): Azure tokens AskDIQ recorded vs did not see. */
export function dailyTokens(summary: AzureMetricsSummary | undefined): DailyTokens[] {
  const byDay = new Map<string, DailyTokens>();
  for (const row of summary?.daily ?? []) {
    if (!row.day) continue;
    const azure = (row.tokens_in.azure ?? 0) + (row.tokens_out.azure ?? 0);
    if (row.tokens_in.azure === null && row.tokens_out.azure === null) continue;
    const notSeen = Math.min(azure, row.not_seen_tokens ?? 0);
    const item = byDay.get(row.day) ?? { day: row.day, label: dayLabel(row.day), recorded: 0, notSeen: 0 };
    item.recorded += azure - notSeen;
    item.notSeen += notSeen;
    byDay.set(row.day, item);
  }
  return Array.from(byDay.values()).sort((a, b) => a.day.localeCompare(b.day));
}

/** Hourly Azure tokens of one deployment (all status series summed). */
export function sparkFor(series: AzureMetricSeries | undefined, deployment: string): SparkPoint[] {
  const points = new Map<string, number>();
  for (const s of series?.series ?? []) {
    if (s.deployment.toLowerCase() !== deployment.toLowerCase()) continue;
    for (const p of s.points) points.set(p.t, (points.get(p.t) ?? 0) + (p.total ?? 0));
  }
  return Array.from(points.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([t, value]) => ({ label: t.slice(0, 16).replace("T", " "), value }));
}

/** Preview FoundryModelEstimatedCost per deployment over the window (USD). */
export function previewCost(summary: AzureMetricsSummary | undefined): Map<string, number> {
  const out = new Map<string, number>();
  for (const row of summary?.daily ?? []) {
    if (row.estimated_cost_usd === null) continue;
    out.set(row.deployment, (out.get(row.deployment) ?? 0) + row.estimated_cost_usd);
  }
  return out;
}

function DeploymentRow({
  item,
  spark,
  cost,
}: {
  item: DeploymentTokens;
  spark: SparkPoint[];
  cost: number | undefined;
}) {
  const unseen = item.not_seen_tokens ?? 0;
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_96px_auto] items-center gap-3 py-2" data-testid="azure-token-row">
      <div className="min-w-0">
        <div className="truncate text-sm font-medium text-fg" title={item.deployment}>
          {item.deployment}
        </div>
        <RoleChip role={item.role} />
      </div>
      <div className="h-8 w-24">
        {spark.length > 1 ? (
          <Sparkline data={spark} height={32} label={`${item.deployment} tokens per hour, last 7 days`} formatValue={formatCount} />
        ) : null}
      </div>
      <div className="text-right tabular-nums">
        <div className="font-mono text-sm font-bold text-fg">{formatCount(item.azure_tokens)}</div>
        <div className={`text-[11px] ${unseen > 0 ? "text-status-warn-fg" : "text-fg-subtle"}`} data-testid="azure-not-seen">
          {formatCount(unseen)} not seen by AskDIQ{item.not_seen_pct !== null ? ` (${formatPct(item.not_seen_pct)})` : ""}
        </div>
        {cost !== undefined ? (
          <div className="text-[11px] text-fg-subtle" data-testid="azure-preview-cost">
            ~{formatUsd(cost)} <StudioPill tone="info">Preview estimate</StudioPill>
          </div>
        ) : null}
      </div>
    </li>
  );
}

export function AzureTokensPanel() {
  const { token } = useAuth();
  const ct = useChartTheme();
  const summaryQuery = useApiQuery<AzureMetricsSummary>(
    azureKeys.summary(AZURE_SUMMARY_DAYS),
    (signal) => azureApi.summary(token, AZURE_SUMMARY_DAYS, signal),
    { enabled: Boolean(token), revalidateOnFocus: false },
  );
  const seriesQuery = useApiQuery<AzureMetricSeries>(
    azureKeys.series(TOKENS_SERIES),
    (signal) => azureApi.series(token, TOKENS_SERIES, signal),
    { enabled: Boolean(token), revalidateOnFocus: false },
  );
  const summary = validSummary(summaryQuery.data);
  const days = useMemo(() => dailyTokens(summary), [summary]);
  const costs = useMemo(() => previewCost(summary), [summary]);
  const tokens = summary?.tokens ?? [];
  const notConnected = isNotConnected(summary);
  const loading = summaryQuery.data === undefined && !summaryQuery.error;
  const error = summary ? null : summaryQuery.error ? "Couldn't load Azure-reported tokens" : null;
  const windowLabel = `last ${summary?.days ?? AZURE_SUMMARY_DAYS} days (UTC)`;
  const emptyMessage = notConnected ? NOT_CONNECTED_MESSAGE : "Azure has not reported tokens in this window yet";

  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-2" data-testid="azure-tokens" data-tour="usage-azure-tokens">
      <ChartCard
        variant="aurora"
        title="Azure-reported tokens"
        subtitle="Azure Monitor tokens per deployment and the share AskDIQ did not record"
        window={windowLabel}
        meta={<AzureStatePills summary={summary} />}
        loading={loading}
        error={error}
        onRetry={() => void summaryQuery.refetch()}
        empty={!summary || notConnected || tokens.length === 0}
        emptyMessage={emptyMessage}
        bodyClassName="w-full"
        table={toChartTable(
          tokens.map((t) => ({
            deployment: t.deployment,
            azure: formatCount(t.azure_tokens),
            askdiq: formatCount(t.askdiq_tokens),
            unseen: formatCount(t.not_seen_tokens),
          })),
          [
            { key: "deployment", label: "Deployment" },
            { key: "azure", label: "Azure tokens", numeric: true },
            { key: "askdiq", label: "AskDIQ tokens", numeric: true },
            { key: "unseen", label: "Not seen by AskDIQ", numeric: true },
          ],
        )}
      >
        <ul className="divide-y divide-line">
          {tokens.map((item) => (
            <DeploymentRow
              key={item.deployment}
              item={item}
              spark={sparkFor(seriesQuery.data, item.deployment)}
              cost={costs.get(item.deployment)}
            />
          ))}
        </ul>
      </ChartCard>

      <ChartCard
        variant="aurora"
        title="Recorded vs not seen"
        subtitle="Azure tokens per day: recorded by AskDIQ and not seen by AskDIQ"
        window={windowLabel}
        loading={loading}
        error={error}
        onRetry={() => void summaryQuery.refetch()}
        empty={!summary || notConnected || days.length === 0}
        emptyMessage={emptyMessage}
        table={toChartTable(days, [
          { key: "label", label: "Day (UTC)" },
          { key: "recorded", label: "Recorded by AskDIQ", numeric: true, format: (v: unknown) => formatCount(Number(v)) },
          { key: "notSeen", label: "Not seen by AskDIQ", numeric: true, format: (v: unknown) => formatCount(Number(v)) },
        ])}
      >
        <StackedBar
          data={days}
          xKey="label"
          series={[
            { key: "recorded", label: "Recorded by AskDIQ", color: ct.categorical[0] },
            { key: "notSeen", label: "Not seen by AskDIQ", color: ct.status.warn },
          ]}
          formatValue={formatCount}
          label="Azure-reported tokens per day, recorded vs not seen by AskDIQ"
          xAxisLabel="Date (UTC)"
          yAxisLabel="Tokens per day"
        />
      </ChartCard>
    </div>
  );
}
