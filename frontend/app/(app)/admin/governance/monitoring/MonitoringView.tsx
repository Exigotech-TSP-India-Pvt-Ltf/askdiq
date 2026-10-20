"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BadgeCheck,
  Coins,
  Cpu,
  Database,
  FileSearch,
  FileText,
  Gauge,
  Hash,
  KeyRound,
  Layers,
  Loader,
  MemoryStick,
  PieChart,
  Scale,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sigma,
  Timer,
  TriangleAlert,
  Users,
  XCircle,
  Zap,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { m } from "motion/react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { useApiQuery } from "@/lib/query-cache";
import { useChartTheme } from "@/lib/use-chart-theme";
import type { ChartTheme } from "@/lib/theme-tokens";
import { cardEnter, staggerFor, useMotionVariants } from "@/lib/motion";
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
import { OUTCOMES, OUTCOME_LABELS } from "@/lib/entity-stats-api";
import { quotaTrend, resourceGauges, type TrendPoint } from "@/lib/resource-gauges";
import { ResourceTrend } from "@/components/admin/governance/ResourceTrend";
import { AzureConnectionCard } from "@/components/admin/governance/AzureConnectionCard";
import { AzureMonitoringSection } from "@/components/admin/governance/AzurePanels";
import { quotaLimitCaption, usesAzureLimits } from "@/lib/azure-capacity-api";
import { logsHref, tabHref, tracesHref } from "@/lib/governance-links";
import { useCarryHref } from "@/lib/entity-routes";
import {
  formatCount,
  formatCountExact,
  formatHour,
  formatMs,
  formatPct,
  humanize,
  latencyTick,
  windowSourceLabel,
} from "@/lib/governance-format";
import { useGovernanceStream } from "@/lib/use-governance-stream";
import { ChartCard, toChartTable } from "@/components/charts/ChartCard";
import { BarList, type BarDatum } from "@/components/charts/BarList";
import { StackedBar } from "@/components/charts/StackedBar";
import { PercentileBand, type PercentilePoint } from "@/components/charts/PercentileBand";
import { StackedArea } from "@/components/charts/StackedArea";
import { RadialGauge, type GaugeBands } from "@/components/charts/RadialGauge";
import { StatCard } from "@/components/admin/insights/StatCard";
import { StudioPill, StudioSectionHeader, StudioSegmented, studioCardClass } from "@/components/ui/StudioCard";
import { CountUp } from "@/components/ui/CountUp";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { stageLabel } from "@/components/admin/governance/TraceDrawer";
import { LiveIndicator } from "@/components/admin/governance/badges";
import { ActiveVisitorsInfo } from "@/components/admin/ActiveVisitorsPanel";
import { AzureReconciliationPanel } from "@/components/admin/azure/AzureReconciliationPanel";
import { ACTIVE_VISITORS_LABEL } from "@/lib/active-visitors";

/** SSE events arrive every few seconds; refetch the full snapshot at most this often. */
export const MONITORING_REFETCH_MIN_MS = 5_000;

/** Values at or above this render compact ("19.5M") with the exact value in a tooltip. */
const COMPACT_FROM = 10_000;

/** Usage & Cost console (token and cost detail). */
const USAGE_COST_HREF = "/admin/usage";

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

const UNKNOWN_OUTCOME = "unknown";

/** Every outcome (zero rows kept, so the mix never collapses to one bar); legacy "unknown" only when present. */
export function outcomeRows(mix: Record<string, number> | undefined): BarDatum[] {
  const rows: BarDatum[] = OUTCOMES.map((o) => ({ key: o, label: OUTCOME_LABELS[o], value: mix?.[o] ?? 0 }));
  const unknown = mix?.[UNKNOWN_OUTCOME] ?? 0;
  if (unknown > 0) rows.push({ key: UNKNOWN_OUTCOME, label: "Unknown", value: unknown });
  return rows;
}

/** Guardrail flags, most frequent first. */
export function flagRows(flags: Record<string, number> | undefined): BarDatum[] {
  return Object.entries(flags ?? {})
    .sort((a, b) => b[1] - a[1])
    .map(([key, value]) => ({ key, label: humanize(key), value }));
}

export interface LlmUsage {
  window?: string;
  source?: string;
  latency_window_hours?: number;
  requests?: number | null;
  successful?: number | null;
  failed?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_tokens?: number | null;
  average_latency_ms?: number | null;
  outcomes?: Record<string, number>;
}

export interface LlmUsageSummary {
  requests: number | null;
  failed: number | null;
  succeeded: number | null;
  /** 0-100, or null without requests. */
  successRate: number | null;
  /** Turns that completed as designed without an answer from the documents (not failures). */
  blocked: number | null;
  conversational: number | null;
  ungrounded: number | null;
}

const asNum = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/**
 * Failed = real errors only (the LLM produced no answer). Blocked,
 * conversational and ungrounded turns are reported separately, never as
 * failures; requests and failures share one window (see the backend card).
 */
export function llmUsageSummary(u: LlmUsage | undefined): LlmUsageSummary {
  const requests = asNum(u?.requests);
  const failed = asNum(u?.failed);
  const succeeded = requests === null || failed === null ? null : Math.max(0, requests - failed);
  const outcome = (key: string) => (u?.outcomes ? (asNum(u.outcomes[key]) ?? 0) : null);
  return {
    requests,
    failed,
    succeeded,
    successRate: requests && succeeded !== null ? (succeeded / requests) * 100 : null,
    blocked: outcome("blocked"),
    conversational: outcome("conversational"),
    ungrounded: outcome("ungrounded"),
  };
}

/** `title` for a compact value: the exact number when compacting hides digits. */
export function exactTitle(value: number | null | undefined): string | undefined {
  return typeof value === "number" && Math.abs(value) >= COMPACT_FROM ? formatCountExact(value) : undefined;
}

// --- small pieces ---------------------------------------------------------------

/** Card entrance (fade + rise) inside a `Stagger` grid; opacity only under reduced motion. */
function Enter({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  const variants = useMotionVariants(cardEnter);
  return (
    <m.div variants={variants} className={className}>
      {children}
    </m.div>
  );
}

function Stagger({ children, className, ...rest }: { children: React.ReactNode; className: string } & Record<`data-${string}`, string>) {
  return (
    <m.div variants={staggerFor(React.Children.count(children))} initial="hidden" animate="visible" className={className} {...rest}>
      {children}
    </m.div>
  );
}

/** A count tile; it links to the rows behind the number (the hover lift promises a click). */
function StatTile({
  label,
  value,
  exact,
  caption,
  icon: Icon,
  href,
  info,
}: {
  label: string;
  value: string;
  /** Exact value tooltip when `value` is compact. */
  exact?: string;
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
      className={`group flex h-full flex-col gap-1.5 p-4 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${studioCardClass({ interactive: true })}`}
    >
      <div className="relative z-10 flex items-center justify-between gap-2">
        <p className="truncate text-xs font-medium text-fg-subtle">{label}</p>
        <Icon className="h-4 w-4 shrink-0 text-fg-subtle group-hover:text-accent-fg" strokeWidth={1.75} aria-hidden="true" />
      </div>
      <p className="relative z-10 truncate text-2xl font-semibold tabular-nums text-fg" title={exact}>
        {value}
      </p>
      <p className="relative z-10 truncate text-[11px] text-fg-subtle" data-testid="count-label">
        {caption}
      </p>
    </Link>
  );
  if (!info) return tile;
  return (
    <div className="relative h-full">
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

/**
 * Shared KPI card (StatCard) with a count-up value, its time window in the
 * caption, and an optional deep link (arrow + focus ring) to the rows behind it.
 */
function MetricCard({
  label,
  value,
  format = formatCount,
  caption,
  icon,
  href,
}: {
  label: string;
  value: number | null;
  format?: (value: number) => string;
  caption: string;
  icon: LucideIcon;
  href?: string;
}) {
  const carry = useCarryHref();
  const shown = value === null ? "—" : format(value);
  const card = (
    <StatCard
      title={label}
      icon={icon}
      className="h-full"
      value={
        <span title={format === formatCount ? exactTitle(value) : undefined} data-testid="metric-value">
          <CountUp value={value} format={format} />
        </span>
      }
      badge={
        href ? (
          <ArrowUpRight
            className="h-4 w-4 shrink-0 text-fg-subtle transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-accent-fg motion-reduce:transition-none"
            strokeWidth={1.75}
            aria-hidden="true"
          />
        ) : undefined
      }
      subtitle={caption}
    />
  );
  return (
    <Enter className="h-full">
      {href ? (
        <Link
          href={carry(href)}
          aria-label={`${label}: ${shown}, ${caption}. Open the rows behind it`}
          className="block h-full rounded-2xl focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
        >
          {card}
        </Link>
      ) : (
        card
      )}
    </Enter>
  );
}

/** Pipeline latency per stage (chart kit; G1 + G2: ms / s ticks, "no data" stages). */
function StageLatencyChart({ rows }: { rows: StageRow[] }) {
  const data = rows.map((r) => ({ label: r.label, avg: r.avg ?? 0 }));
  const noData = new Set(rows.filter((r) => r.avg === null).map((r) => r.label));
  return (
    <StackedBar
      data={data}
      xKey="label"
      series={[{ key: "avg", label: "Average" }]}
      formatLabel={(label) => (noData.has(label) ? `${label} (no data)` : label)}
      formatValue={latencyTick}
      label="Average latency per pipeline stage"
      xAxisLabel="Pipeline stage"
      yAxisLabel="Average time"
    />
  );
}

/** Outcome colours: answered ok, ungrounded warn, failed danger, others neutral. */
function outcomeColor(d: BarDatum, i: number, ct: ChartTheme): string {
  switch (d.key) {
    case "answered":
      return ct.status.ok;
    case "ungrounded":
      return ct.status.warn;
    case "blocked":
      return ct.status.info;
    case "llm_failed":
      return ct.status.danger;
    default:
      return ct.categorical[(i + 1) % ct.categorical.length];
  }
}

const shareOf = (value: number, total: number) => (total > 0 ? formatPct((value / total) * 100, 0) : "—");

/** Icon per live resource gauge (keys from lib/resource-gauges). */
const GAUGE_ICONS: Record<string, LucideIcon> = {
  cpu: Cpu,
  memory: MemoryStick,
  rss: Layers,
  loop: Timer,
  pool: Database,
};

const RESOURCE_BANDS: Record<"cpu" | "ram" | "quota", GaugeBands> = {
  cpu: { warn: 70, danger: 90 },
  ram: { warn: 75, danger: 90 },
  quota: { warn: 70, danger: 90 },
};

function ResourceGaugeCard({
  title,
  subtitle,
  icon,
  value,
  bands,
  caption,
  trend,
}: {
  title: string;
  subtitle: string;
  icon: LucideIcon;
  value: number | null;
  bands: GaugeBands;
  caption: string;
  trend: readonly TrendPoint[];
}) {
  const reading = value === null ? "no measurement yet" : formatPct(value, 0);
  return (
    <Enter className="h-full">
      <ChartCard
        title={title}
        subtitle={subtitle}
        icon={icon}
        className="h-full"
        bodyClassName="w-full"
        table={toChartTable(
          [{ metric: title, value: reading, warn: `${bands.warn}%`, danger: `${bands.danger}%` }],
          [
            { key: "metric", label: "Metric" },
            { key: "value", label: "Used", numeric: true },
            { key: "warn", label: "Warning at", numeric: true },
            { key: "danger", label: "Critical at", numeric: true },
          ],
        )}
      >
        <div className="flex flex-col gap-2" data-testid="resource-gauge-card">
          <div title={`${title}: ${reading}. Warning at ${bands.warn}%, critical at ${bands.danger}%`}>
            <RadialGauge
              label={`${title} used`}
              value={value}
              bands={bands}
              higherIsBetter={false}
              formatValue={(v) => `${Math.round(v)}%`}
            />
          </div>
          {/* Below the arc (never on it); two lines reserved so cards stay equal height. */}
          <p
            className="line-clamp-2 min-h-[2rem] px-1 text-center text-xs leading-4 tabular-nums text-fg-muted"
            title={caption}
            data-testid="gauge-caption"
          >
            {caption}
          </p>
          <ResourceTrend points={trend} warn={bands.warn} danger={bands.danger} label={title} />
          <p className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-[11px] text-fg-muted">
            <span className="inline-flex items-center gap-1">
              <span aria-hidden="true" className="h-2 w-2 rounded-full bg-status-warn" />
              Warn {bands.warn}%
            </span>
            <span className="inline-flex items-center gap-1">
              <span aria-hidden="true" className="h-2 w-2 rounded-full bg-status-danger" />
              Critical {bands.danger}%
            </span>
            <span>15 min trend</span>
          </p>
        </div>
      </ChartCard>
    </Enter>
  );
}

const USAGE_TABS = [
  { id: "llm", label: "LLM Usage" },
  { id: "embedding", label: "Embedding Usage" },
] as const;
type UsageTabKey = (typeof USAGE_TABS)[number]["id"];
type EmbeddingUsage = Record<string, number | string | null | undefined>;

function AiUsagePanel() {
  const { token } = useAuth();
  const [usageTab, setUsageTab] = useState<UsageTabKey>("llm");
  const llm = useApiQuery<LlmUsage>(["admin/usage/llm"], () => api.adminGet<LlmUsage>(token ?? "", "/usage/llm"), {
    enabled: Boolean(token),
  });
  const embedding = useApiQuery<EmbeddingUsage>(
    ["admin/usage/embedding"],
    () => api.adminGet<EmbeddingUsage>(token ?? "", "/usage/embedding"),
    { enabled: Boolean(token) },
  );
  const l = llm.data;
  const e = embedding.data ?? {};
  const s = llmUsageSummary(l);
  const allTime = "all time · assistant turns";
  const latencyDays = Math.round((l?.latency_window_hours ?? asNum(e.latency_window_hours) ?? 720) / 24);
  const latencyCaption = `last ${latencyDays} days · latency records`;

  return (
    <section aria-labelledby="ai-usage" data-tour="governance-mon-ai-usage" className="space-y-4">
      <StudioSectionHeader
        icon={Cpu}
        title={<span id="ai-usage">AI Usage</span>}
        subtitle="Model usage from the usage records. Failed counts only LLM errors."
        actions={
          <StudioSegmented
            ariaLabel="AI usage"
            options={USAGE_TABS}
            value={usageTab}
            onChange={setUsageTab}
          />
        }
      />
      {usageTab === "llm" ? (
        <>
          <Stagger key="llm" className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="Requests" icon={Activity} value={s.requests} caption={allTime} href={tabHref("Traces")} />
            <MetricCard label="Completed" icon={BadgeCheck} value={s.succeeded} caption={`no LLM error · ${allTime}`} href={tabHref("Traces")} />
            <MetricCard label="Failed" icon={XCircle} value={s.failed} caption={`LLM errors · ${allTime}`} href={tracesHref({ outcome: "llm_failed" })} />
            <MetricCard label="Success rate" icon={Gauge} value={s.successRate} format={(v) => formatPct(v)} caption={`completed / requests · all time`} />
            <MetricCard label="Average latency" icon={Timer} value={asNum(l?.average_latency_ms)} format={formatMs} caption={`LLM stage · ${latencyCaption}`} />
            <MetricCard label="Input tokens" icon={Hash} value={asNum(l?.input_tokens)} caption={allTime} href={USAGE_COST_HREF} />
            <MetricCard label="Output tokens" icon={Hash} value={asNum(l?.output_tokens)} caption={allTime} href={USAGE_COST_HREF} />
            <MetricCard label="Total tokens" icon={Sigma} value={asNum(l?.total_tokens)} caption={allTime} href={USAGE_COST_HREF} />
          </Stagger>
          {s.blocked !== null ? (
            <p className="text-xs text-fg-subtle" data-testid="usage-not-failed">
              Not counted as failed: {formatCount(s.blocked)} blocked by guardrails, {formatCount(s.conversational)} conversational,{" "}
              {formatCount(s.ungrounded)} ungrounded (all time).
            </p>
          ) : null}
        </>
      ) : (
        <Stagger key="embedding" className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <MetricCard label="Dimensions" icon={Layers} value={asNum(e.dimensions)} caption="embedding model setting" />
          <MetricCard label="Documents" icon={FileText} value={asNum(e.documents)} caption="right now · knowledge base" />
          <MetricCard label="Chunks" icon={Database} value={asNum(e.chunks)} caption="right now · knowledge base" />
          <MetricCard label="Requests" icon={Activity} value={asNum(e.requests)} caption="all time · chunks + queries" />
          <MetricCard label="Input tokens" icon={Coins} value={asNum(e.input_tokens)} caption="all time · estimated" href={USAGE_COST_HREF} />
          <MetricCard label="Average latency" icon={Timer} value={asNum(e.average_latency_ms)} format={formatMs} caption={`embedding stage · ${latencyCaption}`} />
        </Stagger>
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
  const router = useRouter();
  const carry = useCarryHref();
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
  const outcomes = useMemo(() => outcomeRows(m?.outcome_mix), [m]);
  const flags = useMemo(() => flagRows(m?.guardrail_flag_breakdown), [m]);
  const openOutcome = useCallback(
    (d: BarDatum) => {
      if (isTraceOutcome(d.key)) router.push(carry(tracesHref({ outcome: d.key })));
    },
    [carry, router],
  );
  const openFlags = useCallback(() => router.push(carry(tracesHref({ status: "blocked" }))), [carry, router]);

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
  const tpmTrend = quotaTrend(m?.azure_quota_series, "tpm");
  const rpmTrend = quotaTrend(m?.azure_quota_series, "rpm");
  const throttled = (quota?.chat?.azure_429_count_60s ?? 0) + (quota?.embedding?.azure_429_count_60s ?? 0);
  const windowH = m?.window_hours ?? 24;
  const windowLabel = `last ${windowH} h`;
  const noDataStages = rows.filter((r) => r.p50 === null).map((r) => r.label);
  const outcomeTotal = outcomes.reduce((sum, d) => sum + d.value, 0);
  const flagTotal = flags.reduce((sum, d) => sum + d.value, 0);
  const qualityCaption = "since restart · online evaluator";
  const countValue = (key: string, fallback: number | undefined) => counts[key]?.value ?? fallback;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-fg-subtle">
          Rolling {windowH} h snapshot. Latency is measured in milliseconds; each count shows its window and source.
        </p>
        <LiveIndicator status={stream.status} updatedAt={query.updatedAt || undefined} />
      </div>

      {/* G8: a balanced 4 x 2 grid (no dead slot) at the same width as AI Usage. */}
      <Stagger className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4" data-tour="governance-mon-tiles">
        {[
          <StatTile key="api" label="API requests" href={logsHref()} icon={Activity} value={formatCount(countValue("api_requests", m?.api_requests))} exact={exactTitle(countValue("api_requests", m?.api_requests))} caption={countCaption(counts.api_requests)} />,
          <StatTile key="queries" label="Queries" href={tabHref("Traces")} icon={Search} value={formatCount(countValue("queries_24h", m?.queries_24h))} exact={exactTitle(countValue("queries_24h", m?.queries_24h))} caption={countCaption(counts.queries_24h)} />,
          <StatTile key="visitors" label={ACTIVE_VISITORS_LABEL} info={<ActiveVisitorsInfo />} href="/admin/usage?tab=users" icon={Users} value={formatCount(countValue("active_users", m?.active_users))} caption={countCaption(counts.active_users)} />,
          <StatTile key="traces" label="Traces" href={tabHref("Traces")} icon={FileSearch} value={formatCount(countValue("traces_total", m?.traces_total))} exact={exactTitle(countValue("traces_total", m?.traces_total))} caption={countCaption(counts.traces_total)} />,
          <StatTile
            key="errors"
            label="Error rate"
            href={tracesHref({ status: "failed" })}
            icon={AlertTriangle}
            value={formatPct(m?.error_rate, 2)}
            caption={`${formatCount(counts.errors?.value)} errors · ${countCaption(counts.errors)}`}
          />,
          <StatTile key="avg" label="Average latency" href={tabHref("Traces")} icon={Timer} value={formatMs(m?.total_latency_ms ?? null)} caption={`end to end · ${windowH}h · latency records`} />,
          <StatTile
            key="p95"
            label="p95 latency"
            href={tracesHref({ latency_bucket: "slow" })}
            icon={Gauge}
            value={formatMs(total?.p95_ms ?? m?.p95_latency_ms ?? null)}
            caption={`${formatCount(total?.sample_count ?? 0)} samples · ${windowH}h · latency records`}
          />,
          <StatTile key="active" label="Active queries" href={tabHref("Traces")} icon={Loader} value={formatCount(countValue("active_queries", m?.active_queries))} caption={countCaption(counts.active_queries)} />,
        ].map((tile) => (
          <Enter key={tile.key} className="h-full">
            {tile}
          </Enter>
        ))}
      </Stagger>

      <Stagger className="grid grid-cols-1 gap-6 xl:grid-cols-2" data-tour="governance-mon-latency">
        <Enter>
          <ChartCard
            title="Pipeline latency"
            subtitle="Average per stage"
            window={windowLabel}
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
        </Enter>

        <Enter>
          <ChartCard
            title="Stage percentiles"
            subtitle="p50 to p95 band with dashed p99, per stage"
            window={windowLabel}
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
                <PercentileBand
                  data={stageBand}
                  label="Stage latency percentiles"
                  formatValue={latencyTick}
                  xAxisLabel="Pipeline stage"
                  yAxisLabel="Latency"
                />
              </div>
              {noDataStages.length > 0 ? (
                <p className="mt-1 text-[11px] text-fg-subtle">No data: {noDataStages.join(", ")}</p>
              ) : null}
            </div>
          </ChartCard>
        </Enter>

        <Enter>
          <ChartCard
            title="Latency over time"
            subtitle="End-to-end p50 to p95, hourly"
            window={windowLabel}
            loading={loading}
            empty={!loading && latencyBand.length === 0}
            table={toChartTable(latencyBand, [
              { key: "label", label: "Hour" },
              { key: "p50", label: "p50", numeric: true, format: nullableMs },
              { key: "p95", label: "p95", numeric: true, format: nullableMs },
            ])}
          >
            <PercentileBand
              data={latencyBand}
              label="Hourly latency percentiles"
              formatValue={latencyTick}
              xAxisLabel="Hour (local time)"
              yAxisLabel="End-to-end latency"
            />
          </ChartCard>
        </Enter>

        <Enter>
          <ChartCard
            title="Requests and errors"
            subtitle="Assistant turns plus server errors, hourly"
            window={windowLabel}
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
              xAxisLabel="Hour (local time)"
              yAxisLabel="Requests per hour"
              series={[
                { key: "ok", label: "Succeeded" },
                { key: "errors", label: "Errors", color: ct.status.danger },
              ]}
            />
          </ChartCard>
        </Enter>
      </Stagger>

      <section aria-labelledby="gov-resources" data-tour="governance-mon-resources" className="space-y-4">
        <StudioSectionHeader
          icon={Gauge}
          title={<span id="gov-resources">Resources and Azure quota</span>}
          subtitle="All API workers (live) · Azure quota over a rolling 60 s window"
          actions={
            throttled || usesAzureLimits(quota) ? (
              <span className="flex flex-wrap items-center gap-2">
                {usesAzureLimits(quota) ? <StudioPill tone="info">Limits from Azure</StudioPill> : null}
                {throttled ? <StudioPill tone="danger">{`${formatCount(throttled)} throttled (429)`}</StudioPill> : null}
              </span>
            ) : undefined
          }
        />
        <Stagger className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {gauges.map((g) => (
            <ResourceGaugeCard
              key={g.key}
              title={g.label}
              subtitle={sampleEvery ? `All API workers · every ${sampleEvery} s` : "All API workers · live"}
              icon={GAUGE_ICONS[g.key] ?? Gauge}
              value={g.value}
              bands={g.bands}
              caption={g.caption}
              trend={g.trend}
            />
          ))}
          <ResourceGaugeCard
            title="Azure TPM"
            subtitle="Tokens per minute · 60 s"
            icon={Zap}
            value={quotaUtilisation(quota, "tpm_headroom_pct")}
            bands={RESOURCE_BANDS.quota}
            caption={quotaLimitCaption(quota, "tpm")}
            trend={tpmTrend}
          />
          <ResourceGaugeCard
            title="Azure RPM"
            subtitle="Requests per minute · 60 s"
            icon={Activity}
            value={quotaUtilisation(quota, "rpm_headroom_pct")}
            bands={RESOURCE_BANDS.quota}
            caption={quotaLimitCaption(quota, "rpm")}
            trend={rpmTrend}
          />
        </Stagger>
      </section>

      {/* Azure (free Foundry data): connection, capacity / health, then Azure vs AskDIQ. */}
      <AzureConnectionCard />
      <AzureMonitoringSection />
      <AzureReconciliationPanel />

      <Stagger className="grid grid-cols-1 gap-6 xl:grid-cols-2" data-tour="governance-mon-guardrails">
        <Enter className="h-full">
          <ChartCard
            title="Guardrail flags"
            subtitle="Flags raised on assistant turns"
            window={windowLabel}
            icon={ShieldAlert}
            loading={loading}
            className="h-full"
            bodyClassName="h-64 w-full"
            table={toChartTable(
              flags.map((d) => ({ flag: d.label, count: d.value, share: shareOf(d.value, flagTotal) })),
              [
                { key: "flag", label: "Flag" },
                { key: "count", label: "Turns", numeric: true },
                { key: "share", label: "Share", numeric: true },
              ],
            )}
          >
            {flags.length === 0 ? (
              <EmptyState
                compact
                icon={ShieldCheck}
                title="No guardrail flags"
                description={`No assistant turn was flagged in the ${windowLabel}.`}
              />
            ) : (
              <BarList
                data={flags}
                label="Guardrail flags"
                valueLabel="Turns"
                xAxisLabel="Assistant turns"
                yAxisLabel="Flag"
                formatValue={formatCount}
                colorFor={(_d, _i, theme) => theme.status.warn}
                onSelect={openFlags}
              />
            )}
          </ChartCard>
        </Enter>
        <Enter className="h-full">
          <ChartCard
            title="Outcome mix"
            subtitle="Assistant turns by outcome; only LLM failed is an error"
            window={windowLabel}
            icon={PieChart}
            loading={loading}
            className="h-full"
            bodyClassName="h-64 w-full"
            table={toChartTable(
              outcomes.map((d) => ({ outcome: d.label, count: d.value, share: shareOf(d.value, outcomeTotal) })),
              [
                { key: "outcome", label: "Outcome" },
                { key: "count", label: "Turns", numeric: true },
                { key: "share", label: "Share", numeric: true },
              ],
            )}
          >
            {outcomeTotal === 0 ? (
              <EmptyState
                compact
                icon={PieChart}
                title="No assistant turns"
                description={`Nothing was answered in the ${windowLabel}.`}
              />
            ) : (
              <BarList
                data={outcomes}
                label="Outcome mix"
                valueLabel="Turns"
                xAxisLabel="Assistant turns"
                yAxisLabel="Outcome"
                formatValue={formatCount}
                colorFor={outcomeColor}
                dimZero
                onSelect={openOutcome}
              />
            )}
          </ChartCard>
        </Enter>
      </Stagger>

      <section aria-labelledby="gov-quality" data-tour="governance-mon-online-quality" className="space-y-4">
        <StudioSectionHeader
          icon={Scale}
          title={<span id="gov-quality">Online quality</span>}
          subtitle="Sampled online evaluation of live answers, since process start."
        />
        <Stagger className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <MetricCard label="Sampled" icon={Search} value={asNum(quality.sampled_count)} caption={qualityCaption} href={tabHref("Traces")} />
          <MetricCard
            label="Violation rate"
            icon={TriangleAlert}
            value={typeof quality.violation_rate === "number" ? quality.violation_rate * 100 : null}
            format={(v) => formatPct(v)}
            caption={qualityCaption}
            href={tracesHref({ status: "blocked" })}
          />
          <MetricCard
            label="Mean faithfulness"
            icon={BadgeCheck}
            value={asNum(quality.mean_faithfulness)}
            format={(v) => `${v.toFixed(2)} / 5`}
            caption={`1 to 5 · ${qualityCaption}`}
          />
          <MetricCard label="Unfaithful" icon={XCircle} value={asNum(quality.unfaithful_count)} caption={qualityCaption} href={tracesHref({ status: "ungrounded" })} />
          <MetricCard label="PII leaks" icon={ShieldAlert} value={asNum(quality.pii_leak_count)} caption={qualityCaption} />
          <MetricCard label="Secret leaks" icon={KeyRound} value={asNum(quality.secret_leak_count)} caption={qualityCaption} />
        </Stagger>
      </section>

      <AiUsagePanel />
    </div>
  );
}
