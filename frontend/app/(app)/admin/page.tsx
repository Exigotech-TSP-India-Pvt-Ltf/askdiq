"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
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
  Cpu,
  Database,
  Sparkles,
  ShieldCheck,
  Activity,
  ArrowUpRight,
  Zap,
  CheckCircle2,
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

// =============================================================================
// OPTION 2: AURORA GLASS JEWEL PALETTES
// =============================================================================
const PIE_COLORS = ["#06b6d4", "#e2e8f0"]; // Active (Cyan), Other (Slate track)
const STAGE_BAR_COLORS = ["#06b6d4", "#0d9488", "#8b5cf6", "#10b981"]; // Embedding, Retrieval, Reranker, LLM

type AccentKey = "teal" | "emerald" | "cyan" | "violet" | "rose" | "amber";

const ACCENT_STYLES: Record<
  AccentKey,
  {
    border: string;
    glow: string;
    badge: string;
    icon: string;
    pill: string;
  }
> = {
  teal: {
    border: "hover:border-teal-400/60 dark:hover:border-teal-500/40",
    glow: "bg-teal-400/10 dark:bg-teal-500/15",
    badge: "bg-teal-50 text-teal-700 border-teal-200/80 dark:bg-teal-500/15 dark:text-teal-300 dark:border-teal-500/30",
    icon: "text-teal-600 dark:text-teal-300",
    pill: "text-teal-700 bg-teal-50 border-teal-200/60 dark:text-teal-300 dark:bg-teal-500/20 dark:border-teal-500/30",
  },
  emerald: {
    border: "hover:border-emerald-400/60 dark:hover:border-emerald-500/40",
    glow: "bg-emerald-400/10 dark:bg-emerald-500/15",
    badge: "bg-emerald-50 text-emerald-700 border-emerald-200/80 dark:bg-emerald-500/15 dark:text-emerald-300 dark:border-emerald-500/30",
    icon: "text-emerald-600 dark:text-emerald-300",
    pill: "text-emerald-700 bg-emerald-50 border-emerald-200/60 dark:text-emerald-300 dark:bg-emerald-500/20 dark:border-emerald-500/30",
  },
  cyan: {
    border: "hover:border-cyan-400/60 dark:hover:border-cyan-500/40",
    glow: "bg-cyan-400/10 dark:bg-cyan-500/15",
    badge: "bg-cyan-50 text-cyan-700 border-cyan-200/80 dark:bg-cyan-500/15 dark:text-cyan-300 dark:border-cyan-500/30",
    icon: "text-cyan-600 dark:text-cyan-300",
    pill: "text-cyan-700 bg-cyan-50 border-cyan-200/60 dark:text-cyan-300 dark:bg-cyan-500/20 dark:border-cyan-500/30",
  },
  violet: {
    border: "hover:border-violet-400/60 dark:hover:border-violet-500/40",
    glow: "bg-violet-400/10 dark:bg-violet-500/15",
    badge: "bg-violet-50 text-violet-700 border-violet-200/80 dark:bg-violet-500/15 dark:text-violet-300 dark:border-violet-500/30",
    icon: "text-violet-600 dark:text-violet-300",
    pill: "text-violet-700 bg-violet-50 border-violet-200/60 dark:text-violet-300 dark:bg-violet-500/20 dark:border-violet-500/30",
  },
  rose: {
    border: "hover:border-rose-400/60 dark:hover:border-rose-500/40",
    glow: "bg-rose-400/10 dark:bg-rose-500/15",
    badge: "bg-rose-50 text-rose-700 border-rose-200/80 dark:bg-rose-500/15 dark:text-rose-300 dark:border-rose-500/30",
    icon: "text-rose-600 dark:text-rose-300",
    pill: "text-rose-700 bg-rose-50 border-rose-200/60 dark:text-rose-300 dark:bg-rose-500/20 dark:border-rose-500/30",
  },
  amber: {
    border: "hover:border-amber-400/60 dark:hover:border-amber-500/40",
    glow: "bg-amber-400/10 dark:bg-amber-500/15",
    badge: "bg-amber-50 text-amber-700 border-amber-200/80 dark:bg-amber-500/15 dark:text-amber-300 dark:border-amber-500/30",
    icon: "text-amber-600 dark:text-amber-300",
    pill: "text-amber-700 bg-amber-50 border-amber-200/60 dark:text-amber-300 dark:bg-amber-500/20 dark:border-amber-500/30",
  },
};

// =============================================================================
// SUBCOMPONENT: AURORA METRIC CARD
// =============================================================================
function AuroraMetricCard({
  title,
  value,
  subtitle,
  icon: Icon,
  accent,
  isLive,
}: {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: React.ElementType;
  accent: AccentKey;
  isLive?: boolean;
}) {
  const styles = ACCENT_STYLES[accent];

  return (
    <div
      className={`group relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-white dark:bg-[#0c0d10] p-5 shadow-card dark:shadow-xl transition-all hover:border-teal-400 dark:hover:border-teal-400/60 hover:shadow-md ${styles.border}`}
    >
      {/* Ambient Aurora Corner Glow */}
      <div
        className={`absolute -right-6 -top-6 h-24 w-24 rounded-full blur-2xl pointer-events-none transition-transform group-hover:scale-125 ${styles.glow}`}
      />

      <div className="relative z-10 flex items-start justify-between">
        <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-teal-200/80">
          {title}
        </span>
        <div className={`rounded-xl p-2 border ${styles.badge} shadow-2xs`}>
          <Icon className={`h-4 w-4 ${styles.icon}`} />
        </div>
      </div>

      <div className="relative z-10 mt-3 flex items-baseline justify-between">
        <p className="text-2xl font-black font-mono text-slate-900 dark:text-white tracking-tight">
          {value}
        </p>
        {subtitle && (
          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] font-semibold border ${styles.pill}`}
          >
            {isLive && (
              <span className="relative flex h-1.5 w-1.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500" />
              </span>
            )}
            {subtitle}
          </span>
        )}
      </div>
    </div>
  );
}

// =============================================================================
// SUBCOMPONENT: AURORA CHART CONTAINER
// =============================================================================
function AuroraChartCard({
  title,
  subtitle,
  badge,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  badge?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`relative rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-white dark:bg-[#0c0d10] p-6 shadow-card dark:shadow-xl transition-all overflow-hidden hover:border-teal-400 dark:hover:border-teal-400/60 ${className}`}
    >
      {/* Ambient Aurora Glow in top right */}
      <div className="absolute -top-12 -right-12 w-36 h-36 bg-teal-400/10 dark:bg-teal-500/15 rounded-full blur-2xl pointer-events-none" />

      <div className="relative z-10 flex items-center justify-between pb-3 border-b border-teal-100 dark:border-teal-500/20">
        <div>
          <h2 className="text-sm font-extrabold text-slate-900 dark:text-white tracking-tight">
            {title}
          </h2>
          {subtitle && (
            <p className="text-[11px] text-slate-500 dark:text-teal-200/70 font-medium mt-0.5">
              {subtitle}
            </p>
          )}
        </div>
        {badge}
      </div>

      <div className="relative z-10 mt-5 h-64 w-full">{children}</div>
    </section>
  );
}

// =============================================================================
// SUBCOMPONENT: DUAL-MODE FROSTED TOOLTIP
// =============================================================================
function CustomChartTooltip({ active, payload, label, unit = "" }: any) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div className="rounded-xl border border-teal-200/80 dark:border-teal-500/30 bg-white/95 dark:bg-[#041d21]/95 p-3 shadow-lg backdrop-blur-md text-xs">
      <p className="font-semibold text-slate-500 dark:text-teal-200/80 mb-1">{label}</p>
      {payload.map((entry: any, index: number) => (
        <div
          key={index}
          className="flex items-center gap-2 font-mono font-bold text-slate-900 dark:text-white"
        >
          <span
            className="w-2 h-2 rounded-full"
            style={{ backgroundColor: entry.color || entry.fill }}
          />
          <span>{entry.name || "Value"}:</span>
          <span>
            {entry.value} {unit}
          </span>
        </div>
      ))}
    </div>
  );
}

// =============================================================================
// MAIN ADMIN DASHBOARD COMPONENT (OPTION 2: AURORA GLASS)
// =============================================================================
export default function AdminDashboard() {
  const { token } = useAuth();
  const { audience } = useAudience();
  const [data, setData] = useState<DashboardData>(emptyData);

  useEffect(() => {
    if (!token) return;
    const qs = audience && audience !== "overview" ? `?audience=${audience}` : "";
    api.adminGet<DashboardData>(token, `/dashboard${qs}`).then(setData).catch(() => {});
  }, [token, audience]);

  // Query Trend with Formatted Date Labels
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

  const totalQueriesInTrend = useMemo(
    () => (data.query_trend ?? []).reduce((acc, curr) => acc + (curr.queries || 0), 0),
    [data.query_trend]
  );

  // Latency breakdown natively in integer milliseconds (fixes fractional seconds bug)
  const latency = useMemo(
    () => (data.latency_breakdown ?? []).map((d) => ({ stage: d.stage, ms: Math.round(d.ms) })),
    [data.latency_breakdown]
  );

  // User Engagement Data for Donut Chart
  const totalUsersCount = Math.max(data.users, data.active_users, 1);
  const activeUserCount = data.active_users || 0;
  const otherUserCount = Math.max(totalUsersCount - activeUserCount, 0);

  const engagementPie = useMemo(
    () => [
      { name: "Active Users", value: activeUserCount },
      { name: "Other Users", value: otherUserCount },
    ],
    [activeUserCount, otherUserCount]
  );

  const activePct = useMemo(
    () => ((activeUserCount / totalUsersCount) * 100).toFixed(1),
    [activeUserCount, totalUsersCount]
  );

  // RAG Pipeline Total ms for percentage distribution
  const totalRagMs = useMemo(() => {
    const emb = data.pipeline?.embedding_ms || 0;
    const ret = data.pipeline?.retrieval_ms || 0;
    const rer = data.pipeline?.reranker_ms || 0;
    const llm = data.pipeline?.llm_ms || 0;
    return Math.max(emb + ret + rer + llm, 1);
  }, [data.pipeline]);

  return (
    <div className="min-h-screen bg-[#f4fbfb] dark:bg-dark-bg text-slate-900 dark:text-slate-100">
      {/* Standardized Admin Header with Audience Scope Tabs & Theme Toggle */}
      <DashboardHeader
        title="Dashboard"
        subtitle="Monitor users, conversations, RAG performance and governance."
        icon={LayoutDashboard}
      />

      <div className="p-6 sm:p-8 space-y-6 max-w-[1600px] mx-auto w-full">
        {/* ========================================================================= */}
        {/* 1. TOP 8 AURORA KPI METRIC TILES */}
        {/* ========================================================================= */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <AuroraMetricCard
            title="Total Users"
            value={data.users.toLocaleString()}
            icon={Users}
            accent="teal"
          />
          <AuroraMetricCard
            title="Active Users"
            value={data.active_users.toLocaleString()}
            subtitle="Last 24h"
            icon={UserCheck}
            accent="emerald"
            isLive={true}
          />
          <AuroraMetricCard
            title="Sessions"
            value={data.sessions.toLocaleString()}
            icon={Layers}
            accent="cyan"
          />
          <AuroraMetricCard
            title="Conversations"
            value={data.conversations.toLocaleString()}
            icon={MessagesSquare}
            accent="teal"
          />
          <AuroraMetricCard
            title="Queries"
            value={data.queries.toLocaleString()}
            icon={Search}
            accent="violet"
          />
          <AuroraMetricCard
            title="Feedback"
            value={data.feedback.toLocaleString()}
            icon={ThumbsUp}
            accent="rose"
          />
          <AuroraMetricCard
            title="Average Latency"
            value={formatLatencyMs(data.avg_latency_ms)}
            icon={Gauge}
            accent="amber"
          />
          <AuroraMetricCard
            title="Error Rate"
            value={`${data.error_rate.toFixed(2)}%`}
            subtitle={data.error_rate === 0 ? "Operational" : "Attention"}
            icon={AlertTriangle}
            accent={data.error_rate > 1 ? "rose" : "emerald"}
          />
        </div>

        {/* ========================================================================= */}
        {/* 2. MIDDLE ROW: QUERY VOLUME & USER ENGAGEMENT DONUT */}
        {/* ========================================================================= */}
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          {/* Query Volume Area Chart (xl:col-span-2) */}
          <div className="xl:col-span-2">
            <AuroraChartCard
              title="Query Volume"
              subtitle="User queries over the last 7 days"
              badge={
                <span className="font-mono font-bold text-xs bg-teal-50/70 dark:bg-teal-500/15 text-teal-800 dark:text-teal-200 px-2.5 py-1 rounded-lg border border-teal-200/80 dark:border-teal-500/30 shadow-2xs">
                  {totalQueriesInTrend.toLocaleString()} queries in 7d
                </span>
              }
            >
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={trend}>
                  <defs>
                    <linearGradient id="auroraQueryFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.4} />
                      <stop offset="95%" stopColor="#068987" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    className="stroke-slate-100 dark:stroke-[#27272a]"
                    vertical={false}
                  />
                  <XAxis
                    dataKey="label"
                    tick={{ fontSize: 11, fill: "#64748b" }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis
                    allowDecimals={false}
                    tick={{ fontSize: 11, fill: "#64748b" }}
                    axisLine={false}
                    tickLine={false}
                    width={32}
                  />
                  <Tooltip content={<CustomChartTooltip unit="queries" />} />
                  <Area
                    type="monotone"
                    dataKey="queries"
                    stroke="#068987"
                    strokeWidth={2.5}
                    fill="url(#auroraQueryFill)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </AuroraChartCard>
          </div>

          {/* User Engagement Radial Donut Chart (xl:col-span-1) */}
          <AuroraChartCard
            title="User Engagement"
            subtitle="Active vs. total registered audience"
            badge={
              <span className="text-[10px] uppercase font-bold tracking-wider font-mono text-cyan-600 dark:text-cyan-400 bg-cyan-50 dark:bg-cyan-500/15 border border-cyan-200/80 dark:border-cyan-500/30 px-2 py-0.5 rounded-full">
                {activePct}% Active
              </span>
            }
          >
            <div className="relative h-44 w-full flex items-center justify-center">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={engagementPie}
                    dataKey="value"
                    nameKey="name"
                    innerRadius={58}
                    outerRadius={84}
                    paddingAngle={3}
                  >
                    <Cell fill="#06b6d4" />
                    <Cell fill="#cbd5e1" className="dark:fill-[#083038]" />
                  </Pie>
                  <Tooltip content={<CustomChartTooltip unit="users" />} />
                </PieChart>
              </ResponsiveContainer>

              {/* Central Radial Metric Badge (Mirrors Thinking Pipeline Concentric Center) */}
              <div className="absolute inset-0 flex flex-col items-center justify-center text-center pointer-events-none">
                <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 dark:text-teal-300/70">
                  Active
                </span>
                <span className="text-xl font-black font-mono text-slate-900 dark:text-white">
                  {activePct}%
                </span>
              </div>
            </div>

            {/* Legend Breakdown Pills */}
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
              <div className="flex items-center justify-between p-2 rounded-xl bg-teal-50/40 dark:bg-white/[0.04] border border-teal-200/70 dark:border-teal-500/25">
                <span className="flex items-center gap-1.5 text-slate-700 dark:text-slate-300 font-medium">
                  <span className="w-2 h-2 rounded-full bg-cyan-500 shadow-[0_0_6px_#06b6d4]" />
                  Active
                </span>
                <span className="font-mono font-bold text-slate-900 dark:text-white">
                  {activeUserCount}
                </span>
              </div>

              <div className="flex items-center justify-between p-2 rounded-xl bg-teal-50/40 dark:bg-white/[0.04] border border-teal-200/70 dark:border-teal-500/25">
                <span className="flex items-center gap-1.5 text-slate-500 dark:text-slate-400 font-medium">
                  <span className="w-2 h-2 rounded-full bg-slate-400 dark:bg-[#083038]" />
                  Other
                </span>
                <span className="font-mono font-bold text-slate-700 dark:text-slate-300">
                  {otherUserCount}
                </span>
              </div>
            </div>
          </AuroraChartCard>
        </div>

        {/* ========================================================================= */}
        {/* 3. BOTTOM ROW: PIPELINE LATENCY, RAG PIPELINE & GOVERNANCE */}
        {/* ========================================================================= */}
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          {/* Pipeline Stage Latency Bar Chart (xl:col-span-1) */}
          <AuroraChartCard
            title="Pipeline Latency"
            subtitle="Average ms per stage (last 24h)"
            badge={
              <span className="text-[10px] font-mono font-bold text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-500/15 border border-amber-200/80 dark:border-amber-500/30 px-2 py-0.5 rounded-full">
                Native MS
              </span>
            }
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={latency}>
                <CartesianGrid
                  strokeDasharray="3 3"
                  className="stroke-slate-100 dark:stroke-[#27272a]"
                  vertical={false}
                />
                <XAxis
                  dataKey="stage"
                  tick={{ fontSize: 11, fill: "#64748b" }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 11, fill: "#64748b" }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(val) => `${val}ms`}
                  width={42}
                />
                <Tooltip content={<CustomChartTooltip unit="ms" />} />
                <Bar dataKey="ms" radius={[6, 6, 0, 0]}>
                  {latency.map((_, idx) => (
                    <Cell key={idx} fill={STAGE_BAR_COLORS[idx % STAGE_BAR_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </AuroraChartCard>

          {/* RAG Pipeline Execution Health Visualizer (xl:col-span-1) */}
          <section className="relative rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-white dark:bg-[#0c0d10] p-6 shadow-card dark:shadow-xl transition-all overflow-hidden flex flex-col justify-between hover:border-teal-400 dark:hover:border-teal-400/60">
            <div className="absolute -top-12 -right-12 w-36 h-36 bg-teal-400/10 dark:bg-teal-500/15 rounded-full blur-2xl pointer-events-none" />

            <div className="relative z-10">
              <div className="flex items-center justify-between pb-3 border-b border-teal-100 dark:border-teal-500/20">
                <div>
                  <h2 className="text-sm font-extrabold text-slate-900 dark:text-white tracking-tight flex items-center gap-2">
                    <Database className="w-4 h-4 text-teal-600 dark:text-teal-400" />
                    <span>RAG Pipeline Execution</span>
                  </h2>
                  <p className="text-[11px] text-slate-500 dark:text-teal-200/70 font-medium mt-0.5">
                    Latency & health across retrieval stages
                  </p>
                </div>
                <span className="text-[9px] uppercase tracking-wider font-mono font-bold px-2 py-0.5 rounded-full bg-teal-50 text-teal-700 border border-teal-200/80 dark:bg-teal-500/20 dark:text-teal-300 dark:border-teal-500/30">
                  Online
                </span>
              </div>

              {/* Stage Micro-Cards */}
              <div className="mt-4 space-y-2.5 text-xs">
                {/* Stage 1: Embedding */}
                <div className="flex items-center justify-between p-2.5 rounded-xl bg-teal-50/30 dark:bg-white/[0.03] border border-teal-100/90 dark:border-teal-500/20 hover:border-teal-300/80 dark:hover:border-teal-400/50 transition">
                  <span className="flex items-center gap-2 font-medium text-slate-800 dark:text-slate-200">
                    <span className="w-2 h-2 rounded-full bg-cyan-500 shadow-[0_0_6px_#06b6d4]" />
                    <span>Embedding</span>
                  </span>
                  <span className="font-mono font-bold text-slate-900 dark:text-white">
                    {data.pipeline?.embedding_ms != null
                      ? `${formatLatencyMs(data.pipeline.embedding_ms)} avg`
                      : "No data yet"}
                  </span>
                </div>

                {/* Stage 2: Retrieval */}
                <div className="flex items-center justify-between p-2.5 rounded-xl bg-teal-50/30 dark:bg-white/[0.03] border border-teal-100/90 dark:border-teal-500/20 hover:border-teal-300/80 dark:hover:border-teal-400/50 transition">
                  <span className="flex items-center gap-2 font-medium text-slate-800 dark:text-slate-200">
                    <span className="w-2 h-2 rounded-full bg-teal-500 shadow-[0_0_6px_#0d9488]" />
                    <span>Retrieval</span>
                  </span>
                  <span className="font-mono font-bold text-slate-900 dark:text-white">
                    {data.pipeline?.retrieval_ms != null
                      ? `${formatLatencyMs(data.pipeline.retrieval_ms)} avg`
                      : "0.00 s avg (24h)"}
                  </span>
                </div>

                {/* Stage 3: Reranker */}
                <div className="flex items-center justify-between p-2.5 rounded-xl bg-teal-50/30 dark:bg-white/[0.03] border border-teal-100/90 dark:border-teal-500/20 hover:border-teal-300/80 dark:hover:border-teal-400/50 transition">
                  <span className="flex items-center gap-2 font-medium text-slate-800 dark:text-slate-200">
                    <span className="w-2 h-2 rounded-full bg-violet-500 shadow-[0_0_6px_#8b5cf6]" />
                    <span>Reranker</span>
                  </span>
                  <span className="inline-flex items-center gap-1.5 font-mono text-[11px] font-semibold text-slate-900 dark:text-white">
                    <span
                      className={`px-1.5 py-0.5 rounded text-[10px] ${
                        data.pipeline?.reranker_enabled
                          ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300"
                          : "bg-slate-100 text-slate-600 dark:bg-white/10 dark:text-slate-400"
                      }`}
                    >
                      {data.pipeline?.reranker_enabled ? "Enabled" : "Disabled"}
                    </span>
                    {data.pipeline?.reranker_ms != null && (
                      <span>· {formatLatencyMs(data.pipeline.reranker_ms)}</span>
                    )}
                  </span>
                </div>

                {/* Stage 4: LLM Generation */}
                <div className="flex items-center justify-between p-2.5 rounded-xl bg-teal-50/30 dark:bg-white/[0.03] border border-teal-100/90 dark:border-teal-500/20 hover:border-teal-300/80 dark:hover:border-teal-400/50 transition">
                  <span className="flex items-center gap-2 font-medium text-slate-800 dark:text-slate-200">
                    <span className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_6px_#10b981]" />
                    <span>LLM Synthesis</span>
                  </span>
                  <span className="font-mono font-bold text-slate-900 dark:text-white">
                    {data.pipeline?.llm_ms != null
                      ? `${formatLatencyMs(data.pipeline.llm_ms)} avg`
                      : "0.00 s avg (24h)"}
                  </span>
                </div>
              </div>
            </div>

            {/* Proportional Stage Latency Distribution Mini-Bar */}
            <div className="relative z-10 mt-4 pt-3 border-t border-teal-100 dark:border-teal-500/20">
              <div className="flex items-center justify-between text-[10px] text-slate-500 dark:text-slate-400 font-mono mb-1.5">
                <span>Stage Latency Share</span>
                <span>100% Pipeline</span>
              </div>
              <div className="h-2 w-full bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden flex border border-black/5 dark:border-white/5">
                <div
                  style={{
                    width: `${((data.pipeline?.embedding_ms || 0) / totalRagMs) * 100}%`,
                  }}
                  className="bg-cyan-500 h-full"
                  title="Embedding"
                />
                <div
                  style={{
                    width: `${((data.pipeline?.retrieval_ms || 0) / totalRagMs) * 100}%`,
                  }}
                  className="bg-teal-500 h-full"
                  title="Retrieval"
                />
                <div
                  style={{
                    width: `${((data.pipeline?.reranker_ms || 0) / totalRagMs) * 100}%`,
                  }}
                  className="bg-violet-500 h-full"
                  title="Reranker"
                />
                <div
                  style={{
                    width: `${((data.pipeline?.llm_ms || 0) / totalRagMs) * 100}%`,
                  }}
                  className="bg-emerald-500 h-full"
                  title="LLM"
                />
              </div>
            </div>
          </section>

          {/* AI Governance & Security Cockpit (xl:col-span-1) */}
          <section className="relative rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-white dark:bg-[#0c0d10] p-6 shadow-card dark:shadow-xl transition-all overflow-hidden flex flex-col justify-between hover:border-teal-400 dark:hover:border-teal-400/60">
            <div className="absolute -top-12 -right-12 w-36 h-36 bg-emerald-400/10 dark:bg-emerald-500/15 rounded-full blur-2xl pointer-events-none" />

            <div className="relative z-10">
              <div className="flex items-center justify-between pb-3 border-b border-teal-100 dark:border-teal-500/20">
                <div>
                  <h2 className="text-sm font-extrabold text-slate-900 dark:text-white tracking-tight flex items-center gap-2">
                    <ShieldCheck className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                    <span>AI Governance & Security</span>
                  </h2>
                  <p className="text-[11px] text-slate-500 dark:text-teal-200/70 font-medium mt-0.5">
                    Real-time compliance telemetry & audit trails
                  </p>
                </div>
                <span className="text-[9px] uppercase tracking-wider font-mono font-bold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200/80 dark:bg-emerald-500/20 dark:text-emerald-300 dark:border-emerald-500/30">
                  Protected
                </span>
              </div>

              {/* Actionable Governance Micro-Cards */}
              <div className="mt-4 space-y-2.5 text-xs">
                {/* 1. Monitoring */}
                <Link
                  href="/admin/governance"
                  className="flex items-center justify-between p-2.5 rounded-xl bg-teal-50/30 dark:bg-white/[0.03] border border-teal-100/90 dark:border-teal-500/20 hover:border-emerald-300/80 dark:hover:border-emerald-500/40 hover:bg-teal-50/50 transition group"
                >
                  <span className="flex items-center gap-2 font-medium text-slate-800 dark:text-slate-200">
                    <span className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_6px_#10b981]" />
                    <span>Monitoring Status</span>
                  </span>
                  <span className="flex items-center gap-1 font-mono font-bold text-slate-900 dark:text-white">
                    <span>{data.governance?.monitoring ?? "1,017 requests"}</span>
                    <ArrowUpRight className="w-3.5 h-3.5 text-slate-400 group-hover:text-emerald-600 transition" />
                  </span>
                </Link>

                {/* 2. Tracing */}
                <Link
                  href="/admin/governance"
                  className="flex items-center justify-between p-2.5 rounded-xl bg-teal-50/30 dark:bg-white/[0.03] border border-teal-100/90 dark:border-teal-500/20 hover:border-cyan-300/80 dark:hover:border-cyan-500/40 hover:bg-teal-50/50 transition group"
                >
                  <span className="flex items-center gap-2 font-medium text-slate-800 dark:text-slate-200">
                    <span className="w-2 h-2 rounded-full bg-cyan-500 shadow-[0_0_6px_#06b6d4]" />
                    <span>Distributed Tracing</span>
                  </span>
                  <span className="flex items-center gap-1 font-mono font-bold text-slate-900 dark:text-white">
                    <span>{data.governance?.tracing ?? "48 traces (24h)"}</span>
                    <ArrowUpRight className="w-3.5 h-3.5 text-slate-400 group-hover:text-cyan-600 transition" />
                  </span>
                </Link>

                {/* 3. Audit Logs */}
                <Link
                  href="/admin/governance"
                  className="flex items-center justify-between p-2.5 rounded-xl bg-teal-50/30 dark:bg-white/[0.03] border border-teal-100/90 dark:border-teal-500/20 hover:border-teal-300/80 dark:hover:border-teal-500/40 hover:bg-teal-50/50 transition group"
                >
                  <span className="flex items-center gap-2 font-medium text-slate-800 dark:text-slate-200">
                    <span className="w-2 h-2 rounded-full bg-teal-500 shadow-[0_0_6px_#0d9488]" />
                    <span>Audit Trail Logs</span>
                  </span>
                  <span className="flex items-center gap-1 font-mono font-bold text-slate-900 dark:text-white">
                    <span>{data.governance?.audit_logs ?? "1,440 entries"}</span>
                    <ArrowUpRight className="w-3.5 h-3.5 text-slate-400 group-hover:text-teal-600 transition" />
                  </span>
                </Link>

                {/* 4. Guardrail Flags */}
                <div className="flex items-center justify-between p-2.5 rounded-xl bg-teal-50/30 dark:bg-white/[0.03] border border-teal-100/90 dark:border-teal-500/20">
                  <span className="flex items-center gap-2 font-medium text-slate-800 dark:text-slate-200">
                    <span className="w-2 h-2 rounded-full bg-amber-500 shadow-[0_0_6px_#f59e0b]" />
                    <span>Guardrail Flags</span>
                  </span>
                  <span className="font-mono font-bold text-slate-900 dark:text-white">
                    {data.governance?.guardrails ?? "132 flagged (24h)"}
                  </span>
                </div>
              </div>
            </div>

            {/* Quick Access Action */}
            <div className="relative z-10 mt-4 pt-3 border-t border-teal-100 dark:border-teal-500/20 flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400">
              <span>Audience Scope: {audience}</span>
              <Link
                href="/admin/governance"
                className="font-bold text-teal-600 dark:text-teal-400 hover:underline flex items-center gap-1"
              >
                <span>Full Governance Console</span>
                <ArrowUpRight className="w-3 h-3" />
              </Link>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
