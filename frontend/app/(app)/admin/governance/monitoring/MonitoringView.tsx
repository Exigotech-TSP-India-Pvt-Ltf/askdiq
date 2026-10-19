"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Cpu,
  Database,
  FileSearch,
  Gauge,
  Loader,
  Search,
  Timer,
  Users,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { useApiQuery } from "@/lib/query-cache";
import { chartTooltipStyle, useChartTheme } from "@/lib/use-chart-theme";
import {
  STAGES,
  TRACE_OUTCOMES,
  governanceApi,
  governanceKeys,
  type GovernanceMonitoring,
  type GovernanceStreamEvent,
  type LabelledCount,
  type QuotaMetrics,
  type ResourcesSnapshot,
  type Stage,
  type TraceOutcome,
} from "@/lib/governance-api";
import { resourceGauges } from "@/lib/resource-gauges";
import { logsHref, tabHref, tracesHref } from "@/lib/governance-links";
import { useCarryHref } from "@/lib/entity-routes";
import {
  axisWidthFor,
  formatCount,
  formatHour,
  formatMs,
  formatPct,
  humanize,
  latencyTick,
  niceTicks,
  windowSourceLabel,
} from "@/lib/governance-format";
import { useGovernanceStream } from "@/lib/use-governance-stream";
import { ChartCard, toChartTable } from "@/components/charts/ChartCard";
import { PercentileBand, type PercentilePoint } from "@/components/charts/PercentileBand";
import { StackedArea } from "@/components/charts/StackedArea";
import { RadialGauge } from "@/components/charts/RadialGauge";
import { ErrorState } from "@/components/ui/ErrorState";
import { stageLabel } from "@/components/admin/governance/TraceDrawer";
import { LiveIndicator } from "@/components/admin/governance/badges";
import { ActiveVisitorsInfo } from "@/components/admin/ActiveVisitorsPanel";
import { ACTIVE_VISITORS_LABEL } from "@/lib/active-visitors";

/** SSE events arrive every few seconds; refetch the full snapshot at most this often. */
export const MONITORING_REFETCH_MIN_MS = 5_000;

// --- pure helpers (exported for tests) ---------------------------------------

export interface StageRow {
  stage: Stage;
  label: string;
  avg: number | null;
  p50: number | null;
  p95: number | null;
  p99: number | null;
  samples: number;
}

/** One row per pipeline stage; a stage without samples is null (G2), never 0. */
export function stageRows(m: Pick<GovernanceMonitoring, "stage_latency" | "stage_percentiles">): StageRow[] {
  return STAGES.map((stage) => {
    const avg = m.stage_latency?.[stage];
    const pct = m.stage_percentiles?.[stage];
    const samples = avg?.sample_count ?? pct?.sample_count ?? 0;
    return {
      stage,
      label: stageLabel(stage),
      avg: samples > 0 ? (avg?.avg_ms ?? null) : null,
      p50: pct?.p50_ms ?? null,
      p95: pct?.p95_ms ?? null,
      p99: pct?.p99_ms ?? null,
      samples,
    };
  });
}

/** Utilisation % (100 - worst headroom) for one quota dimension, or null. */
export function quotaUtilisation(
  telemetry: { chat?: QuotaMetrics; embedding?: QuotaMetrics } | undefined,
  key: "tpm_headroom_pct" | "rpm_headroom_pct",
): number | null {
  const values = [telemetry?.chat?.[key], telemetry?.embedding?.[key]].filter(
    (v): v is number => typeof v === "number" && Number.isFinite(v),
  );
  if (values.length === 0) return null;
  return Math.max(0, Math.min(100, 100 - Math.min(...values)));
}

// --- small pieces ---------------------------------------------------------------

/** A count tile; it links to the rows behind the number (the hover lift promises a click). */
function StatTile({
  label,
  value,
  caption,
  icon: Icon,
  href,
  info,
}: {
  label: string;
  value: string;
  caption: string;
  icon: LucideIcon;
  href: string;
  /** Definition tip, rendered beside the tile (outside its link). */
  info?: React.ReactNode;
}) {
  const carry = useCarryHref();
  const tile = (
    <Link
      href={carry(href)}
      aria-label={`${label}: ${value}. Open the rows behind it`}
      className="group flex flex-col gap-1.5 rounded-xl border border-line bg-surface-card p-4 transition duration-150 hover:border-brand-300 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 motion-safe:hover:-translate-y-0.5 motion-reduce:transition-none dark:hover:border-brand-700/60"
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-fg-subtle">{label}</p>
        <Icon className="h-4 w-4 text-fg-subtle group-hover:text-accent-fg" strokeWidth={1.75} aria-hidden="true" />
      </div>
      <p className="text-2xl font-semibold tabular-nums text-fg">{value}</p>
      <p className="text-[11px] text-fg-subtle" data-testid="count-label">
        {caption}
      </p>
    </Link>
  );
  if (!info) return tile;
  return (
    <div className="relative">
      {tile}
      <div className="absolute right-10 top-4 z-10">{info}</div>
    </div>
  );
}

function isTraceOutcome(value: string): value is TraceOutcome {
  return (TRACE_OUTCOMES as readonly string[]).includes(value);
}

function countCaption(c: LabelledCount | undefined): string {
  return c ? windowSourceLabel(c.window, c.source) : "—";
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface-card px-4 py-3">
      <p className="text-[11px] uppercase tracking-wide text-fg-subtle">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums text-fg">{value}</p>
    </div>
  );
}

/** G1 + G2: ms / s ticks, an axis wide enough for its labels, "no data" stages. */
function StageLatencyChart({ rows }: { rows: StageRow[] }) {
  const ct = useChartTheme();
  const max = Math.max(0, ...rows.map((r) => r.avg ?? 0));
  const ticks = niceTicks(max);
  const width = axisWidthFor(ticks.map(latencyTick));
  const byLabel = new Map(rows.map((r) => [r.label, r]));
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={ct.grid} vertical={false} />
        <XAxis
          dataKey="label"
          interval={0}
          tick={{ fontSize: 11, fill: ct.axis }}
          tickFormatter={(label: string) => (byLabel.get(label)?.avg === null ? `${label} (no data)` : label)}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          domain={[0, ticks[ticks.length - 1]]}
          ticks={ticks}
          tickFormatter={latencyTick}
          tick={{ fontSize: 11, fill: ct.axis }}
          axisLine={false}
          tickLine={false}
          width={width}
        />
        <Tooltip
          contentStyle={chartTooltipStyle(ct)}
          formatter={(v) => [formatMs(typeof v === "number" ? v : null), "Average"]}
        />
        <Bar dataKey="avg" radius={[6, 6, 0, 0]} isAnimationActive={false}>
          {rows.map((r, idx) => (
            <Cell key={r.stage} fill={ct.categorical[idx % ct.categorical.length]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function ShareBars({
  data,
  label,
  emptyText,
  hrefFor,
}: {
  data: Record<string, number>;
  label: string;
  emptyText: string;
  /** Link for a row (its traces); rows without one stay plain text. */
  hrefFor?: (name: string) => string | null;
}) {
  const ct = useChartTheme();
  const carry = useCarryHref();
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((s, [, v]) => s + v, 0);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  if (entries.length === 0) return <p className="py-8 text-center text-xs text-fg-subtle">{emptyText}</p>;
  return (
    <ul className="space-y-2.5" aria-label={label}>
      {entries.map(([name, value], i) => (
        <li key={name} className="grid grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-3 text-xs">
          {hrefFor?.(name) ? (
            <Link
              href={carry(hrefFor(name) ?? "")}
              title={`Open ${humanize(name)} traces`}
              className="truncate rounded text-accent-fg hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              {humanize(name)}
            </Link>
          ) : (
            <span className="truncate text-fg-muted" title={name}>
              {humanize(name)}
            </span>
          )}
          <span className="h-2.5 overflow-hidden rounded-full bg-surface-muted" aria-hidden="true">
            <span
              className="block h-full rounded-full"
              style={{ width: `${(value / max) * 100}%`, backgroundColor: ct.categorical[i % ct.categorical.length] }}
            />
          </span>
          <span className="tabular-nums text-fg">
            {formatCount(value)}
            <span className="ml-1 text-fg-subtle">({total ? formatPct((value / total) * 100, 0) : "—"})</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

const USAGE_TABS = [
  { key: "llm", label: "LLM Usage", icon: Cpu },
  { key: "embedding", label: "Embedding Usage", icon: Database },
] as const;
type UsageTabKey = (typeof USAGE_TABS)[number]["key"];
type UsageStats = Record<string, number | null | undefined>;

function num(v: number | null | undefined): string {
  return typeof v === "number" ? v.toLocaleString() : "—";
}

function AiUsagePanel() {
  const { token } = useAuth();
  const [usageTab, setUsageTab] = useState<UsageTabKey>("llm");
  const llm = useApiQuery<UsageStats>(["admin/usage/llm"], () => api.adminGet<UsageStats>(token ?? "", "/usage/llm"), {
    enabled: Boolean(token),
  });
  const embedding = useApiQuery<UsageStats>(
    ["admin/usage/embedding"],
    () => api.adminGet<UsageStats>(token ?? "", "/usage/embedding"),
    { enabled: Boolean(token) },
  );
  const l = llm.data ?? {};
  const e = embedding.data ?? {};

  return (
    <section aria-labelledby="ai-usage" data-tour="governance-mon-ai-usage" className="rounded-xl border border-line bg-surface-card p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="ai-usage" className="text-base font-semibold text-fg">
            AI Usage
          </h2>
          <p className="mt-1 text-xs text-fg-subtle">Model usage from the usage records.</p>
        </div>
        <div role="tablist" aria-label="AI usage" className="flex flex-wrap gap-1 rounded-lg bg-surface-muted p-1">
          {USAGE_TABS.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={usageTab === tab.key}
                onClick={() => setUsageTab(tab.key)}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${
                  usageTab === tab.key ? "bg-surface-card text-fg shadow-sm" : "text-fg-subtle hover:text-fg"
                }`}
              >
                <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                {tab.label}
              </button>
            );
          })}
        </div>
      </div>
      {usageTab === "llm" ? (
        <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MiniStat label="Requests" value={num(l.requests)} />
          <MiniStat label="Successful" value={num(l.successful)} />
          <MiniStat label="Failed" value={num(l.failed)} />
          <MiniStat label="Average Latency" value={formatMs(l.average_latency_ms ?? null)} />
          <MiniStat label="Input Tokens" value={num(l.input_tokens)} />
          <MiniStat label="Output Tokens" value={num(l.output_tokens)} />
          <MiniStat label="Total Tokens" value={num(l.total_tokens)} />
          <MiniStat
            label="Success Rate"
            value={typeof l.requests === "number" && l.requests > 0 && typeof l.successful === "number" ? formatPct((l.successful / l.requests) * 100) : "—"}
          />
        </div>
      ) : (
        <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3">
          <MiniStat label="Dimensions" value={num(e.dimensions)} />
          <MiniStat label="Documents" value={num(e.documents)} />
          <MiniStat label="Chunks" value={num(e.chunks)} />
          <MiniStat label="Requests" value={num(e.requests)} />
          <MiniStat label="Input Tokens" value={num(e.input_tokens)} />
          <MiniStat label="Average Latency" value={formatMs(e.average_latency_ms ?? null)} />
        </div>
      )}
    </section>
  );
}

const nullableMs = (v: unknown) => (typeof v === "number" ? formatMs(v) : "no data");

// --- view -------------------------------------------------------------------

export default function MonitoringView() {
  const { token } = useAuth();
  const { audience } = useAudience();
  const ct = useChartTheme();
  const query = useApiQuery<GovernanceMonitoring>(
    governanceKeys.monitoring(audience),
    (signal) => governanceApi.monitoring(token, audience, signal),
    { enabled: Boolean(token), revalidateOnFocus: false },
  );
  const { refetch } = query;

  // G13: SSE replaces the 10 s polling. Change events trigger at most one
  // refetch per MONITORING_REFETCH_MIN_MS; the stream pauses while hidden.
  const lastRefetch = useRef(0);
  useEffect(() => {
    lastRefetch.current = 0;
  }, [audience]);
  // Resource gauges update in place from the stream (every sample interval);
  // the REST snapshot is the fallback when the stream is not live.
  const [liveResources, setLiveResources] = useState<Partial<ResourcesSnapshot> | null>(null);
  const onEvent = useCallback(
    (event: GovernanceStreamEvent) => {
      if (event.type === "resources") {
        setLiveResources(event.data);
        return;
      }
      const now = Date.now();
      if (event.type === "snapshot" && lastRefetch.current === 0) {
        lastRefetch.current = now;
        return;
      }
      if (now - lastRefetch.current < MONITORING_REFETCH_MIN_MS) return;
      lastRefetch.current = now;
      void refetch();
    },
    [refetch],
  );
  const stream = useGovernanceStream({ audience, onEvent, enabled: Boolean(token) });

  const m = query.data;
  const rows = useMemo(() => (m ? stageRows(m) : []), [m]);
  const stageBand: PercentilePoint[] = rows.flatMap((r) =>
    r.p50 === null || r.p95 === null
      ? []
      : [{ label: r.label, p50: r.p50, p95: r.p95, ...(r.p99 === null ? {} : { p99: r.p99 }) }],
  );
  const hourly = m?.hourly ?? [];
  const latencyBand: PercentilePoint[] = hourly.flatMap((h) =>
    h.latency_p50_ms === null || h.latency_p95_ms === null
      ? []
      : [{ label: formatHour(h.hour), p50: h.latency_p50_ms, p95: h.latency_p95_ms }],
  );
  const traffic = hourly.map((h) => ({ hour: formatHour(h.hour), ok: Math.max(0, h.requests - h.errors), errors: h.errors }));

  if (query.error && !m) {
    return (
      <ErrorState
        title="Couldn't load monitoring data"
        message="The monitoring endpoint did not respond."
        onRetry={() => void query.refetch()}
      />
    );
  }

  const loading = !m;
  const counts = m?.counts ?? {};
  const total = m?.stage_percentiles?.total;
  const quality = m?.online_quality_kpis ?? {};
  const resources = liveResources ?? m?.resources;
  const gauges = resourceGauges(resources);
  const sampleEvery = resources?.interval_seconds;
  const quota = m?.azure_quota_telemetry;
  const throttled = (quota?.chat?.azure_429_count_60s ?? 0) + (quota?.embedding?.azure_429_count_60s ?? 0);
  const windowH = m?.window_hours ?? 24;
  const noDataStages = rows.filter((r) => r.p50 === null).map((r) => r.label);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-fg-subtle">
          Rolling {windowH} h snapshot. Latency is measured in milliseconds; each count shows its window and source.
        </p>
        <LiveIndicator status={stream.status} updatedAt={query.updatedAt || undefined} />
      </div>

      {/* G8: a balanced 4 x 2 grid (no dead slot) at the same width as AI Usage. */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-busy={loading || undefined} data-tour="governance-mon-tiles">
        <StatTile label="API requests" href={logsHref()} icon={Activity} value={formatCount(counts.api_requests?.value ?? m?.api_requests)} caption={countCaption(counts.api_requests)} />
        <StatTile label="Queries" href={tabHref("Traces")} icon={Search} value={formatCount(counts.queries_24h?.value ?? m?.queries_24h)} caption={countCaption(counts.queries_24h)} />
        <StatTile label={ACTIVE_VISITORS_LABEL} info={<ActiveVisitorsInfo />} href="/admin/usage?tab=users" icon={Users} value={formatCount(counts.active_users?.value ?? m?.active_users)} caption={countCaption(counts.active_users)} />
        <StatTile label="Traces" href={tabHref("Traces")} icon={FileSearch} value={formatCount(counts.traces_total?.value ?? m?.traces_total)} caption={countCaption(counts.traces_total)} />
        <StatTile
          label="Error rate"
          href={tracesHref({ status: "failed" })}
          icon={AlertTriangle}
          value={formatPct(m?.error_rate, 2)}
          caption={`${formatCount(counts.errors?.value)} errors · ${countCaption(counts.errors)}`}
        />
        <StatTile label="Average latency" href={tabHref("Traces")} icon={Timer} value={formatMs(m?.total_latency_ms ?? null)} caption={`end to end · ${windowH}h · latency records`} />
        <StatTile
          label="p95 latency"
          href={tracesHref({ latency_bucket: "slow" })}
          icon={Gauge}
          value={formatMs(total?.p95_ms ?? m?.p95_latency_ms ?? null)}
          caption={`${formatCount(total?.sample_count ?? 0)} samples · ${windowH}h · latency records`}
        />
        <StatTile label="Active queries" href={tabHref("Traces")} icon={Loader} value={formatCount(counts.active_queries?.value ?? m?.active_queries)} caption={countCaption(counts.active_queries)} />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2" data-tour="governance-mon-latency">
        <ChartCard
          title="Pipeline latency"
          subtitle="Average per stage"
          window={`${windowH}h`}
          loading={loading}
          empty={!loading && rows.every((r) => r.avg === null)}
          emptyMessage="No stage timings recorded in this window"
          table={toChartTable(rows, [
            { key: "label", label: "Stage" },
            { key: "avg", label: "Average", numeric: true, format: nullableMs },
            { key: "samples", label: "Samples", numeric: true },
          ])}
        >
          <StageLatencyChart rows={rows} />
        </ChartCard>

        <ChartCard
          title="Stage percentiles"
          subtitle="p50 to p95 band with dashed p99, per stage"
          window={`${windowH}h`}
          loading={loading}
          empty={!loading && stageBand.length === 0}
          emptyMessage="No stage samples in this window"
          table={toChartTable(rows, [
            { key: "label", label: "Stage" },
            { key: "p50", label: "p50", numeric: true, format: nullableMs },
            { key: "p95", label: "p95", numeric: true, format: nullableMs },
            { key: "p99", label: "p99", numeric: true, format: nullableMs },
            { key: "samples", label: "Samples", numeric: true },
          ])}
        >
          <div className="flex h-full flex-col">
            <div className="min-h-0 flex-1">
              <PercentileBand data={stageBand} label="Stage latency percentiles" formatValue={latencyTick} />
            </div>
            {noDataStages.length > 0 ? (
              <p className="mt-1 text-[11px] text-fg-subtle">No data: {noDataStages.join(", ")}</p>
            ) : null}
          </div>
        </ChartCard>

        <ChartCard
          title="Latency over time"
          subtitle="End-to-end p50 to p95, hourly"
          window={`${windowH}h`}
          loading={loading}
          empty={!loading && latencyBand.length === 0}
          table={toChartTable(latencyBand, [
            { key: "label", label: "Hour" },
            { key: "p50", label: "p50", numeric: true, format: nullableMs },
            { key: "p95", label: "p95", numeric: true, format: nullableMs },
          ])}
        >
          <PercentileBand data={latencyBand} label="Hourly latency percentiles" formatValue={latencyTick} />
        </ChartCard>

        <ChartCard
          title="Requests and errors"
          subtitle="Assistant turns plus server errors, hourly"
          window={`${windowH}h`}
          loading={loading}
          empty={!loading && traffic.every((t) => t.ok + t.errors === 0)}
          table={toChartTable(traffic, [
            { key: "hour", label: "Hour" },
            { key: "ok", label: "Succeeded", numeric: true },
            { key: "errors", label: "Errors", numeric: true },
          ])}
        >
          <StackedArea
            data={traffic}
            xKey="hour"
            label="Requests and errors per hour"
            series={[
              { key: "ok", label: "Succeeded" },
              { key: "errors", label: "Errors", color: ct.status.danger },
            ]}
          />
        </ChartCard>
      </div>

      <section aria-labelledby="gov-resources" data-tour="governance-mon-resources" className="rounded-xl border border-line bg-surface-card p-6">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="gov-resources" className="text-base font-semibold text-fg">
            Resources and Azure quota
          </h2>
          <p className="text-[11px] text-fg-subtle">
            All API workers{sampleEvery ? ` · sampled every ${sampleEvery} s` : ""} · Azure rolling 60 s window
            {throttled ? ` · ${throttled} throttled (429)` : ""}
          </p>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-4 xl:grid-cols-4">
          {gauges.map((g) => (
            <RadialGauge key={g.key} label={g.label} value={g.value} bands={g.bands} higherIsBetter={false} caption={g.caption} />
          ))}
          <RadialGauge
            label="Azure TPM used"
            value={quotaUtilisation(quota, "tpm_headroom_pct")}
            bands={{ warn: 70, danger: 90 }}
            higherIsBetter={false}
            caption="Azure TPM"
          />
          <RadialGauge
            label="Azure RPM used"
            value={quotaUtilisation(quota, "rpm_headroom_pct")}
            bands={{ warn: 70, danger: 90 }}
            higherIsBetter={false}
            caption="Azure RPM"
          />
        </div>
      </section>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2" data-tour="governance-mon-guardrails">
        <ChartCard
          title="Guardrail flags"
          subtitle="Flags raised on assistant turns"
          window={`${windowH}h`}
          loading={loading}
          bodyClassName="w-full"
          table={toChartTable(
            Object.entries(m?.guardrail_flag_breakdown ?? {}).map(([flag, count]) => ({ flag: humanize(flag), count })),
            [
              { key: "flag", label: "Flag" },
              { key: "count", label: "Turns", numeric: true },
            ],
          )}
        >
          <ShareBars
            data={m?.guardrail_flag_breakdown ?? {}}
            label="Guardrail flags"
            emptyText="No guardrail flags in this window."
            hrefFor={() => tracesHref({ status: "blocked" })}
          />
        </ChartCard>
        <ChartCard
          title="Outcome mix"
          subtitle="Assistant turns by outcome"
          window={`${windowH}h`}
          loading={loading}
          bodyClassName="w-full"
          table={toChartTable(
            Object.entries(m?.outcome_mix ?? {}).map(([outcome, count]) => ({ outcome: humanize(outcome), count })),
            [
              { key: "outcome", label: "Outcome" },
              { key: "count", label: "Turns", numeric: true },
            ],
          )}
        >
          <ShareBars
            data={m?.outcome_mix ?? {}}
            label="Outcome mix"
            emptyText="No assistant turns in this window."
            hrefFor={(name) => (isTraceOutcome(name) ? tracesHref({ outcome: name }) : null)}
          />
        </ChartCard>
      </div>

      <section aria-labelledby="gov-quality" data-tour="governance-mon-online-quality" className="rounded-xl border border-line bg-surface-card p-6">
        <h2 id="gov-quality" className="text-base font-semibold text-fg">
          Online quality
        </h2>
        <p className="mt-1 text-xs text-fg-subtle">Sampled online evaluation of live answers, since process start.</p>
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
          <MiniStat label="Sampled" value={formatCount(quality.sampled_count ?? null)} />
          <MiniStat label="Violation rate" value={quality.violation_rate === undefined ? "—" : formatPct(quality.violation_rate * 100)} />
          <MiniStat
            label="Mean faithfulness"
            value={typeof quality.mean_faithfulness === "number" ? `${quality.mean_faithfulness.toFixed(2)} / 5` : "—"}
          />
          <MiniStat label="Unfaithful" value={formatCount(quality.unfaithful_count ?? null)} />
          <MiniStat label="PII leaks" value={formatCount(quality.pii_leak_count ?? null)} />
          <MiniStat label="Secret leaks" value={formatCount(quality.secret_leak_count ?? null)} />
        </div>
      </section>

      <AiUsagePanel />
    </div>
  );
}
