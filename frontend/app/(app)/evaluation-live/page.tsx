"use client";

import { useEffect, useMemo, useState } from "react";
import {
  evaluationCaseKey,
  evaluationCasesKey,
  evaluationSummaryKey,
  fetchEvaluationCase,
  fetchEvaluationCases,
  fetchEvaluationSummary,
} from "@/lib/evaluation-live";
import { prefetch, runWhenIdle, useApiQuery } from "@/lib/query-cache";
import dynamic from "next/dynamic";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import {
  RECHARTS_AXIS_TITLE_SPACE,
  chartTooltipStyle,
  rechartsAxisLabel,
  useChartTheme,
} from "@/lib/use-chart-theme";
import { useAudience } from "@/lib/audience-context";
import { formatLatencyMs } from "@/lib/format";
import { useDeepLink } from "@/lib/use-deep-link";
import { useRangeParams } from "@/components/admin/entity/shared";
import {
  CheckCircle2,
  Target,
  ShieldCheck,
  ClipboardCheck,
  Sparkles,
  GitCompare,
  ShieldAlert,
  Zap,
  Layers,
  FileText,
  AlertTriangle,
} from "lucide-react";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { HeaderIconButton } from "@/components/ui/HeaderIconButton";
import { TabGroup, EVALUATION_LIVE_TABS } from "@/components/admin/TabGroup";
import { EvaluationRecordDrawer } from "@/components/admin/EvaluationRecordDrawer";
import { EvaluationCompareMatrix } from "@/components/admin/EvaluationCompareMatrix";
import { ChartCard, toChartTable } from "@/components/charts/ChartCard";
import { StatCard } from "@/components/admin/insights/StatCard";
import { JudgeDisabledState, hasJudgedSamples } from "@/components/admin/evaluation/JudgeDisabledState";
import { SkeletonCard } from "@/components/ui/Skeleton";
import {
  DEFAULT_EXPLORER_STATE,
  EvaluationExplorer,
  explorerQuery,
  type ExplorerState,
} from "@/components/admin/evaluation/EvaluationExplorer";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

// The radar (polar Recharts) and the trend card sit below the fold: split out.
const QualityRadarCard = dynamic(
  () => import("@/components/admin/evaluation/QualityCharts").then((mod) => mod.QualityRadarCard),
  { ssr: false, loading: () => <SkeletonCard label="Loading chart" className="h-80" /> },
);
const PassRateTrendCard = dynamic(
  () => import("@/components/admin/evaluation/QualityCharts").then((mod) => mod.PassRateTrendCard),
  { ssr: false, loading: () => <SkeletonCard label="Loading chart" className="h-80" /> },
);


const pct = (v: unknown) =>
  typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—";
/** Percent, or "Not measured" when the server sends null (no dash-only cards). */
const pctOrUnmeasured = (v: unknown) => (typeof v === "number" ? pct(v) : "Not measured");
const num = (v: unknown, d = 2) =>
  typeof v === "number" ? v.toFixed(d) : "—";
const ms = formatLatencyMs;
const label = (k: string) => k.replace(/[_@]/g, " ").replace(/^./, (c) => c.toUpperCase());

function statusColor(value: string): string {
  if (!value.endsWith("%")) return "text-slate-900 dark:text-white";
  const n = parseFloat(value);
  if (Number.isNaN(n)) return "text-slate-900 dark:text-white";
  if (n >= 80) return "text-emerald-600 dark:text-emerald-400";
  if (n >= 50) return "text-amber-600 dark:text-amber-400";
  return "text-rose-600 dark:text-rose-400";
}

function statusBar(value: string): string {
  if (!value.endsWith("%")) return "bg-chart-track";
  const n = parseFloat(value);
  if (Number.isNaN(n)) return "bg-chart-track";
  if (n >= 80) return "bg-emerald-500";
  if (n >= 50) return "bg-amber-500";
  return "bg-rose-500";
}

function Stat({ label, value }: { label: string; value: string }) {
  const isPct = value.endsWith("%");
  const pctNum = isPct ? Math.min(Math.max(parseFloat(value) || 0, 0), 100) : null;
  return (
    <div className="relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-surface-card/90 backdrop-blur-md px-4 py-3.5 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60 hover:shadow-md">
      <div className="pointer-events-none absolute -right-6 -top-6 h-16 w-16 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
      <p className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-fg-muted">{label}</p>
      {value === "Not measured" || value === "—" ? (
        <p className="mt-2 text-sm font-semibold text-fg-subtle">Not measured</p>
      ) : (
        <p className={`mt-1 whitespace-nowrap text-xl font-black font-mono tracking-tight tabular-nums ${isPct ? statusColor(value) : "text-slate-900 dark:text-white"}`}>
          {value}
        </p>
      )}
      {pctNum !== null && (
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-chart-track">
          <div
            className={`h-full rounded-full transition-all duration-500 ${statusBar(value)}`}
            style={{ width: `${pctNum}%` }}
          />
        </div>
      )}
    </div>
  );
}

function HeroStat({
  label,
  value,
  icon: Icon,
  accent,
}: {
  label: string;
  value: string;
  icon: React.ElementType;
  accent?: string;
}) {
  // Shared KPI card (same as the rest of the console); no dash-only values.
  const measured = value !== "—" && value !== "Not measured";
  return (
    <StatCard
      title={label}
      value={measured ? value : <span className="text-base font-medium text-fg-subtle">Not measured</span>}
      subtitle={measured ? undefined : "No scored evaluation in this range"}
      icon={Icon as React.ComponentType<{ className?: string }>}
      className={accent ?? ""}
    />
  );
}

export default function EvaluationLivePage() {
  const { token } = useAuth();
  const ct = useChartTheme();
  const tooltipStyle = chartTooltipStyle(ct);
  // Pass / fail / partial outcome colours.
  const pieColors = [ct.status.ok, ct.status.danger, ct.status.warn];
  const { audience } = useAudience();
  // Global date range (header picker): live records and the pass-rate trend.
  const { from: rangeFrom, to: rangeTo } = useRangeParams();
  const range = useMemo(() => ({ from: rangeFrom, to: rangeTo }), [rangeFrom, rangeTo]);
  // The report without its case lists (a few KB): cached per audience and
  // range (persisted query cache, sidebar prefetch, live updates); the
  // previous report stays on screen while another loads.
  const report = useApiQuery<Record<string, any>>(
    token ? evaluationSummaryKey(audience, range) : null,
    (signal) => fetchEvaluationSummary(token ?? "", audience, range, signal),
    { keepPreviousData: true },
  );
  const r: any = report.data ?? null;
  const loading = !token || report.isLoading;
  const error =
    report.error && !r
      ? report.error instanceof ApiError
        ? report.error.message
        : "Couldn't load live evaluation telemetry."
      : null;
  // Tab and the selected run (pass-rate trend marker) live in the URL.
  const { get, set } = useDeepLink();
  const rawTab = get("tab");
  const activeTab = EVALUATION_LIVE_TABS.some((t) => t.id === rawTab) ? (rawTab as string) : "overview";
  const setActiveTab = (tab: string) => set({ tab: tab === "overview" ? null : tab });
  const selectedRun = get("run");
  const [compareMode, setCompareMode] = useState(false);
  const [explorer, setExplorer] = useState<ExplorerState>(DEFAULT_EXPLORER_STATE);
  const patchExplorer = (patch: Partial<ExplorerState>) => setExplorer((prev) => ({ ...prev, ...patch }));

  // Selected query for inspector drawer
  const [selectedRecord, setSelectedRecord] = useState<any>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);


  // Scored cases passed / failed (known issues excluded), from the server.
  const passBreakdown = useMemo(
    () =>
      [
        { name: "Passed", value: Number(r?.outcomes?.passed ?? 0) },
        { name: "Failed", value: Number(r?.outcomes?.failed ?? 0) },
      ].filter((d) => d.value > 0),
    [r?.outcomes?.passed, r?.outcomes?.failed],
  );

  // The explorer's first page is warmed while idle, so its tab opens filled.
  useEffect(() => {
    if (!token) return undefined;
    const first = explorerQuery(DEFAULT_EXPLORER_STATE);
    return runWhenIdle(() => {
      prefetch(evaluationCasesKey(audience, range, first), (signal) =>
        fetchEvaluationCases(token, audience, range, first, signal),
      ).catch(() => undefined);
    });
  }, [token, audience, range]);

  const caseTypeBars = useMemo(
    () =>
      Object.entries(r?.by_case_type ?? {}).map(([k, v]: [string, any]) => ({
        type: label(k),
        passRate: Math.round((v.pass_rate ?? 0) * 100),
      })),
    [r]
  );

  const latencyBars = useMemo(() => {
    const stageMap: Record<string, any> = r?.operational?.latency_ms ?? {};
    // Only measured stages: the API omits stages it has no timings for.
    return Object.entries(stageMap)
      .filter(([, v]) => v && typeof v === "object")
      .map(([k, v]: [string, any]) => ({
      stage: label(k),
      p50: ((v.p50 ?? 0) / 1000).toFixed(2),
      p95: ((v.p95 ?? 0) / 1000).toFixed(2),
      p99: ((v.p99 ?? 0) / 1000).toFixed(2),
    }));
  }, [r?.operational?.latency_ms]);

  const handleRowClick = (record: any) => {
    setSelectedRecord(record);
    setDrawerOpen(true);
  };

  return (
    <div className="flex flex-col min-h-screen bg-surface-page text-ink dark:text-slate-100">
      <div data-tour="evaluation-header">
      <DashboardHeader
        title="Evaluation"
        subtitle="Continuous production evaluation, live query audit, and benchmark comparative scorecard"
        icon={Sparkles}
        showDateRange
      >
        <HeaderIconButton
          icon={GitCompare}
          label="Compare with benchmark"
          tooltip={compareMode ? "Exit comparison" : "Compare with benchmark"}
          pressed={compareMode}
          indicator={compareMode}
          data-tour="evaluation-compare"
          onClick={() => setCompareMode(!compareMode)}
        />
      </DashboardHeader>
      </div>

      <div className="flex-1 p-6 sm:p-8 space-y-6 max-w-[1600px] mx-auto w-full">
        {error && (
          <div className="p-4 rounded-xl bg-rose-50 border border-rose-200 text-sm text-rose-700 flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {selectedRun && (
          <div
            role="status"
            className="flex flex-wrap items-center gap-2 rounded-xl border border-status-info/40 bg-status-info-bg p-3 text-xs text-status-info-fg"
          >
            <span>
              Run <span className="font-mono font-semibold">{selectedRun}</span> selected from the pass-rate trend. Per-run case
              lists are not reported by the server, so the figures below are the latest evaluation.
            </span>
            <button
              type="button"
              onClick={() => set({ run: null })}
              className="ml-auto rounded-md border border-status-info/40 px-2 py-0.5 font-semibold hover:bg-surface-card focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              Clear
            </button>
          </div>
        )}

        {/* Live Comparison View Banner if enabled */}
        {compareMode && r?.comparison && (
          <EvaluationCompareMatrix comparisonData={r.comparison} />
        )}

        {/* Standardized Tab Navigation */}
        <div data-tour="evaluation-tabs" className="flex items-center justify-between overflow-x-auto pb-1">
          <TabGroup
            tabs={EVALUATION_LIVE_TABS}
            activeTab={activeTab}
            onChange={(t) => setActiveTab(t)}
            ariaLabel="Live evaluation domains"
          />
        </div>

        {/* Domain Tab 1: Quality & Retrieval */}
        {activeTab === "overview" && (
          <div className="space-y-6 animate-in fade-in duration-200">
            {/* Unified 6-stat hero grid */}
            <div data-tour="evaluation-overview-kpis" className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4">
              <HeroStat label="Pass rate" value={pct(r?.pass_rate)} icon={CheckCircle2} />
              <HeroStat label="Cases total" value={String(r?.n_cases ?? "—")} icon={Target} />
              <HeroStat label="Retrieval recall" value={pct(r?.retrieval_recall)} icon={ShieldCheck} />
              <HeroStat label="MRR (Mean Reciprocal)" value={num(r?.retrieval?.mrr, 3)} icon={Layers} />
              <HeroStat label="NDCG@k" value={num(r?.retrieval?.["ndcg@k"], 3)} icon={Sparkles} />
              <HeroStat label="Groundedness" value={pct(r?.groundedness_accuracy)} icon={CheckCircle2} />
            </div>

            {/* Radar and Outcomes */}
            <div data-tour="evaluation-overview-radar" className="grid grid-cols-1 gap-6 xl:grid-cols-3">
              <div className="xl:col-span-2">
                <QualityRadarCard report={r} loading={loading} />
              </div>

              <ChartCard
                title="Case outcomes"
                subtitle="Pass / fail split across live evaluations"
                variant="aurora"
                titleStyle="eyebrow"
                empty={passBreakdown.length === 0}
                emptyMessage="No scored cases yet"
                table={toChartTable(passBreakdown, [
                { key: "name", label: "Outcome" },
                { key: "value", label: "Cases", numeric: true },
              ])}
              >
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={passBreakdown}
                      dataKey="value"
                      nameKey="name"
                      innerRadius={55}
                      outerRadius={85}
                      paddingAngle={3}
                    >
                      {passBreakdown.map((_, idx) => (
                        <Cell key={idx} fill={pieColors[idx % pieColors.length]} />
                      ))}
                    </Pie>
                    <Legend verticalAlign="top" height={24} wrapperStyle={{ fontSize: 11 }} />
                    <Tooltip
                      contentStyle={tooltipStyle}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </ChartCard>
            </div>

            <div data-tour="evaluation-overview-trend">
              <PassRateTrendCard report={r} loading={loading} />
            </div>

            {/* Category breakdown & Stage Latency */}
            <div data-tour="evaluation-overview-breakdown" className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <ChartCard
                title="Pass rate by query type"
                empty={caseTypeBars.length === 0}
                emptyMessage="No scored cases in this range yet"
                subtitle="Distribution of passing answers per intent"
                variant="aurora"
                titleStyle="eyebrow"
                table={toChartTable(caseTypeBars, [
                { key: "type", label: "Query type" },
                { key: "passRate", label: "Pass rate", numeric: true, format: (v) => `${String(v)}%` },
              ])}
              >
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={caseTypeBars} margin={{ top: 5, right: 5, bottom: RECHARTS_AXIS_TITLE_SPACE, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={ct.grid} vertical={false} />
                    <XAxis
                      dataKey="type"
                      tick={{ fontSize: 11, fill: ct.axis }}
                      axisLine={false}
                      tickLine={false}
                      label={rechartsAxisLabel(ct, "Query type", "x")}
                    />
                    <YAxis
                      tick={{ fontSize: 11, fill: ct.axis }}
                      axisLine={false}
                      tickLine={false}
                      width={30 + RECHARTS_AXIS_TITLE_SPACE}
                      domain={[0, 100]}
                      label={rechartsAxisLabel(ct, "Pass rate (%)", "y")}
                    />
                    <Tooltip
                      contentStyle={tooltipStyle}
                      formatter={(v) => [`${v}%`, "Pass rate"]}
                    />
                    <Bar dataKey="passRate" radius={[6, 6, 0, 0]}>
                      {caseTypeBars.map((_, idx) => (
                        <Cell key={idx} fill={ct.categorical[idx % ct.categorical.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>

              <ChartCard
                title="Latency by pipeline stage"
                empty={latencyBars.every((b) => !Number(b.p50) && !Number(b.p95) && !Number(b.p99))}
                emptyMessage="No stage timings in this range yet"
                subtitle="p50 / p95 / p99 across stages (seconds)"
                variant="aurora"
                titleStyle="eyebrow"
                table={toChartTable(latencyBars, [
                { key: "stage", label: "Stage" },
                { key: "p50", label: "p50", numeric: true, format: (v) => `${Number(v).toFixed(2)} s` },
                { key: "p95", label: "p95", numeric: true, format: (v) => `${Number(v).toFixed(2)} s` },
                { key: "p99", label: "p99", numeric: true, format: (v) => `${Number(v).toFixed(2)} s` },
              ])}
              >
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={latencyBars} margin={{ top: 5, right: 5, bottom: RECHARTS_AXIS_TITLE_SPACE, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={ct.grid} vertical={false} />
                    <XAxis
                      dataKey="stage"
                      tick={{ fontSize: 11, fill: ct.axis }}
                      axisLine={false}
                      tickLine={false}
                      label={rechartsAxisLabel(ct, "Pipeline stage", "x")}
                    />
                    <YAxis
                      tick={{ fontSize: 11, fill: ct.axis }}
                      axisLine={false}
                      tickLine={false}
                      width={40 + RECHARTS_AXIS_TITLE_SPACE}
                      label={rechartsAxisLabel(ct, "Latency (seconds)", "y")}
                    />
                    <Tooltip
                      contentStyle={tooltipStyle}
                      formatter={(v, name) => [`${v} s`, name]}
                    />
                    <Legend verticalAlign="top" height={24} wrapperStyle={{ fontSize: 11 }} />
                    <Bar dataKey="p50" name="p50" fill={ct.categorical[0]} radius={[4, 4, 0, 0]} />
                    <Bar dataKey="p95" name="p95" fill={ct.status.warn} radius={[4, 4, 0, 0]} />
                    <Bar dataKey="p99" name="p99" fill={ct.status.danger} radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>
            </div>

            {/* Retrieval Detailed Performance Grid */}
            <div data-tour="evaluation-overview-retrieval" className="relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-surface-card/90 backdrop-blur-md p-6 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60">
              <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
              <div className="border-b border-teal-100 dark:border-teal-500/20 pb-3 mb-4">
                <h3 className="text-xs font-bold uppercase tracking-wider text-slate-600 dark:text-teal-300/90">Retrieval Ranking Metrics</h3>
                <p className="mt-1 text-[11px] text-slate-500 dark:text-fg-muted">Context coverage, noise ratio, and multi-tier retrieval recall</p>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                <Stat label="Recall @ 1" value={pct(r?.retrieval?.["recall@1"])} />
                <Stat label="Recall @ 3" value={pct(r?.retrieval?.["recall@3"])} />
                <Stat label="Recall @ k" value={pct(r?.retrieval?.["recall@k"])} />
                <Stat label="Precision @ k" value={pct(r?.retrieval?.["precision@k"])} />
                <Stat label="Context Coverage" value={pct(r?.retrieval?.context_coverage)} />
                <Stat label="Noise Ratio" value={pct(r?.retrieval?.noise_ratio)} />
              </div>
            </div>
          </div>
        )}

        {/* Domain Tab 2: Semantic Quality & Citations */}
        {activeTab === "judge" && (
          <div className="space-y-6 animate-in fade-in duration-200">
            {/* LLM Judge Hero Metrics (empty state while the online judge has scored nothing) */}
            {!hasJudgedSamples(r) ? (
              <div data-tour="evaluation-judge-kpis">
                <JudgeDisabledState />
              </div>
            ) : (
            <div data-tour="evaluation-judge-kpis" className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <HeroStat
                label="Faithfulness (LLM Judge)"
                value={`${num(r?.judge?.faithfulness, 2)} / 5.0`}
                icon={Sparkles}
              />
              <HeroStat
                label="Answer Relevance"
                value={`${num(r?.judge?.relevance, 2)} / 5.0`}
                icon={Target}
              />
              <HeroStat
                label="Completeness"
                value={`${num(r?.judge?.completeness, 2)} / 5.0`}
                icon={CheckCircle2}
              />
            </div>
            )}

            {/* Citation & Attribution Matrix */}
            <div data-tour="evaluation-judge-citations" className="relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-surface-card/90 backdrop-blur-md p-6 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60">
              <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-teal-100 dark:border-teal-500/20 pb-3 mb-4">
                <div>
                  <h3 className="text-xs font-bold uppercase tracking-wider text-slate-600 dark:text-teal-300/90">Citation & Attribution Verification</h3>
                  <p className="mt-1 text-[11px] text-slate-500 dark:text-fg-muted">Claims grounded in cited passages vs total claims stated</p>
                </div>
                <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-teal-50 dark:bg-teal-950/60 text-teal-700 dark:text-teal-300 border border-teal-200/80 dark:border-teal-500/30">
                  Automated Citation Attributor Active
                </span>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 pt-1">
                <Stat label="Citation Precision" value={pctOrUnmeasured(r?.citation?.precision)} />
                <Stat label="Citation Coverage" value={pctOrUnmeasured(r?.citation?.coverage)} />
                <Stat label="Correct Document" value={r?.citation?.quality?.correct_document === true ? "100.0%" : "Not measured"} />
                <Stat label="Current Version" value={r?.citation?.quality?.current_version === true ? "100.0%" : "Not measured"} />
              </div>
            </div>

            {/* Semantic Quality Insights */}
            {hasJudgedSamples(r) && (
            <div data-tour="evaluation-judge-rationale" className="relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-teal-50/30 dark:bg-teal-950/20 backdrop-blur-md p-6 shadow-card dark:shadow-xl space-y-3">
              <h4 className="text-xs font-bold uppercase tracking-wider text-teal-900 dark:text-teal-300 flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-teal-600 dark:text-teal-400" />
                Live Evaluation Quality Rationale
              </h4>
              <p className="text-xs text-slate-700 dark:text-slate-300 leading-relaxed">
                The evaluator scores assistant responses against the retrieved documents. Mean faithfulness is{" "}
                <strong className="text-teal-700 dark:text-teal-300 font-bold font-mono">{num(r?.judge?.faithfulness, 2)} / 5.0</strong>{" "}
                and citation precision (word overlap with the sources) is{" "}
                <strong className="text-emerald-600 dark:text-emerald-400 font-bold font-mono">{pct(r?.citation?.precision)}</strong>.
                Without the LLM judge (ONLINE_EVAL_ENABLED) faithfulness is a heuristic, not a judged score.
              </p>
            </div>
            )}
          </div>
        )}

        {/* Domain Tab 3: Safety & Robustness */}
        {activeTab === "safety" && (
          <div className="space-y-6 animate-in fade-in duration-200">
            {/* Safety Scorecard */}
            <div data-tour="evaluation-safety-kpis" className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <HeroStat label="Safety Passed" value={pct(r?.safety?.passed)} icon={ShieldCheck} />
              <HeroStat label="PII Leakage Rate" value={pct(r?.safety?.pii_leakage_rate)} icon={ShieldAlert} />
              <HeroStat label="Secret Exposure Rate" value={pct(r?.safety?.secret_exposure_rate)} icon={ShieldAlert} />
              <HeroStat label="Injection Compliance" value={pct(r?.safety?.injection_compliance_rate)} icon={ShieldCheck} />
            </div>

            {/* Robustness by Perturbation */}
            <div data-tour="evaluation-safety-robustness" className="relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-surface-card/90 backdrop-blur-md p-6 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60">
              <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
              <div className="border-b border-teal-100 dark:border-teal-500/20 pb-3 mb-4">
                <h3 className="text-xs font-bold uppercase tracking-wider text-slate-600 dark:text-teal-300/90">Adversarial Robustness & Stability</h3>
                <p className="mt-1 text-[11px] text-slate-500 dark:text-fg-muted">Response stability against typos, paraphrasing, and adversarial prompt perturbations</p>
              </div>

              <div className="overflow-x-auto rounded-xl border border-teal-100 dark:border-teal-500/20">
                <table className="w-full text-left text-xs">
                  <thead className="bg-surface-studio text-fg-muted dark:text-brand-200 border-b border-line-brand/70 dark:border-brand-500/20 font-bold">
                    <tr>
                      <th className="px-3.5 py-2.5">Perturbation Type</th>
                      <th className="px-3.5 py-2.5 text-right">Cases Tested</th>
                      <th className="px-3.5 py-2.5 text-right">Retrieval Stability</th>
                      <th className="px-3.5 py-2.5 text-right">Answer Consistency</th>
                      <th className="px-3.5 py-2.5 text-right">Citation Consistency</th>
                      <th className="px-3.5 py-2.5 text-right">Pass Rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.keys(r?.robustness?.by_perturbation ?? {}).length === 0 ? (
                      <tr>
                        <td colSpan={6} className="px-3.5 py-4 text-center text-fg-muted" data-testid="robustness-not-measured">
                          Not measured: robustness comes from the benchmark suite, not live traffic.
                        </td>
                      </tr>
                    ) : null}
                    {Object.entries(r?.robustness?.by_perturbation ?? {}).map(([k, v]: any) => (
                      <tr key={k} className="border-b border-teal-100/50 dark:border-teal-500/10 even:bg-surface-muted/30 dark:even:bg-white/[0.015] hover:bg-brand-50/60 dark:hover:bg-brand-500/[0.07] transition-colors">
                        <td className="px-3.5 py-2.5 font-bold text-slate-900 dark:text-white">{label(k)}</td>
                        <td className="px-3.5 py-2.5 text-right font-mono tabular-nums text-slate-700 dark:text-slate-300">{v.n}</td>
                        <td className="px-3.5 py-2.5 text-right font-mono tabular-nums text-emerald-600 dark:text-emerald-400">{num(v.retrieval_stability, 3)}</td>
                        <td className="px-3.5 py-2.5 text-right font-mono tabular-nums text-indigo-600 dark:text-indigo-400">{num(v.answer_consistency, 3)}</td>
                        <td className="px-3.5 py-2.5 text-right font-mono tabular-nums text-cyan-600 dark:text-cyan-400">{num(v.citation_consistency, 3)}</td>
                        <td className="px-3.5 py-2.5 text-right font-bold font-mono tabular-nums text-emerald-700 dark:text-emerald-400">{pct(v.pass_rate_perturbed)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Abstention Analysis */}
            <div data-tour="evaluation-safety-abstention" className="relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-surface-card/90 backdrop-blur-md p-6 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60">
              <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
              <div className="border-b border-teal-100 dark:border-teal-500/20 pb-3 mb-4">
                <h3 className="text-xs font-bold uppercase tracking-wider text-slate-600 dark:text-teal-300/90">Abstention & Refusal Quality</h3>
                <p className="mt-1 text-[11px] text-slate-500 dark:text-fg-muted">Refusal precision on out-of-domain and unanswerable scenarios</p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <Stat label="Abstention Rate" value={pct(r?.abstention?.abstention_rate)} />
                <Stat label="False Abstention" value={pct(r?.abstention?.false_abstention_rate)} />
                <Stat label="Refusal Scenarios Tested" value={String(r?.abstention?.n ?? 0)} />
              </div>
            </div>
          </div>
        )}

        {/* Domain Tab 4: Query & Case Explorer */}
        {activeTab === "explorer" && (
          <div data-tour="evaluation-explorer">
          <EvaluationExplorer
            token={token}
            audience={audience}
            range={range}
            state={explorer}
            onStateChange={patchExplorer}
            onOpen={handleRowClick}
          />
          </div>
        )}
      </div>

      {/* Deep Query Inspector Drawer */}
      <EvaluationRecordDrawer
        isOpen={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        recordId={selectedRecord?.id ?? null}
        initialData={selectedRecord}
        token={token}
        detailKey={selectedRecord?.id ? evaluationCaseKey(audience, range, String(selectedRecord.id)) : null}
        fetchDetail={(signal) =>
          fetchEvaluationCase(token ?? "", audience, range, String(selectedRecord?.id ?? ""), signal)
        }
      />
    </div>
  );
}
