"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useAudience } from "@/lib/audience-context";
import { formatLatencyMs } from "@/lib/format";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import {
  LayoutDashboard,
  Users,
  UserCheck,
  MessagesSquare,
  Layers,
  Search,
  ThumbsUp,
  Gauge,
  AlertTriangle,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

type QueryTrendPoint = { date: string; queries: number };
type LatencyStage = { stage: string; ms: number };
type PipelineStatus = {
  embedding_ms?: number | null;
  retrieval_ms?: number | null;
  reranker_enabled?: boolean;
  reranker_ms?: number | null;
  llm_ms?: number | null;
};

type DashboardData = {
  users: number;
  active_users: number;
  sessions: number;
  conversations: number;
  queries: number;
  feedback: number;
  avg_latency_ms: number;
  error_rate: number;
  pipeline?: PipelineStatus;
  governance?: Record<string, string>;
  query_trend?: QueryTrendPoint[];
  latency_breakdown?: LatencyStage[];
};

const emptyData: DashboardData = {
  users: 0,
  active_users: 0,
  sessions: 0,
  conversations: 0,
  queries: 0,
  feedback: 0,
  avg_latency_ms: 0,
  error_rate: 0,
  query_trend: [],
  latency_breakdown: [],
};

const PIE_COLORS = ["#6366f1", "#06b6d4", "#f59e0b", "#10b981"];

const ACCENTS = {
  indigo: { bg: "bg-indigo-500", soft: "bg-indigo-50", text: "text-indigo-500" },
  cyan: { bg: "bg-cyan-500", soft: "bg-cyan-50", text: "text-cyan-500" },
  amber: { bg: "bg-amber-500", soft: "bg-amber-50", text: "text-amber-500" },
  emerald: { bg: "bg-emerald-500", soft: "bg-emerald-50", text: "text-emerald-500" },
  fuchsia: { bg: "bg-fuchsia-500", soft: "bg-fuchsia-50", text: "text-fuchsia-500" },
  rose: { bg: "bg-rose-500", soft: "bg-rose-50", text: "text-rose-500" },
  sky: { bg: "bg-sky-500", soft: "bg-sky-50", text: "text-sky-500" },
  orange: { bg: "bg-orange-500", soft: "bg-orange-50", text: "text-orange-500" },
} as const;

type AccentKey = keyof typeof ACCENTS;

function MetricCard({
  title,
  value,
  subtitle,
  icon: Icon,
  accent,
}: {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: React.ElementType;
  accent: AccentKey;
}) {
  const colors = ACCENTS[accent];
  return (
    <div className="group relative overflow-hidden rounded-xl border border-slate-200 bg-white p-5 transition-shadow hover:shadow-md">
      <div
        className={`absolute -right-4 -top-4 h-20 w-20 rounded-full opacity-10 transition-transform group-hover:scale-110 ${colors.bg}`}
      />
      <div className="flex items-start justify-between">
        <p className="text-sm font-medium text-slate-500">{title}</p>
        <div className={`rounded-lg p-2 ${colors.soft}`}>
          <Icon className={`h-4 w-4 ${colors.text}`} />
        </div>
      </div>

      <p className="mt-3 text-2xl font-semibold text-slate-900">{value}</p>

      {subtitle && <p className="mt-1 text-xs text-slate-400">{subtitle}</p>}
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
    <section className="rounded-xl border border-slate-200 bg-white p-6">
      <h2 className="text-base font-semibold text-slate-900">{title}</h2>
      {subtitle && <p className="mt-1 text-xs text-slate-500">{subtitle}</p>}
      <div className="mt-5 h-64 w-full">{children}</div>
    </section>
  );
}

export default function AdminDashboard() {
  const { token } = useAuth();
  const { audience } = useAudience();
  const [data, setData] = useState<DashboardData>(emptyData);

  useEffect(() => {
    if (!token) return;
    const qs = audience && audience !== "overview" ? `?audience=${audience}` : "";
    api.adminGet<DashboardData>(token, `/dashboard${qs}`).then(setData).catch(() => {});
  }, [token, audience]);

  const trend = useMemo(
    () =>
      (data.query_trend ?? []).map((d) => ({
        ...d,
        label: new Date(d.date).toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
        }),
      })),
    [data.query_trend]
  );

  const latency = useMemo(
    () => (data.latency_breakdown ?? []).map((d) => ({ stage: d.stage, seconds: d.ms / 1000 })),
    [data.latency_breakdown]
  );

  const engagementPie = useMemo(
    () => [
      { name: "Active Users", value: data.active_users },
      { name: "Other Users", value: Math.max(data.users - data.active_users, 0) },
    ],
    [data.users, data.active_users]
  );

  return (
    <div className="min-h-screen bg-slate-50/50">
      <DashboardHeader
        title="Dashboard"
        subtitle="Monitor users, conversations, RAG performance and governance."
        icon={LayoutDashboard}
      />

      <div className="p-6 sm:p-8 space-y-6 max-w-[1600px] mx-auto w-full">
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard title="Total Users" value={data.users} icon={Users} accent="indigo" />
        <MetricCard
          title="Active Users"
          value={data.active_users}
          subtitle="Last 24h"
          icon={UserCheck}
          accent="cyan"
        />
        <MetricCard title="Sessions" value={data.sessions} icon={Layers} accent="amber" />
        <MetricCard
          title="Conversations"
          value={data.conversations}
          icon={MessagesSquare}
          accent="emerald"
        />
        <MetricCard title="Queries" value={data.queries} icon={Search} accent="fuchsia" />
        <MetricCard title="Feedback" value={data.feedback} icon={ThumbsUp} accent="rose" />
        <MetricCard
          title="Average Latency"
          value={formatLatencyMs(data.avg_latency_ms)}
          icon={Gauge}
          accent="sky"
        />
        <MetricCard
          title="Error Rate"
          value={`${data.error_rate.toFixed(2)}%`}
          icon={AlertTriangle}
          accent="orange"
        />
      </div>

      <div className="mt-8 grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <ChartCard title="Query Volume" subtitle="User queries over the last 7 days">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={trend}>
                <defs>
                  <linearGradient id="queryFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.35} />
                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 12, fill: "#64748b" }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  allowDecimals={false}
                  tick={{ fontSize: 12, fill: "#64748b" }}
                  axisLine={false}
                  tickLine={false}
                  width={30}
                />
                <Tooltip
                  contentStyle={{
                    borderRadius: 8,
                    border: "1px solid #e2e8f0",
                    fontSize: 12,
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="queries"
                  stroke="#6366f1"
                  strokeWidth={2}
                  fill="url(#queryFill)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>

        <ChartCard title="User Engagement" subtitle="Active vs. total users">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={engagementPie}
                dataKey="value"
                nameKey="name"
                innerRadius={55}
                outerRadius={85}
                paddingAngle={3}
              >
                {engagementPie.map((_, idx) => (
                  <Cell key={idx} fill={PIE_COLORS[idx % PIE_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  borderRadius: 8,
                  border: "1px solid #e2e8f0",
                  fontSize: 12,
                }}
              />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-3">
        <ChartCard title="Pipeline Latency" subtitle="Average seconds per stage (last 24h)">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={latency}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
              <XAxis
                dataKey="stage"
                tick={{ fontSize: 12, fill: "#64748b" }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 12, fill: "#64748b" }}
                axisLine={false}
                tickLine={false}
                width={30}
              />
              <Tooltip
                contentStyle={{
                  borderRadius: 8,
                  border: "1px solid #e2e8f0",
                  fontSize: 12,
                }}
                formatter={(v) => [`${Number(v).toFixed(2)} s`, "Latency"]}
              />
              <Bar dataKey="seconds" radius={[6, 6, 0, 0]}>
                {latency.map((_, idx) => (
                  <Cell key={idx} fill={PIE_COLORS[idx % PIE_COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <section className="rounded-xl border border-slate-200 bg-white p-6">
          <h2 className="text-base font-semibold text-slate-900">RAG Pipeline</h2>

          <div className="mt-5 space-y-4">
            <MetricRow label="Embedding" value={pipelineLatencyLabel(data.pipeline?.embedding_ms)} />
            <MetricRow label="Retrieval" value={pipelineLatencyLabel(data.pipeline?.retrieval_ms)} />
            <MetricRow
              label="Reranker"
              value={
                data.pipeline?.reranker_enabled
                  ? `Enabled · ${pipelineLatencyLabel(data.pipeline?.reranker_ms)}`
                  : "Disabled"
              }
            />
            <MetricRow label="LLM" value={pipelineLatencyLabel(data.pipeline?.llm_ms)} />
          </div>
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-6">
          <h2 className="text-base font-semibold text-slate-900">Governance</h2>

          <div className="mt-5 space-y-4">
            <MetricRow label="Monitoring" value={data.governance?.monitoring ?? "—"} />
            <MetricRow label="Tracing" value={data.governance?.tracing ?? "—"} />
            <MetricRow label="Audit Logs" value={data.governance?.audit_logs ?? "—"} />
            <MetricRow label="Guardrail Flags" value={data.governance?.guardrails ?? "—"} />
          </div>
        </section>
      </div>
      </div>
    </div>
  );
}

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-slate-100 pb-3 last:border-0">
      <span className="text-sm text-slate-600">{label}</span>
      <span className="text-sm font-medium text-slate-900">{value}</span>
    </div>
  );
}

function pipelineLatencyLabel(ms: number | null | undefined): string {
  return ms == null ? "No data yet" : `${formatLatencyMs(ms)} avg (24h)`;
}
