"use client";

import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { chartTooltipStyle, useChartTheme } from "@/lib/use-chart-theme";
import { useAudience } from "@/lib/audience-context";
import { formatLatencyMs } from "@/lib/format";
import { CheckCircle2, Target, ShieldCheck, ClipboardCheck, Sparkles } from "lucide-react";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TabGroup, EVALUATION_TABS } from "@/components/admin/TabGroup";
import { TableCard } from "@/components/admin/TableCard";
import { TableToolbar } from "@/components/admin/TableToolbar";
import { DataTable, type ColumnDef } from "@/components/admin/DataTable";
import { ChartCard, toChartTable } from "@/components/charts/ChartCard";
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


const pct = (v: unknown) =>
  typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—";
const num = (v: unknown, d = 2) =>
  typeof v === "number" ? v.toFixed(d) : "—";
const ms = formatLatencyMs;
const label = (k: string) => k.replace(/[_@]/g, " ").replace(/^./, (c) => c.toUpperCase());

function statusColor(value: string): string {
  if (!value.endsWith("%")) return "text-ink dark:text-slate-100";
  const n = parseFloat(value);
  if (Number.isNaN(n)) return "text-ink dark:text-slate-100";
  if (n >= 80) return "text-emerald-600 dark:text-emerald-400";
  if (n >= 50) return "text-amber-600 dark:text-amber-400";
  return "text-red-600 dark:text-red-400";
}

function statusBar(value: string): string {
  if (!value.endsWith("%")) return "bg-ink/20";
  const n = parseFloat(value);
  if (Number.isNaN(n)) return "bg-ink/20";
  if (n >= 80) return "bg-emerald-500";
  if (n >= 50) return "bg-amber-500";
  return "bg-red-500";
}

function Stat({ label, value }: { label: string; value: string }) {
  const isPct = value.endsWith("%");
  const pctNum = isPct ? Math.min(Math.max(parseFloat(value) || 0, 0), 100) : null;
  return (
    <div className="rounded-xl border border-ink/10 dark:border-dark-border bg-white dark:bg-dark-card px-4 py-3.5 transition-shadow hover:shadow-sm">
      <p className="text-[11px] uppercase tracking-wide text-ink/40 dark:text-slate-500">{label}</p>
      <p className={`mt-1 text-xl font-semibold ${isPct ? statusColor(value) : "text-ink dark:text-white"}`}>
        {value}
      </p>
      {pctNum !== null && (
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-ink/5 dark:bg-dark-surface">
          <div
            className={`h-full rounded-full ${statusBar(value)}`}
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
  accent: string;
}) {
  return (
    <div className="group relative overflow-hidden rounded-xl border border-ink/10 dark:border-dark-border bg-white dark:bg-dark-card p-5 transition-shadow hover:shadow-md">
      <div
        className={`absolute -right-4 -top-4 h-20 w-20 rounded-full opacity-10 transition-transform group-hover:scale-110 ${accent}`}
      />
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-ink/50 dark:text-slate-400">{label}</p>
        <Icon className="h-4 w-4 text-ink/40 dark:text-slate-500" />
      </div>
      <p className="mt-2 text-2xl font-semibold text-ink dark:text-white">{value}</p>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-6 rounded-xl border border-ink/10 dark:border-dark-border bg-white dark:bg-dark-card p-5">
      <h2 className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink/50 dark:text-slate-400">
        <span className="h-3 w-1 rounded-full bg-brand-500" />
        {title}
      </h2>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">{children}</div>
    </section>
  );
}

function Table({
  title,
  head,
  rows,
}: {
  title: string;
  head: string[];
  rows: (string | number)[][];
}) {
  if (!rows.length) return null;
  return (
    <section className="mb-6 rounded-xl border border-ink/10 dark:border-dark-border bg-white dark:bg-dark-card p-5">
      <h2 className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink/50 dark:text-slate-400">
        <span className="h-3 w-1 rounded-full bg-brand-500" />
        {title}
      </h2>
      <div className="overflow-x-auto rounded-lg border border-ink/10 dark:border-white/10">
        <table className="w-full text-left text-xs">
          <thead className="bg-ink/5 dark:bg-dark-surface text-ink/60 dark:text-slate-400 border-b border-ink/5 dark:border-white/10">
            <tr>
              {head.map((h, idx) => {
                const isNumericCol = rows.some((row) => {
                  const cell = row[idx];
                  return (
                    typeof cell === "number" ||
                    (typeof cell === "string" && (cell.endsWith("%") || /^\d+(\.\d+)?$/.test(cell.trim())))
                  );
                });
                return (
                  <th
                    key={h}
                    className={`px-3 py-2 font-medium ${isNumericCol ? "text-right" : "text-left"}`}
                  >
                    {h}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="divide-y divide-ink/5 dark:divide-white/5">
            {rows.map((row, i) => (
              <tr
                key={i}
                className="odd:bg-white dark:odd:bg-dark-card even:bg-ink/[0.02] dark:even:bg-dark-surface/40 hover:bg-brand-50/40 dark:hover:bg-brand-950/30 transition-colors"
              >
                {row.map((cell, j) => {
                  const isNum =
                    typeof cell === "number" ||
                    (typeof cell === "string" && (cell.endsWith("%") || /^\d+(\.\d+)?$/.test(cell.trim())));
                  return (
                    <td
                      key={j}
                      className={`px-3 py-2 text-ink/80 dark:text-slate-200 ${
                        isNum ? "text-right font-mono tabular-nums" : ""
                      }`}
                    >
                      {cell}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

const statsFrom = (obj: any, fmt: (v: unknown) => string, skip: string[] = []) =>
  Object.entries(obj ?? {})
    .filter(([k, v]) => typeof v === "number" && !skip.includes(k))
    .map(([k, v]) => <Stat key={k} label={label(k)} value={fmt(v)} />);

const TABS = [
  "Overall",
  "Retrieval",
  "Citation",
  "Answer quality",
  "Correctness",
  "Helpfulness",
  "Abstention",
  "Safety",
  "Robustness",
  "Results by case type",
  "All test cases",
  "Other",
] as const;
type Tab = (typeof TABS)[number];

export default function EvaluationPage() {
  const { token } = useAuth();
  const ct = useChartTheme();
  const tooltipStyle = chartTooltipStyle(ct);
  // Pass / fail / partial outcome colours.
  const pieColors = [ct.status.ok, ct.status.danger, ct.status.warn];
  const { audience } = useAudience();
  const [r, setR] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "failed">("all");
  const [caseSearch, setCaseSearch] = useState("");
  const [activeTab, setActiveTab] = useState<Tab>("Overall");

  useEffect(() => {
    if (!token) return;
    api
      .evaluationReport(token, audience)
      .then(setR)
      .catch((err) =>
        setError(
          err instanceof ApiError ? err.message : "Couldn't load evaluation report."
        )
      );
  }, [token, audience]);

  const shown = useMemo(
    () => (r?.cases ?? []).filter((c: any) => (filter === "failed" ? !c.passed : true)),
    [r?.cases, filter]
  );

  const displayedCases = useMemo(() => {
    if (!caseSearch.trim()) return shown;
    const q = caseSearch.toLowerCase();
    return shown.filter((c: any) => {
      const matchId = String(c.id ?? "").toLowerCase().includes(q);
      const matchQuery = String(c.query ?? "").toLowerCase().includes(q);
      const matchType = String(c.type ?? "").toLowerCase().includes(q);
      const matchNotes = (c.flags ?? []).join(" ").toLowerCase().includes(q);
      return matchId || matchQuery || matchType || matchNotes;
    });
  }, [shown, caseSearch]);

  const caseColumns: ColumnDef<any>[] = [
    {
      key: "passed",
      header: "Status",
      align: "center",
      render: (c) => (
        <span
          className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium border ${
            c.passed
              ? "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800"
              : "bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 border-rose-200 dark:border-rose-800"
          }`}
        >
          {c.passed ? "Pass" : "Fail"}
        </span>
      ),
    },
    {
      key: "id",
      header: "ID",
      render: (c) => (
        <span className="font-mono text-xs font-medium text-slate-900 dark:text-white">
          {c.id}
        </span>
      ),
    },
    {
      key: "type",
      header: "Type",
      render: (c) => (
        <span className="text-xs text-slate-600 dark:text-slate-300">
          {c.type}
        </span>
      ),
    },
    {
      key: "query",
      header: "Query",
      render: (c) => (
        <span className="text-xs text-slate-800 dark:text-slate-200 max-w-xs block line-clamp-2" title={c.query}>
          {c.query}
        </span>
      ),
    },
    {
      key: "confidence",
      header: "Conf.",
      align: "right",
      render: (c) => (
        <span className="font-mono text-xs tabular-nums text-slate-800 dark:text-slate-200">
          {num(c.confidence)}
        </span>
      ),
    },
    {
      key: "recall",
      header: "Recall",
      align: "right",
      render: (c) => (
        <span className="font-mono text-xs tabular-nums text-slate-800 dark:text-slate-200">
          {pct(c.recall)}
        </span>
      ),
    },
    {
      key: "citation_precision",
      header: "Cit. Prec.",
      align: "right",
      render: (c) => (
        <span className="font-mono text-xs tabular-nums text-slate-800 dark:text-slate-200">
          {pct(c.citation_precision)}
        </span>
      ),
    },
    {
      key: "faithfulness",
      header: "Faithfulness",
      align: "right",
      render: (c) => (
        <span className="font-mono text-xs tabular-nums text-slate-800 dark:text-slate-200">
          {c.faithfulness != null ? num(c.faithfulness) : "—"}
        </span>
      ),
    },
    {
      key: "latency_ms",
      header: "Latency",
      align: "right",
      render: (c) => (
        <span className="font-mono text-xs tabular-nums text-slate-600 dark:text-slate-400">
          {ms(c.latency_ms)}
        </span>
      ),
    },
    {
      key: "flags",
      header: "Flags & Notes",
      render: (c) => {
        const notes = [
          ...(c.flags ?? []),
          c.abstained ? "abstained" : "",
          ...(c.missing_keywords ?? []).map((k: string) => `missing: ${k}`),
          c.error ?? "",
        ].filter(Boolean);
        return (
          <span className="text-xs text-slate-500 dark:text-slate-400">
            {notes.length ? notes.join(", ") : "—"}
          </span>
        );
      },
    },
  ];

  const passBreakdown = useMemo(() => {
    const casesList: any[] = r?.cases ?? [];
    const passed = casesList.filter((c) => c.passed && !c.known_issue).length;
    const failed = casesList.filter((c) => !c.passed && !c.known_issue).length;
    return [
      { name: "Passed", value: passed },
      { name: "Failed", value: failed },
    ].filter((d) => d.value > 0);
  }, [r?.cases]);

  const qualityRadar = useMemo(
    () => [
      { metric: "Retrieval recall", score: (r?.retrieval_recall ?? 0) * 100 },
      { metric: "Groundedness", score: (r?.groundedness_accuracy ?? 0) * 100 },
      { metric: "Heading hit", score: (r?.heading_hit_rate ?? 0) * 100 },
      { metric: "Keyword pass", score: (r?.keyword_pass_rate ?? 0) * 100 },
      { metric: "Length OK", score: (r?.length_ok_rate ?? 0) * 100 },
      { metric: "Follow-up", score: (r?.follow_up_accuracy ?? 0) * 100 },
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
      p50: (v.p50 ?? 0) / 1000,
      p95: (v.p95 ?? 0) / 1000,
      p99: (v.p99 ?? 0) / 1000,
    }));
  }, [r?.operational?.latency_ms]);

  return (
    <div className="flex flex-col min-h-screen bg-surface-page text-ink dark:text-slate-100">
      <DashboardHeader
        title="RAG Evaluation"
        subtitle="Systematic evaluation metrics, benchmark pass rates, and turn-level quality scorecard"
        icon={ClipboardCheck}
      />
      <div className="flex-1 p-6 sm:p-8 space-y-6 max-w-[1600px] mx-auto w-full">
      {error && <p className="text-sm text-red-600">{error}</p>}
      {!r && !error && <p className="text-sm text-ink/50">Loading…</p>}

      {r && (
        <>
          <div className="flex items-center justify-between overflow-x-auto pb-1">
            <TabGroup
              tabs={EVALUATION_TABS}
              activeTab={activeTab}
              onChange={(t) => setActiveTab(t as Tab)}
              ariaLabel="Evaluation sub-tabs"
            />
          </div>

          {activeTab === "Overall" && (
          <>
          {/* Unified 6-stat hero grid consolidating redundant rows */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4">
            <HeroStat label="Pass rate" value={pct(r.pass_rate)} icon={CheckCircle2} accent="bg-emerald-500" />
            <HeroStat label="Cases total" value={String(r.n_cases ?? "—")} icon={Target} accent="bg-slate-500" />
            <HeroStat label="Cases scored" value={String(r.n_scored ?? "—")} icon={Target} accent="bg-indigo-500" />
            <HeroStat label="Retrieval recall" value={pct(r.retrieval_recall)} icon={ShieldCheck} accent="bg-cyan-500" />
            <HeroStat label="Groundedness" value={pct(r.groundedness_accuracy)} icon={CheckCircle2} accent="bg-purple-500" />
            <HeroStat label="Follow-up acc." value={pct(r.follow_up_accuracy)} icon={Sparkles} accent="bg-amber-500" />
          </div>

          <div className="mb-8 grid grid-cols-1 gap-6 xl:grid-cols-3">
            <div className="xl:col-span-2">
              <ChartCard
                title="Quality scorecard"
                subtitle="Key evaluation metrics (%)"
                titleStyle="eyebrow"
                table={toChartTable(qualityRadar, [
                  { key: "metric", label: "Metric" },
                  { key: "score", label: "Score", numeric: true, format: (v) => `${Number(v).toFixed(1)}%` },
                ])}
              >
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart data={qualityRadar} outerRadius="75%">
                    <PolarGrid stroke={ct.grid} />
                    <PolarAngleAxis dataKey="metric" tick={{ fontSize: 11, fill: ct.axis }} />
                    <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fontSize: 10, fill: ct.axis }} />
                    <Radar
                      name="Score"
                      dataKey="score"
                      stroke={ct.categorical[5]}
                      fill={ct.categorical[5]}
                      fillOpacity={0.35}
                    />
                    <Tooltip
                      contentStyle={tooltipStyle}
                      formatter={(v) => [`${Number(v).toFixed(1)}%`, "Score"]}
                    />
                  </RadarChart>
                </ResponsiveContainer>
              </ChartCard>
            </div>

            <ChartCard
              title="Case outcomes"
              subtitle="Pass / fail split"
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
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Tooltip contentStyle={tooltipStyle} />
                </PieChart>
              </ResponsiveContainer>
            </ChartCard>
          </div>

          <div className="mb-8 grid grid-cols-1 gap-6 xl:grid-cols-2">
            <ChartCard
              title="Pass rate by case type"
              subtitle="Share of cases passing, per category"
              titleStyle="eyebrow"
              table={toChartTable(caseTypeBars, [
                { key: "type", label: "Case type" },
                { key: "passRate", label: "Pass rate", numeric: true, format: (v) => `${String(v)}%` },
              ])}
            >
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={caseTypeBars}>
                  <CartesianGrid strokeDasharray="3 3" stroke={ct.grid} vertical={false} />
                  <XAxis dataKey="type" tick={{ fontSize: 11, fill: ct.axis }} axisLine={false} tickLine={false} />
                  <YAxis
                    tick={{ fontSize: 11, fill: ct.axis }}
                    axisLine={false}
                    tickLine={false}
                    width={30}
                    domain={[0, 100]}
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
              title="Latency by stage"
              subtitle="p50 / p95 / p99 (seconds)"
              titleStyle="eyebrow"
              table={toChartTable(latencyBars, [
                { key: "stage", label: "Stage" },
                { key: "p50", label: "p50", numeric: true, format: (v) => `${Number(v).toFixed(2)} s` },
                { key: "p95", label: "p95", numeric: true, format: (v) => `${Number(v).toFixed(2)} s` },
                { key: "p99", label: "p99", numeric: true, format: (v) => `${Number(v).toFixed(2)} s` },
              ])}
            >
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={latencyBars}>
                  <CartesianGrid strokeDasharray="3 3" stroke={ct.grid} vertical={false} />
                  <XAxis dataKey="stage" tick={{ fontSize: 11, fill: ct.axis }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 11, fill: ct.axis }} axisLine={false} tickLine={false} width={40} />
                  <Tooltip
                    contentStyle={tooltipStyle}
                    formatter={(v) => [`${Number(v).toFixed(2)} s`, ""]}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar dataKey="p50" name="p50" fill={ct.categorical[5]} radius={[4, 4, 0, 0]} />
                  <Bar dataKey="p95" name="p95" fill={ct.status.warn} radius={[4, 4, 0, 0]} />
                  <Bar dataKey="p99" name="p99" fill={ct.status.danger} radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
          </div>
          </>
          )}

          {activeTab === "Retrieval" && (
          <Section title="Retrieval">
            {statsFrom(r.retrieval, (v) => num(v, 3), ["noise_ratio", "context_coverage"])}
          </Section>
          )}

          {activeTab === "Citation" && (
          <Section title="Citation">
            <Stat label="Precision" value={pct(r.citation?.precision)} />
            <Stat label="Coverage" value={pct(r.citation?.coverage)} />
            {statsFrom(r.citation?.quality, pct)}
          </Section>
          )}

          {activeTab === "Answer quality" && (
          <Section title="Answer quality — LLM judge (1–5)">
            <Stat label="Faithfulness" value={num(r.judge?.faithfulness)} />
            <Stat label="Relevance" value={num(r.judge?.relevance)} />
            <Stat label="Completeness" value={num(r.judge?.completeness)} />
          </Section>
          )}

          {activeTab === "Correctness" && (
          <Section title="Correctness (1–5)">{statsFrom(r.deep_judge?.correctness, num)}</Section>
          )}

          {activeTab === "Helpfulness" && (
          <Section title="Helpfulness (1–5)">{statsFrom(r.deep_judge?.helpfulness, num)}</Section>
          )}

          {activeTab === "Abstention" && (
          <>
          <Section title="Abstention">
            <Stat label="Abstention rate" value={pct(r.abstention?.abstention_rate)} />
            <Stat label="False abstention" value={pct(r.abstention?.false_abstention_rate)} />
            <Stat label="Cases" value={String(r.abstention?.n ?? "—")} />
          </Section>
          <Table
            title="Abstention by category"
            head={["Category", "Cases", "Correct abstention rate"]}
            rows={Object.entries(r.abstention?.by_category ?? {}).map(([k, v]: any) => [
              label(k),
              v.n,
              pct(v.rate),
            ])}
          />
          </>
          )}

          {activeTab === "Safety" && (
          <Section title="Safety">
            <Stat label="PII leakage" value={pct(r.safety?.pii_leakage_rate)} />
            <Stat label="Secret exposure" value={pct(r.safety?.secret_exposure_rate)} />
            <Stat label="Injection compliance" value={pct(r.safety?.injection_compliance_rate)} />
            <Stat label="Unauthorized retrieval" value={pct(r.safety?.unauthorized_retrieval_rate)} />
            <Stat label="Chunks with injection text" value={String(r.safety?.chunks_with_injection_text ?? "—")} />
            <Stat label="Safety passed" value={pct(r.safety?.passed)} />
          </Section>
          )}

          {activeTab === "Robustness" && (
          <>
          <Section title="Robustness (overall)">
            {statsFrom(r.robustness?.overall, (v) => num(v, 3), ["n"])}
          </Section>
          <Table
            title="Robustness by perturbation"
            head={["Perturbation", "Cases", "Retrieval stability", "Answer consistency", "Citation consistency", "Pass (perturbed)"]}
            rows={Object.entries(r.robustness?.by_perturbation ?? {}).map(([k, v]: any) => [
              label(k),
              v.n,
              num(v.retrieval_stability),
              num(v.answer_consistency),
              num(v.citation_consistency),
              pct(v.pass_rate_perturbed),
            ])}
          />
          </>
          )}

          {activeTab === "Results by case type" && (
          <Table
            title="Results by case type"
            head={["Type", "Cases", "Passed", "Pass rate"]}
            rows={Object.entries(r.by_case_type ?? {}).map(([k, v]: any) => [
              label(k),
              v.n,
              v.passed,
              pct(v.pass_rate),
            ])}
          />
          )}

          {activeTab === "Other" && (
          <>
          <Section title="Operational">
            <Stat label="Availability" value={pct(r.operational?.availability)} />
            <Stat label="Retrieval failure rate" value={pct(r.operational?.retrieval_failure_rate)} />
            <Stat label="Model error rate" value={pct(r.operational?.model_error_rate)} />
            <Stat label="Input tokens" value={String(r.operational?.tokens?.input_total ?? "—")} />
            <Stat label="Output tokens" value={String(r.operational?.tokens?.output_total ?? "—")} />
            <Stat label="Retrieved tokens" value={String(r.operational?.tokens?.retrieved_total ?? "—")} />
            <Stat label="Avg retrieved / query" value={num(r.operational?.tokens?.mean_retrieved_per_query, 0)} />
          </Section>
          {r.kb && (
            <section className="mb-7 rounded-xl border border-ink/10 dark:border-dark-border bg-white dark:bg-dark-card p-5">
              <h2 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-ink/50 dark:text-slate-400">
                <span className="h-3 w-1 rounded-full bg-brand-500" />
                Knowledge-base evaluation
              </h2>
              <pre className="max-h-80 overflow-auto rounded-lg border border-ink/10 dark:border-white/10 bg-ink/[0.03] dark:bg-dark-surface p-3 text-[11px] text-ink/70 dark:text-slate-300">
                {JSON.stringify(r.kb, null, 2)}
              </pre>
            </section>
          )}
          </>
          )}

          {activeTab === "All test cases" && (
          <TableCard
            title={`All test cases (${displayedCases.length})`}
            toolbar={
              <TableToolbar
                search={caseSearch}
                onSearchChange={setCaseSearch}
                searchPlaceholder="Search query, ID, type, or notes…"
                filters={[
                  {
                    id: "outcome",
                    label: "Case Outcome",
                    value: filter,
                    options: [
                      { label: "All Cases", value: "all" },
                      { label: "Failed Only", value: "failed" },
                    ],
                    onChange: (v) => setFilter(v as "all" | "failed"),
                  },
                ]}
              />
            }
          >
            <DataTable
              columns={caseColumns}
              data={displayedCases}
              keyField="id"
              emptyMessage="No test cases match the selected filters."
            />
          </TableCard>
          )}
        </>
      )}
      </div>
    </div>
  );
}
