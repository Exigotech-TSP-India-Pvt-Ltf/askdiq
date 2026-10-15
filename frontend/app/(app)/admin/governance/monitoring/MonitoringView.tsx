"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { formatLatencyMs } from "@/lib/format";
import { chartTooltipStyle, useChartTheme } from "@/lib/use-chart-theme";
import { ChartCard, toChartTable } from "@/components/charts/ChartCard";
import {
  Activity,
  AlertTriangle,
  Cpu,
  Database,
  Gauge,
  Search,
  Users,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const pct = (v: unknown, d = 1) =>
  typeof v === "number" ? `${v.toFixed(d)}%` : "—";

const ACCENTS = {
  indigo: { bg: "bg-indigo-500", soft: "bg-indigo-50 dark:bg-indigo-500/15", text: "text-indigo-500" },
  cyan: { bg: "bg-cyan-500", soft: "bg-cyan-50 dark:bg-cyan-500/15", text: "text-cyan-500" },
  amber: { bg: "bg-amber-500", soft: "bg-amber-50 dark:bg-amber-500/15", text: "text-amber-500" },
  emerald: { bg: "bg-emerald-500", soft: "bg-emerald-50 dark:bg-emerald-500/15", text: "text-emerald-500" },
  rose: { bg: "bg-rose-500", soft: "bg-rose-50 dark:bg-rose-500/15", text: "text-rose-500" },
} as const;

type AccentKey = keyof typeof ACCENTS;

function HeroStat({
  label,
  value,
  icon: Icon,
  accent,
}: {
  label: string;
  value: string;
  icon: React.ElementType;
  accent: AccentKey;
}) {
  const colors = ACCENTS[accent];
  return (
    <div className="group relative overflow-hidden rounded-xl border border-line bg-surface-card p-5 transition-shadow hover:shadow-md">
      <div
        className={`absolute -right-4 -top-4 h-20 w-20 rounded-full opacity-10 transition-transform group-hover:scale-110 ${colors.bg}`}
      />
      <div className="flex items-start justify-between">
        <p className="text-sm font-medium text-fg-subtle">{label}</p>
        <div className={`rounded-lg p-2 ${colors.soft}`}>
          <Icon className={`h-4 w-4 ${colors.text}`} />
        </div>
      </div>
      <p className="mt-3 text-2xl font-semibold text-fg">{value}</p>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div className="rounded-lg border border-line bg-surface-card px-4 py-3">
      <p className="text-[11px] uppercase tracking-wide text-fg-subtle">{label}</p>
      <p className="mt-1 text-lg font-semibold text-fg">{value ?? "—"}</p>
    </div>
  );
}

const USAGE_TABS = [
  { key: "llm", label: "LLM Usage", icon: Cpu },
  { key: "embedding", label: "Embedding Usage", icon: Database },
] as const;

type UsageTabKey = (typeof USAGE_TABS)[number]["key"];

export default function MonitoringView({ hideHeader = false }: { hideHeader?: boolean } = {}) {
  const { token } = useAuth();
  const { audience } = useAudience();
  const ct = useChartTheme();
  const [m, setM] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [usageTab, setUsageTab] = useState<UsageTabKey>("llm");
  const [llm, setLlm] = useState<any>({});
  const [embedding, setEmbedding] = useState<any>({});

  useEffect(() => {
    if (!token) return;
    api.adminGet(token, "/usage/llm").then(setLlm).catch(() => {});
    api.adminGet(token, "/usage/embedding").then(setEmbedding).catch(() => {});
  }, [token]);

  useEffect(() => {
    if (!token) return;
    const qs = audience && audience !== "overview" ? `?audience=${audience}` : "";
    const load = () =>
      api
        .adminGet(token, `/governance/monitoring${qs}`)
        .then((d) => {
          setM(d);
          setError(null);
          setLastUpdated(new Date());
        })
        .catch((err) =>
          setError(err instanceof ApiError ? err.message : "Couldn't load monitoring data.")
        );
    load();
    const timer = setInterval(load, 10000);
    return () => clearInterval(timer);
  }, [token, audience]);

  const errorRatePct = m ? m.error_rate : 0;
  const errorAccent: AccentKey = errorRatePct >= 5 ? "rose" : errorRatePct >= 1 ? "amber" : "emerald";

  const latencyBars = [
    { stage: "Embedding", seconds: (m?.embedding_latency_ms ?? 0) / 1000 },
    { stage: "Retrieval", seconds: (m?.retrieval_latency_ms ?? 0) / 1000 },
    { stage: "Reranker", seconds: (m?.reranker_latency_ms ?? 0) / 1000 },
    { stage: "LLM", seconds: (m?.llm_latency_ms ?? 0) / 1000 },
  ];

  return (
    <div>
      {!hideHeader && (
      <header className="flex h-20 shrink-0 items-center justify-between border-b border-line bg-surface-card px-8">
        <div>
          <h1 className="text-sm font-semibold text-fg">Governance Monitoring</h1>
          <p className="text-xs text-fg-subtle">
            Infrastructure, application and RAG pipeline health, refreshed every 10 seconds.
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-full border border-line bg-surface-card px-3 py-1.5 text-xs text-fg-subtle">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
          </span>
          {lastUpdated ? `Live · updated ${lastUpdated.toLocaleTimeString()}` : "Connecting…"}
        </div>
      </header>
      )}

      <div className="p-8">
      {error && (
        <p className="mb-6 rounded-lg bg-status-danger-bg px-3 py-2 text-sm text-status-danger-fg">{error}</p>
      )}

      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4">
        <HeroStat
          label="API Requests"
          value={m ? m.api_requests.toLocaleString() : "—"}
          icon={Activity}
          accent="indigo"
        />
        <HeroStat
          label="Queries (24h)"
          value={m ? m.queries_24h.toLocaleString() : "—"}
          icon={Search}
          accent="cyan"
        />
        <HeroStat
          label="Active Users (24h)"
          value={m ? String(m.active_users) : "—"}
          icon={Users}
          accent="emerald"
        />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4">
        <HeroStat label="Average Latency" value={formatLatencyMs(m?.avg_latency_ms)} icon={Gauge} accent="indigo" />
        <HeroStat
          label="Error Rate"
          value={m ? pct(errorRatePct, 2) : "—"}
          icon={AlertTriangle}
          accent={errorAccent}
        />
      </div>

      <div className="mt-8 grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <ChartCard
            title="Pipeline Latency"
            subtitle="Current measurement per stage (seconds)"
            table={toChartTable(latencyBars, [
              { key: "stage", label: "Stage" },
              { key: "seconds", label: "Seconds", numeric: true },
            ])}
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={latencyBars}>
                <CartesianGrid strokeDasharray="3 3" stroke={ct.grid} vertical={false} />
                <XAxis dataKey="stage" tick={{ fontSize: 12, fill: ct.axis }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 12, fill: ct.axis }} axisLine={false} tickLine={false} width={40} />
                <Tooltip
                  contentStyle={chartTooltipStyle(ct)}
                formatter={(v) => [`${Number(v).toFixed(2)} s`, "Latency"]}
                />
                <Bar dataKey="seconds" radius={[6, 6, 0, 0]}>
                  {latencyBars.map((_, idx) => (
                    <Cell key={idx} fill={ct.categorical[idx % ct.categorical.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>
      </div>

      <section className="mt-8 rounded-xl border border-line bg-surface-card p-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-fg">AI Usage</h2>
            <p className="mt-1 text-xs text-fg-subtle">
              Model usage, resource consumption and pipeline configuration.
            </p>
          </div>
          <div className="flex flex-wrap gap-1 rounded-lg bg-surface-muted p-1">
            {USAGE_TABS.map((tab) => {
              const Icon = tab.icon;
              return (
                <button
                  key={tab.key}
                  onClick={() => setUsageTab(tab.key)}
                  className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition ${
                    usageTab === tab.key
                      ? "bg-surface-card text-fg shadow-sm"
                      : "text-fg-subtle hover:text-fg"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {tab.label}
                </button>
              );
            })}
          </div>
        </div>

        {usageTab === "llm" && (
          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
            <MiniStat label="Requests" value={llm.requests?.toLocaleString?.() ?? llm.requests} />
            <MiniStat label="Successful" value={llm.successful} />
            <MiniStat label="Failed" value={llm.failed} />
            <MiniStat label="Input Tokens" value={llm.input_tokens?.toLocaleString?.() ?? llm.input_tokens} />
            <MiniStat label="Output Tokens" value={llm.output_tokens?.toLocaleString?.() ?? llm.output_tokens} />
            <MiniStat label="Total Tokens" value={llm.total_tokens?.toLocaleString?.() ?? llm.total_tokens} />
            <MiniStat label="Average Latency" value={formatLatencyMs(llm.average_latency_ms)} />
          </div>
        )}

        {usageTab === "embedding" && (
          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
            <MiniStat label="Dimensions" value={embedding.dimensions} />
            <MiniStat label="Documents" value={embedding.documents} />
            <MiniStat label="Chunks" value={embedding.chunks} />
            <MiniStat label="Requests" value={embedding.requests} />
            <MiniStat label="Input Tokens" value={embedding.input_tokens} />
            <MiniStat label="Average Latency" value={formatLatencyMs(embedding.average_latency_ms)} />
          </div>
        )}
      </section>
      </div>
    </div>
  );
}
