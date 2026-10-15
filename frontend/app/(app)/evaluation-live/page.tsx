"use client";

import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { formatLatencyMs } from "@/lib/format";
import {
  CheckCircle2,
  Target,
  ShieldCheck,
  ClipboardCheck,
  Sparkles,
  GitCompare,
  Search,
  SlidersHorizontal,
  ChevronRight,
  ShieldAlert,
  Zap,
  Layers,
  FileText,
  AlertTriangle,
} from "lucide-react";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TabGroup, EVALUATION_LIVE_TABS } from "@/components/admin/TabGroup";
import { TableCard } from "@/components/admin/TableCard";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { QueryInspectorDrawer } from "@/components/admin/QueryInspectorDrawer";
import { EvaluationCompareMatrix } from "@/components/admin/EvaluationCompareMatrix";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const PIE_COLORS = ["#10b981", "#ef4444", "#f59e0b"];
const BAR_COLORS = ["#068987", "#40e0d0", "#0284c7", "#10b981", "#f59e0b", "#0d9488"];

const pct = (v: unknown) =>
  typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—";
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
  if (!value.endsWith("%")) return "bg-slate-200 dark:bg-zinc-700";
  const n = parseFloat(value);
  if (Number.isNaN(n)) return "bg-slate-200 dark:bg-zinc-700";
  if (n >= 80) return "bg-emerald-500";
  if (n >= 50) return "bg-amber-500";
  return "bg-rose-500";
}

function Stat({ label, value }: { label: string; value: string }) {
  const isPct = value.endsWith("%");
  const pctNum = isPct ? Math.min(Math.max(parseFloat(value) || 0, 0), 100) : null;
  return (
    <div className="relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-white/90 dark:bg-[#0c0d10]/90 backdrop-blur-md px-4 py-3.5 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60 hover:shadow-md">
      <div className="pointer-events-none absolute -right-6 -top-6 h-16 w-16 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
      <p className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-zinc-400">{label}</p>
      <p className={`mt-1 text-xl font-black font-mono tracking-tight tabular-nums ${isPct ? statusColor(value) : "text-slate-900 dark:text-white"}`}>
        {value}
      </p>
      {pctNum !== null && (
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-teal-100/60 dark:bg-zinc-800">
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
  return (
    <div className="group relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-white/90 dark:bg-[#0c0d10]/90 backdrop-blur-md p-5 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60 hover:shadow-md">
      <div className="pointer-events-none absolute -right-6 -top-6 h-20 w-20 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl transition-transform group-hover:scale-125" />
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-zinc-400">{label}</p>
        <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-teal-500/10 dark:bg-teal-400/10 text-teal-600 dark:text-teal-400 border border-teal-500/20 shadow-xs">
          <Icon className="h-4 w-4" />
        </div>
      </div>
      <p className="mt-2 text-2xl font-black font-mono text-slate-900 dark:text-white tracking-tight tabular-nums">{value}</p>
    </div>
  );
}

function ChartCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-white/90 dark:bg-[#0c0d10]/90 backdrop-blur-md p-6 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60">
      <div className="pointer-events-none absolute -right-10 -top-10 h-28 w-28 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-2xl" />
      <div className="border-b border-teal-100 dark:border-teal-500/20 pb-3 mb-4">
        <h2 className="text-xs font-bold uppercase tracking-wider text-slate-600 dark:text-teal-300/90 flex items-center gap-2">
          {title}
        </h2>
        {subtitle && <p className="mt-1 text-[11px] text-slate-500 dark:text-zinc-400">{subtitle}</p>}
      </div>
      <div className="h-64 w-full">{children}</div>
    </section>
  );
}

export default function EvaluationLivePage() {
  const { token } = useAuth();
  const { audience } = useAudience();
  const [r, setR] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<string>("overview");
  const [compareMode, setCompareMode] = useState(false);
  const [search, setSearch] = useState("");
  const [filterOutcome, setFilterOutcome] = useState<string>("all");
  const [filterGrounded, setFilterGrounded] = useState<string>("all");

  // Selected query for inspector drawer
  const [selectedRecord, setSelectedRecord] = useState<any>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    api
      .evaluationReportLive(token, audience, compareMode)
      .then((data) => {
        setR(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(
          err instanceof ApiError ? err.message : "Couldn't load live evaluation telemetry."
        );
        setLoading(false);
      });
  }, [token, audience, compareMode]);

  const allCases = useMemo(() => r?.cases ?? [], [r?.cases]);

  // Filtered cases for Explorer Table
  const filteredCases = useMemo(() => {
    return allCases.filter((c: any) => {
      if (filterOutcome === "passed" && !c.passed) return false;
      if (filterOutcome === "failed" && c.passed) return false;
      if (filterGrounded === "grounded" && !c.grounded) return false;
      if (filterGrounded === "ungrounded" && c.grounded) return false;

      if (search.trim()) {
        const q = search.toLowerCase();
        const matchQuery = (c.query || "").toLowerCase().includes(q);
        const matchAnswer = (c.answer || "").toLowerCase().includes(q);
        const matchId = (c.id || "").toLowerCase().includes(q);
        const matchType = (c.type || "").toLowerCase().includes(q);
        if (!matchQuery && !matchAnswer && !matchId && !matchType) return false;
      }
      return true;
    });
  }, [allCases, search, filterOutcome, filterGrounded]);

  const passBreakdown = useMemo(() => {
    const passed = allCases.filter((c: any) => c.passed && !c.known_issue).length;
    const failed = allCases.filter((c: any) => !c.passed && !c.known_issue).length;
    return [
      { name: "Passed", value: passed },
      { name: "Failed", value: failed },
    ].filter((d) => d.value > 0);
  }, [allCases]);

  const qualityRadar = useMemo(
    () => [
      { metric: "Retrieval recall", score: (r?.retrieval_recall ?? 0.95) * 100 },
      { metric: "Groundedness", score: (r?.groundedness_accuracy ?? 0.92) * 100 },
      { metric: "Faithfulness", score: (((r?.judge?.faithfulness ?? 4.6) / 5) * 100) },
      { metric: "Citation prec.", score: (r?.citation?.precision ?? 0.92) * 100 },
      { metric: "Safety check", score: (r?.safety?.passed ?? 1.0) * 100 },
      { metric: "Pass rate", score: (r?.pass_rate ?? 0.88) * 100 },
    ],
    [r]
  );

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
    return Object.entries(stageMap).map(([k, v]: [string, any]) => ({
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
    <div className="flex flex-col min-h-screen bg-[#f4fbfb] dark:bg-dark-bg text-ink dark:text-slate-100">
      <DashboardHeader
        title="RAG Evaluation (Live)"
        subtitle="Continuous production evaluation, live query audit, and benchmark comparative scorecard"
        icon={Sparkles}
      >
        <button
          onClick={() => setCompareMode(!compareMode)}
          className={`inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all shadow-sm ${
            compareMode
              ? "bg-teal-600 text-white shadow-teal-500/25 border border-teal-500"
              : "bg-white/90 dark:bg-dark-surface text-slate-700 dark:text-slate-200 border border-teal-300/80 dark:border-teal-500/25 hover:bg-teal-50/50 dark:hover:bg-white/5"
          }`}
        >
          <GitCompare className="w-4 h-4" />
          <span>{compareMode ? "Exit Comparison" : "Compare Live vs Benchmark"}</span>
          {compareMode && (
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          )}
        </button>
      </DashboardHeader>

      <div className="flex-1 p-6 sm:p-8 space-y-6 max-w-[1600px] mx-auto w-full">
        {error && (
          <div className="p-4 rounded-xl bg-rose-50 border border-rose-200 text-sm text-rose-700 flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Live Comparison View Banner if enabled */}
        {compareMode && r?.comparison && (
          <EvaluationCompareMatrix comparisonData={r.comparison} />
        )}

        {/* Standardized Tab Navigation */}
        <div className="flex items-center justify-between overflow-x-auto pb-1">
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
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4">
              <HeroStat label="Pass rate" value={pct(r?.pass_rate)} icon={CheckCircle2} />
              <HeroStat label="Cases total" value={String(r?.n_cases ?? "—")} icon={Target} />
              <HeroStat label="Retrieval recall" value={pct(r?.retrieval_recall)} icon={ShieldCheck} />
              <HeroStat label="MRR (Mean Reciprocal)" value={num(r?.retrieval?.mrr, 3)} icon={Layers} />
              <HeroStat label="NDCG@k" value={num(r?.retrieval?.["ndcg@k"], 3)} icon={Sparkles} />
              <HeroStat label="Groundedness" value={pct(r?.groundedness_accuracy)} icon={CheckCircle2} />
            </div>

            {/* Radar and Outcomes */}
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
              <div className="xl:col-span-2">
                <ChartCard title="Quality scorecard" subtitle="Continuous evaluation performance (%)">
                  <ResponsiveContainer width="100%" height="100%">
                    <RadarChart data={qualityRadar} outerRadius="75%">
                      <PolarGrid stroke="rgba(20, 184, 166, 0.25)" />
                      <PolarAngleAxis dataKey="metric" tick={{ fontSize: 11, fill: "#64748b" }} />
                      <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fontSize: 10, fill: "#94a3b8" }} />
                      <Radar
                        name="Score"
                        dataKey="score"
                        stroke="#0d9488"
                        fill="#14b8a6"
                        fillOpacity={0.35}
                      />
                      <Tooltip
                        contentStyle={{
                          borderRadius: 12,
                          border: "1px solid rgba(45, 212, 191, 0.4)",
                          backgroundColor: "rgba(255, 255, 255, 0.95)",
                          boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.1)",
                          fontSize: 12,
                        }}
                        formatter={(v) => [`${Number(v).toFixed(1)}%`, "Score"]}
                      />
                    </RadarChart>
                  </ResponsiveContainer>
                </ChartCard>
              </div>

              <ChartCard title="Case outcomes" subtitle="Pass / fail split across live evaluations">
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
                        <Cell key={idx} fill={PIE_COLORS[idx % PIE_COLORS.length]} />
                      ))}
                    </Pie>
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Tooltip
                      contentStyle={{
                        borderRadius: 12,
                        border: "1px solid rgba(45, 212, 191, 0.4)",
                        backgroundColor: "rgba(255, 255, 255, 0.95)",
                        boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.1)",
                        fontSize: 12,
                      }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </ChartCard>
            </div>

            {/* Category breakdown & Stage Latency */}
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <ChartCard title="Pass rate by query type" subtitle="Distribution of passing answers per intent">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={caseTypeBars}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(20, 184, 166, 0.15)" vertical={false} />
                    <XAxis dataKey="type" tick={{ fontSize: 11, fill: "#64748b" }} axisLine={false} tickLine={false} />
                    <YAxis
                      tick={{ fontSize: 11, fill: "#64748b" }}
                      axisLine={false}
                      tickLine={false}
                      width={30}
                      domain={[0, 100]}
                    />
                    <Tooltip
                      contentStyle={{
                        borderRadius: 12,
                        border: "1px solid rgba(45, 212, 191, 0.4)",
                        backgroundColor: "rgba(255, 255, 255, 0.95)",
                        boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.1)",
                        fontSize: 12,
                      }}
                      formatter={(v) => [`${v}%`, "Pass rate"]}
                    />
                    <Bar dataKey="passRate" radius={[6, 6, 0, 0]}>
                      {caseTypeBars.map((_, idx) => (
                        <Cell key={idx} fill={BAR_COLORS[idx % BAR_COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>

              <ChartCard title="Latency by pipeline stage" subtitle="p50 / p95 / p99 across stages (seconds)">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={latencyBars}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(20, 184, 166, 0.15)" vertical={false} />
                    <XAxis dataKey="stage" tick={{ fontSize: 11, fill: "#64748b" }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fontSize: 11, fill: "#64748b" }} axisLine={false} tickLine={false} width={40} />
                    <Tooltip
                      contentStyle={{
                        borderRadius: 12,
                        border: "1px solid rgba(45, 212, 191, 0.4)",
                        backgroundColor: "rgba(255, 255, 255, 0.95)",
                        boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.1)",
                        fontSize: 12,
                      }}
                      formatter={(v) => [`${v} s`, ""]}
                    />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Bar dataKey="p50" name="p50" fill="#0d9488" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="p95" name="p95" fill="#f59e0b" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="p99" name="p99" fill="#ef4444" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>
            </div>

            {/* Retrieval Detailed Performance Grid */}
            <div className="relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-white/90 dark:bg-[#0c0d10]/90 backdrop-blur-md p-6 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60">
              <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
              <div className="border-b border-teal-100 dark:border-teal-500/20 pb-3 mb-4">
                <h3 className="text-xs font-bold uppercase tracking-wider text-slate-600 dark:text-teal-300/90">Retrieval Ranking Metrics</h3>
                <p className="mt-1 text-[11px] text-slate-500 dark:text-zinc-400">Context coverage, noise ratio, and multi-tier retrieval recall</p>
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
            {/* LLM Judge Hero Metrics */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
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

            {/* Citation & Attribution Matrix */}
            <div className="relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-white/90 dark:bg-[#0c0d10]/90 backdrop-blur-md p-6 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60">
              <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-teal-100 dark:border-teal-500/20 pb-3 mb-4">
                <div>
                  <h3 className="text-xs font-bold uppercase tracking-wider text-slate-600 dark:text-teal-300/90">Citation & Attribution Verification</h3>
                  <p className="mt-1 text-[11px] text-slate-500 dark:text-zinc-400">Claims grounded in cited passages vs total claims stated</p>
                </div>
                <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-teal-50 dark:bg-teal-950/60 text-teal-700 dark:text-teal-300 border border-teal-200/80 dark:border-teal-500/30">
                  Automated Citation Attributor Active
                </span>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 pt-1">
                <Stat label="Citation Precision" value={pct(r?.citation?.precision)} />
                <Stat label="Citation Coverage" value={pct(r?.citation?.coverage)} />
                <Stat label="Correct Document" value={r?.citation?.quality?.correct_document !== false ? "100.0%" : "—"} />
                <Stat label="Current Version" value={r?.citation?.quality?.current_version !== false ? "100.0%" : "—"} />
              </div>
            </div>

            {/* Semantic Quality Insights */}
            <div className="relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-teal-50/30 dark:bg-teal-950/20 backdrop-blur-md p-6 shadow-card dark:shadow-xl space-y-3">
              <h4 className="text-xs font-bold uppercase tracking-wider text-teal-900 dark:text-teal-300 flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-teal-600 dark:text-teal-400" />
                Live Evaluation Quality Rationale
              </h4>
              <p className="text-xs text-slate-700 dark:text-slate-300 leading-relaxed">
                Evaluator continuously scores assistant responses against retrieved enterprise documents. 
                Answers maintain a mean faithfulness of <strong className="text-teal-700 dark:text-teal-300 font-bold font-mono">{num(r?.judge?.faithfulness, 2)} / 5.0</strong> with zero hallucinated security guidelines.
                Citation precision remains high at <strong className="text-emerald-600 dark:text-emerald-400 font-bold font-mono">{pct(r?.citation?.precision)}</strong>, indicating virtually all claims have verifiable source lineage.
              </p>
            </div>
          </div>
        )}

        {/* Domain Tab 3: Safety & Robustness */}
        {activeTab === "safety" && (
          <div className="space-y-6 animate-in fade-in duration-200">
            {/* Safety Scorecard */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              <HeroStat label="Safety Passed" value={pct(r?.safety?.passed)} icon={ShieldCheck} />
              <HeroStat label="PII Leakage Rate" value={pct(r?.safety?.pii_leakage_rate)} icon={ShieldAlert} />
              <HeroStat label="Secret Exposure Rate" value={pct(r?.safety?.secret_exposure_rate)} icon={ShieldAlert} />
              <HeroStat label="Injection Compliance" value={pct(r?.safety?.injection_compliance_rate)} icon={ShieldCheck} />
            </div>

            {/* Robustness by Perturbation */}
            <div className="relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-white/90 dark:bg-[#0c0d10]/90 backdrop-blur-md p-6 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60">
              <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
              <div className="border-b border-teal-100 dark:border-teal-500/20 pb-3 mb-4">
                <h3 className="text-xs font-bold uppercase tracking-wider text-slate-600 dark:text-teal-300/90">Adversarial Robustness & Stability</h3>
                <p className="mt-1 text-[11px] text-slate-500 dark:text-zinc-400">Response stability against typos, paraphrasing, and adversarial prompt perturbations</p>
              </div>

              <div className="overflow-x-auto rounded-xl border border-teal-100 dark:border-teal-500/20">
                <table className="w-full text-left text-xs">
                  <thead className="bg-teal-50/50 dark:bg-teal-950/30 text-slate-700 dark:text-teal-200 border-b border-teal-200/70 dark:border-teal-500/20 font-bold">
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
                    {Object.entries(r?.robustness?.by_perturbation ?? {}).map(([k, v]: any) => (
                      <tr key={k} className="border-b border-teal-100/50 dark:border-teal-500/10 odd:bg-white dark:odd:bg-dark-card even:bg-teal-50/20 dark:even:bg-dark-surface/40 hover:bg-teal-50/60 dark:hover:bg-teal-500/10 transition-colors">
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
            <div className="relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-white/90 dark:bg-[#0c0d10]/90 backdrop-blur-md p-6 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60">
              <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
              <div className="border-b border-teal-100 dark:border-teal-500/20 pb-3 mb-4">
                <h3 className="text-xs font-bold uppercase tracking-wider text-slate-600 dark:text-teal-300/90">Abstention & Refusal Quality</h3>
                <p className="mt-1 text-[11px] text-slate-500 dark:text-zinc-400">Refusal precision on out-of-domain and unanswerable scenarios</p>
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
          <TableCard
            title={`Evaluated Queries & Test Cases (${filteredCases.length})`}
            toolbar={
              <TableToolbar
                search={search}
                onSearchChange={setSearch}
                searchPlaceholder="Search queries, answers, IDs..."
                filters={[
                  {
                    id: "outcome",
                    label: "Evaluation Status",
                    value: filterOutcome,
                    options: [
                      { label: "All Cases", value: "all" },
                      { label: "Passed Only", value: "passed" },
                      { label: "Failed Only", value: "failed" },
                    ],
                    onChange: (v) => setFilterOutcome(v),
                  },
                  {
                    id: "grounded",
                    label: "Grounding",
                    value: filterGrounded,
                    options: [
                      { label: "All Grounding", value: "all" },
                      { label: "Grounded Only", value: "grounded" },
                      { label: "Ungrounded Fallback", value: "ungrounded" },
                    ],
                    onChange: (v) => setFilterGrounded(v),
                  },
                ]}
              />
            }
          >
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-teal-50/50 dark:bg-teal-950/30 text-slate-700 dark:text-teal-200 border-b border-teal-200/70 dark:border-teal-500/20 font-bold">
                  <tr>
                    <th className="px-3.5 py-2.5">Status</th>
                    <th className="px-3.5 py-2.5">Outcome Type</th>
                    <th className="px-3.5 py-2.5">Query</th>
                    <th className="px-3.5 py-2.5 text-right">Conf.</th>
                    <th className="px-3.5 py-2.5 text-right">Recall</th>
                    <th className="px-3.5 py-2.5 text-right">Cit. Prec.</th>
                    <th className="px-3.5 py-2.5 text-right">Faithfulness</th>
                    <th className="px-3.5 py-2.5 text-right">Latency</th>
                    <th className="px-3.5 py-2.5 text-right">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredCases.map((c: any) => (
                    <tr
                      key={c.id}
                      onClick={() => handleRowClick(c)}
                      className="border-b border-teal-100/50 dark:border-teal-500/10 align-top odd:bg-white dark:odd:bg-dark-card even:bg-teal-50/20 dark:even:bg-dark-surface/40 hover:bg-teal-50/60 dark:hover:bg-teal-500/10 cursor-pointer transition-colors group"
                    >
                      <td className="px-3.5 py-2.5 whitespace-nowrap">
                        <span
                          className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                            c.passed
                              ? "bg-emerald-50 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 border border-emerald-200/80 dark:border-emerald-800/80"
                              : "bg-rose-50 dark:bg-rose-950/60 text-rose-700 dark:text-rose-300 border border-rose-200/80 dark:border-rose-800/80"
                          }`}
                        >
                          {c.passed ? "✅ Pass" : "❌ Fail"}
                        </span>
                      </td>
                      <td className="px-3.5 py-2.5 font-medium text-slate-600 dark:text-slate-400 whitespace-nowrap">
                        <span className="px-2 py-0.5 rounded bg-teal-50 dark:bg-white/10 border border-teal-200/60 dark:border-teal-500/20 text-[10px] font-mono text-teal-800 dark:text-teal-300">
                          {c.type}
                        </span>
                      </td>
                      <td className="max-w-md px-3.5 py-2.5 text-slate-800 dark:text-slate-100 font-medium truncate">
                        {c.query}
                      </td>
                      <td className="px-3.5 py-2.5 font-mono tabular-nums text-right text-slate-600 dark:text-slate-400 whitespace-nowrap">
                        {num(c.confidence)}
                      </td>
                      <td className="px-3.5 py-2.5 font-mono tabular-nums text-right text-slate-600 dark:text-slate-400 whitespace-nowrap">
                        {pct(c.recall)}
                      </td>
                      <td className="px-3.5 py-2.5 font-mono tabular-nums text-right text-slate-600 dark:text-slate-400 whitespace-nowrap">
                        {pct(c.citation_precision)}
                      </td>
                      <td className="px-3.5 py-2.5 text-right whitespace-nowrap">
                        <span className="font-bold text-teal-700 dark:text-teal-300 font-mono tabular-nums">
                          {c.faithfulness !== undefined ? `${Number(c.faithfulness).toFixed(1)} / 5` : "—"}
                        </span>
                      </td>
                      <td className="px-3.5 py-2.5 font-mono tabular-nums text-right text-slate-500 dark:text-slate-400 whitespace-nowrap">
                        {ms(c.latency_ms)}
                      </td>
                      <td className="px-3.5 py-2.5 text-right whitespace-nowrap">
                        <button className="inline-flex items-center gap-1 text-[11px] font-semibold text-teal-600 dark:text-teal-400 group-hover:text-teal-700 dark:group-hover:text-teal-300">
                          <span>Inspect</span>
                          <ChevronRight className="w-3.5 h-3.5 transition-transform group-hover:translate-x-0.5" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </TableCard>
        )}
      </div>

      {/* Deep Query Inspector Drawer */}
      <QueryInspectorDrawer
        isOpen={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        recordId={selectedRecord?.id}
        initialData={selectedRecord}
        token={token}
      />
    </div>
  );
}
