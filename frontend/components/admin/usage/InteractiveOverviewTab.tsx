"use client";

import React, { useState } from "react";
import {
  overviewFeedParams,
  prefetchQueryDetail,
  queryErrorMessage,
  useUsageList,
  usePrefetchQueryDetails,
  usePreloadQueryInspector,
} from "@/lib/use-usage-data";
import type { UsageSummary, UsageQueryRow, Audience, PageEnvelope } from "@/lib/types";
import { useRowIntentHandlers } from "@/lib/query-cache";
import dynamic from "next/dynamic";
import {
  DollarSign,
  Cpu,
  Layers,
  Sparkles,
  Zap,
  Clock,
  ShieldCheck,
  Activity,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  HelpCircle,
  ArrowRight,
  RefreshCw,
  Search,
  Eye,
  SlidersHorizontal,
  ChevronDown,
  ExternalLink,
} from "lucide-react";
import { useChartTheme, useResolvedThemeClass } from "@/lib/use-chart-theme";
import { usePrefersReducedMotion } from "@/lib/motion";
import { Collapse } from "@/components/ui/Collapse";
import { SkeletonCard, SkeletonGroup, Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { studioTile } from "@/components/ui/styles";

/** Milliseconds → seconds for display (e.g. 1116 → "1.12"). */
const toSeconds = (ms: number | null | undefined): string =>
  typeof ms === "number" && Number.isFinite(ms) ? (ms / 1000).toFixed(2) : "—";
import {
  OutcomeBadge,
  formatTokens,
  formatCurrency,
} from "../TableFormatters";

const QueryInspectorDrawer = dynamic(
  () => import("./QueryInspectorDrawer").then((mod) => mod.QueryInspectorDrawer),
  { ssr: false }
);

interface InteractiveOverviewTabProps {
  summary: UsageSummary | null;
  isLoading: boolean;
  error?: string | null;
  onRetry?: () => void;
  token?: string;
  audience?: string;
  /** Global date range, inclusive UTC days (YYYY-MM-DD); "" = all time. */
  fromDay?: string;
  toDay?: string;
}

export function InteractiveOverviewTab({
  summary,
  isLoading,
  error,
  onRetry,
  token = "",
  audience = "overview",
  fromDay = "",
  toDay = "",
}: InteractiveOverviewTabProps) {
  // Token pipeline colours (prompt, reasoning, output) from the chart theme.
  const ct = useChartTheme();
  const isDark = useResolvedThemeClass() === "dark";
  // Dark keeps the original glowing teal trio. Light uses three clearly distinct
  // palette colours (teal / amber / indigo) without glow, so the rings and the
  // Prompt / Reasoning / Output rows don't blur into one dark teal.
  const [promptColor, reasoningColor, outputColor] = isDark
    ? [ct.categorical[0], ct.categorical[1], ct.status.ok]
    : [ct.categorical[0], ct.categorical[2], ct.categorical[5]];
  const dot = (color: string, blur = 6) =>
    isDark ? { backgroundColor: color, boxShadow: `0 0 ${blur}px ${color}` } : { backgroundColor: color };
  const glow = (color: string) => (isDark ? { filter: `drop-shadow(0 0 4px ${color})` } : {});
  // Navigation & Drilldown State
  const [activeDomainFilter, setActiveDomainFilter] = useState<"all" | "costs" | "tokens" | "quality">("all");
  const reducedMotion = usePrefersReducedMotion();
  /** Telemetry Focus: highlight one card, dim the others and bring it into view. */
  const focusDomain = (id: "all" | "costs" | "tokens" | "quality") => {
    setActiveDomainFilter(id);
    if (id !== "all") {
      document
        .getElementById(`telemetry-${id}`)
        ?.scrollIntoView?.({ behavior: reducedMotion ? "auto" : "smooth", block: "nearest" });
    }
  };
  const dimUnless = (id: "costs" | "tokens" | "quality") =>
    activeDomainFilter !== "all" && activeDomainFilter !== id ? "opacity-60" : "";
  const [selectedOutcome, setSelectedOutcome] = useState<string>("all");
  const [selectedStageDetail, setSelectedStageDetail] = useState<"llm" | "embed" | "savings" | null>(null);
  const [tokenViewMode, setTokenViewMode] = useState<"absolute" | "percentage">("absolute");
  const [inspectRequestId, setInspectRequestId] = useState<string | null>(null);

  // Live query feed: cached per filter set (the backend serves the default
  // feed from a snapshot), previous rows stay while another filter loads.
  const feed = useUsageList<PageEnvelope<UsageQueryRow>>(
    "queries",
    token || null,
    overviewFeedParams(audience as Audience, { from: fromDay, to: toDay }, selectedOutcome),
  );
  const realQueries = feed.data?.items ?? [];
  // The feed's inspectors (one request) and the drawer's code, while idle.
  usePrefetchQueryDetails(
    token,
    feed.data && !feed.isPlaceholderData ? realQueries.map((q) => q.request_id) : null,
    feed.updatedAt,
  );
  usePreloadQueryInspector();
  const feedRowIntent = useRowIntentHandlers((q: UsageQueryRow) => prefetchQueryDetail(token, q.request_id));
  const loadingQueries = feed.isLoading;
  const queriesError = queryErrorMessage(feed.error, "Failed to load real chatbot query feed.");

  if (isLoading && !summary) {
    return (
      <SkeletonGroup label="Loading live telemetry" className="space-y-6">
        <Skeleton className="h-28 w-full rounded-2xl" />
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <SkeletonCard className="h-80" />
          <SkeletonCard className="h-80" />
          <SkeletonCard className="h-80" />
        </div>
      </SkeletonGroup>
    );
  }

  if (error || !summary) {
    return (
      <ErrorState
        className="rounded-2xl border border-status-danger/30 bg-status-danger-bg/60"
        title="Failed to load Live Telemetry Studio"
        message={error || "No telemetry found for the selected audience scope."}
        onRetry={onRetry}
      />
    );
  }

  const { financial, tokens, pricing, accounting, quality, as_of } = summary;

  // Real Stage Token Calculations
  const promptTokens = tokens.llm_tokens.prompt_context_tokens;
  const reasoningTokens = tokens.llm_tokens.reasoning_tokens;
  const outputTokens = tokens.llm_tokens.visible_output_tokens;
  const embedTokens = tokens.embeddings_tokens.total_embeddings_tokens;
  const totalTokens = Math.max(tokens.total_billed_tokens, 1);

  const promptPct = ((promptTokens / totalTokens) * 100).toFixed(1);
  const reasoningPct = ((reasoningTokens / totalTokens) * 100).toFixed(1);
  const outputPct = ((outputTokens / totalTokens) * 100).toFixed(1);
  const embedPct = ((embedTokens / totalTokens) * 100).toFixed(1);

  // Speedometer geometry
  // Real percentiles only; the needle rests at 0 when the server reports none.
  const p50Ms = quality.latency?.p50_ms ?? null;
  const p95Ms = quality.latency?.p95_ms ?? null;
  const p50 = p50Ms ?? 0;
  const p95 = p95Ms ?? 0;
  const maxScaleLatency = 4000;
  const p50Angle = Math.min(Math.max((p50 / maxScaleLatency) * 180 - 90, -90), 90);
  const p95Angle = Math.min(Math.max((p95 / maxScaleLatency) * 180 - 90, -90), 90);

  // Groundedness percentage
  const groundedPassPct = Number(quality.groundedness_pass_rate ?? 0);
  const groundedCircumference = 2 * Math.PI * 40;
  const groundedStrokeDash = (groundedPassPct / 100) * groundedCircumference;

  const outcomes = quality.outcomes || {};

  return (
    <div className="space-y-8 select-none">
      {/* ========================================================================= */}
      {/* 1. CHATBOT LIVE TELEMETRY COMMAND BAR (Signature Command Center) */}
      {/* ========================================================================= */}
      <div data-tour="usage-command-bar" className="relative overflow-hidden rounded-2xl border border-teal-500/30 bg-gradient-to-r from-brand-600 via-brand-800 to-brand-950 p-6 text-white shadow-xl transition-all">
        {/* Ambient Glow Orbs */}
        <div className="absolute right-0 top-0 -mt-12 -mr-12 h-64 w-64 rounded-full bg-teal-400/20 blur-3xl pointer-events-none" />
        <div className="absolute left-1/4 bottom-0 -mb-12 h-40 w-40 rounded-full bg-cyan-400/20 blur-2xl pointer-events-none" />

        <div className="relative z-10 flex flex-col gap-5">
          {/* Top Title & Operational Status Row */}
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 border-b border-white/15 pb-4">
            <div className="space-y-1">
              <div className="flex items-center gap-2.5">
                <span className="relative flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-400" />
                </span>
                <h3 className="text-sm font-extrabold tracking-wider uppercase text-white flex items-center gap-2">
                  <span>Chatbot Live Telemetry Command Center</span>
                  <span className="rounded-full bg-emerald-400/20 text-emerald-300 border border-emerald-400/30 px-2 py-0.5 text-[10px] font-semibold">
                    Live Production Stream
                  </span>
                </h3>
              </div>
              <p className="text-xs text-teal-100/90">
                Audience: <strong className="text-white capitalize">{audience}</strong> • Synchronized across real multi-turn dialogues, reasoning chains, and ground-truth citations.
              </p>
            </div>

            {/* Sync Status & Refresh */}
            <div className="flex items-center gap-3">
              <div className="text-[11px] text-teal-200 hidden sm:block">
                <span>Last Telemetry Sync: </span>
                <span className="font-mono font-bold text-white">
                  {as_of ? new Date(as_of).toLocaleTimeString() : "Live"}
                </span>
              </div>
              {onRetry && (
                <button
                  type="button"
                  onClick={() => {
                    onRetry();
                    void feed.refetch();
                  }}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-white/15 px-3 py-1.5 text-xs font-semibold text-white border border-white/20 hover:bg-white/25 transition shadow-sm"
                  title="Synchronize live backend telemetry"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  <span>Sync Telemetry</span>
                </button>
              )}
            </div>
          </div>

          {/* Core Chatbot Health Metrics Strip (Frosted Translucent Tiles - NOT White) */}
          <div data-tour="usage-kpis" className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            {/* Metric 1: Total Turns Processed */}
            <div className="bg-white/10 backdrop-blur-md rounded-xl p-3 border border-white/15 flex flex-col justify-between shadow-xs">
              <span className="text-[11px] text-teal-200 font-medium">Conversational Turns</span>
              <div className="flex items-baseline gap-1 mt-1">
                <span className="text-xl font-extrabold font-mono text-white">
                  {accounting.total_turns.toLocaleString()}
                </span>
                <span className="text-[10px] text-teal-200">total turns</span>
              </div>
            </div>

            {/* Metric 2: Groundedness Pass Rate */}
            <div className="bg-white/10 backdrop-blur-md rounded-xl p-3 border border-white/15 flex flex-col justify-between shadow-xs">
              <span className="text-[11px] text-teal-200 font-medium">Grounded Citations</span>
              <div className="flex items-baseline gap-1 mt-1">
                <span className="text-xl font-extrabold font-mono text-emerald-400 drop-shadow-[0_0_8px_rgba(52,211,153,0.4)]">
                  {groundedPassPct.toFixed(1)}%
                </span>
                <span className="text-[10px] text-emerald-200">verified</span>
              </div>
            </div>

            {/* Metric 3: P50 Median Latency */}
            <div data-tour="usage-latency-p50" className="bg-white/10 backdrop-blur-md rounded-xl p-3 border border-white/15 flex flex-col justify-between shadow-xs">
              <span className="text-[11px] text-teal-200 font-medium">Median Turn Speed (P50)</span>
              <div className="flex items-baseline gap-1 mt-1">
                <span className="text-xl font-extrabold font-mono text-cyan-300 drop-shadow-[0_0_8px_rgba(103,232,249,0.4)]">
                  {toSeconds(p50Ms)}
                </span>
                <span className="text-[10px] text-cyan-100">s / query</span>
              </div>
            </div>

            {/* Metric 4: Total Platform Net Cost */}
            <div data-tour="usage-net-spend" className="bg-white/10 backdrop-blur-md rounded-xl p-3 border border-white/15 flex flex-col justify-between shadow-xs">
              <span className="text-[11px] text-teal-200 font-medium">Net Platform Spend</span>
              <div className="flex items-baseline gap-1 mt-1">
                <span className="text-xl font-extrabold font-mono text-white">
                  ${financial.total_cost_usd.toFixed(4)}
                </span>
                <span className="text-[10px] text-teal-200">USD</span>
              </div>
            </div>

            {/* Metric 5: Average Cost / Turn */}
            <div data-tour="usage-cost-per-turn" className="bg-white/10 backdrop-blur-md rounded-xl p-3 border border-white/15 flex flex-col justify-between col-span-2 sm:col-span-1 shadow-xs">
              <span className="text-[11px] text-teal-200 font-medium">Avg Cost / Turn</span>
              <div className="flex items-baseline gap-1 mt-1">
                <span className="text-xl font-extrabold font-mono text-amber-300 drop-shadow-[0_0_8px_rgba(252,211,77,0.4)]">
                  ${financial.avg_cost_per_query_usd.toFixed(4)}
                </span>
                <span className="text-[10px] text-amber-100">per answer</span>
              </div>
            </div>
          </div>

          {/* Quick Domain Filter Selector */}
          <div data-tour="usage-focus" className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-white/15">
            <span className="text-xs text-teal-200 font-medium flex items-center gap-1.5">
              <SlidersHorizontal className="w-3.5 h-3.5" />
              <span>Telemetry Focus:</span>
            </span>

            <div className="flex items-center gap-1.5 flex-wrap">
              {[
                { id: "all", label: "Full Studio View" },
                { id: "costs", label: "Economic Waterfall" },
                { id: "tokens", label: "Cognitive Thinking Pipeline" },
                { id: "quality", label: "Performance Cockpit" },
              ].map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  aria-pressed={activeDomainFilter === tab.id}
                  onClick={() => focusDomain(tab.id as "all" | "costs" | "tokens" | "quality")}
                  className={`px-3 py-1 text-xs rounded-lg font-medium transition ${
                    activeDomainFilter === tab.id
                      ? "bg-brand-50 text-brand-950 font-bold shadow-sm"
                      : "text-teal-200 hover:text-white hover:bg-white/10"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* ========================================================================= */}
      {/* 2. THREE DYNAMIC TELEMETRY VISUALIZERS (GRID LAYOUT) */}
      {/* ========================================================================= */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

        {/* ----------------------------------------------------------------------- */}
        {/* DOMAIN 1: FINANCIAL COST ECONOMICS — LIVE FINANCIAL ACCOUNTING */}
        {/* ----------------------------------------------------------------------- */}
        <div data-tour="usage-waterfall"
          id="telemetry-costs"
          className={`${dimUnless("costs")} relative rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-white p-6 text-slate-900 shadow-card dark:bg-surface-card dark:text-white dark:shadow-xl flex flex-col justify-between transition-all overflow-hidden hover:border-teal-400 dark:hover:border-teal-400/60 ${
            activeDomainFilter === "costs"
              ? "ring-2 ring-teal-500 border-teal-500 dark:ring-teal-400/80 dark:border-teal-400/60"
              : ""
          }`}
        >
          {/* Ambient Corner Glow in top right */}
          <div className="absolute -top-12 -right-12 w-36 h-36 bg-emerald-400/10 dark:bg-emerald-500/15 rounded-full blur-2xl pointer-events-none" />

          <div className="relative z-10">
            {/* Header with Production Aurora Glass Branding */}
            <div className="flex items-center justify-between pb-3 border-b border-teal-100 dark:border-teal-500/20">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-xl bg-emerald-50 text-emerald-700 border border-emerald-200/80 dark:bg-emerald-500/15 dark:border-emerald-500/30 dark:text-emerald-300 flex items-center justify-center shadow-xs">
                  <DollarSign className="w-5 h-5" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h4 className="text-sm font-extrabold text-slate-900 dark:text-white tracking-tight">Economic Waterfall</h4>
                    <span className="text-[9px] uppercase tracking-wider font-mono font-bold px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200/80 dark:bg-emerald-500/20 dark:text-emerald-300 dark:border-emerald-500/30 shadow-xs">
                      Live Accounting
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-500 dark:text-teal-200/70 font-medium">Live Capital Outflow (Click to inspect)</p>
                </div>
              </div>
              <div className="text-right">
                <div className="text-base font-black font-mono text-slate-900 dark:text-white tracking-tight">
                  ${financial.total_cost_usd.toFixed(4)}
                </div>
                <div className="text-[10px] text-slate-500 dark:text-slate-400 font-medium">Total Actual Net</div>
              </div>
            </div>

            {/* Clickable Channel 1: LLM Generation (Aurora Glass Precision) */}
            <div className="mt-4 space-y-3">
              <button
                type="button"
                onClick={() => setSelectedStageDetail(selectedStageDetail === "llm" ? null : "llm")}
                className={`w-full text-left p-3 rounded-xl border transition ${
                  selectedStageDetail === "llm"
                    ? "border-teal-500 bg-teal-50/70 ring-1 ring-teal-500 dark:border-teal-400 dark:bg-teal-500/20 dark:ring-teal-400/40 shadow-inner"
                    : "border-teal-100/90 bg-teal-50/30 hover:bg-teal-50/60 hover:border-teal-300/80 dark:border-teal-500/20 dark:bg-white/[0.03] dark:hover:bg-white/[0.06] dark:hover:border-teal-400/50"
                }`}
              >
                <div className="flex items-center justify-between text-xs">
                  <div className="flex items-center gap-2 font-bold text-slate-900 dark:text-slate-200">
                    <span className="w-2 h-2 rounded-full animate-pulse" style={dot(promptColor)} />
                    <span>LLM Generation Pipeline</span>
                  </div>
                  <span className="font-mono font-black text-slate-900 dark:text-white">
                    ${financial.llm_cost_usd.toFixed(4)}
                  </span>
                </div>

                {/* Aurora Multi-stage Jewel Gradient Bar */}
                <div className="mt-2.5 h-1.5 w-full bg-chart-track rounded-full overflow-hidden flex border border-black/5 dark:border-white/5">
                  <div style={{ width: `${promptPct}%`, backgroundColor: promptColor }} className="h-full" title={`Prompt: ${promptPct}%`} />
                  <div style={{ width: `${reasoningPct}%`, backgroundColor: reasoningColor }} className="h-full" title={`Reasoning: ${reasoningPct}%`} />
                  <div style={{ width: `${outputPct}%`, backgroundColor: outputColor }} className="h-full" title={`Output: ${outputPct}%`} />
                </div>
                <div className="mt-1.5 flex justify-between text-[10px] text-slate-500 dark:text-slate-400 font-mono">
                  <span>Prompt: {promptPct}%</span>
                  <span>Thinking: {reasoningPct}%</span>
                  <span>Output: {outputPct}%</span>
                </div>
              </button>

              {/* Clickable Channel 2: Embedding & Guardrails */}
              <button
                type="button"
                onClick={() => setSelectedStageDetail(selectedStageDetail === "embed" ? null : "embed")}
                className={`w-full text-left p-3 rounded-xl border transition ${
                  selectedStageDetail === "embed"
                    ? "border-teal-500 bg-teal-50/70 ring-1 ring-teal-500 dark:border-teal-400 dark:bg-teal-500/20 dark:ring-teal-400/40 shadow-inner"
                    : "border-teal-100/90 bg-teal-50/30 hover:bg-teal-50/60 hover:border-teal-300/80 dark:border-teal-500/20 dark:bg-white/[0.03] dark:hover:bg-white/[0.06] dark:hover:border-teal-400/50"
                }`}
              >
                <div className="flex items-center justify-between text-xs">
                  <div className="flex items-center gap-2 font-bold text-slate-900 dark:text-slate-200">
                    <span className="w-2 h-2 rounded-full" style={dot(reasoningColor)} />
                    <span>Vector & Guardrails Embeddings</span>
                  </div>
                  <span className="font-mono font-black text-slate-900 dark:text-white">
                    ${financial.embeddings_cost_usd.toFixed(4)}
                  </span>
                </div>
                <div className="mt-1.5 text-[10px] text-slate-500 dark:text-slate-400 flex justify-between font-mono">
                  <span>Rate: ${pricing.price_embed_per_million.toFixed(3)} / 1M</span>
                  <span>{embedTokens.toLocaleString()} tokens</span>
                </div>
              </button>

              {/* Celebratory Cost Savings Highlight — Frosted Emerald Jewel Pill */}
              <button
                type="button"
                onClick={() => setSelectedStageDetail(selectedStageDetail === "savings" ? null : "savings")}
                className={`w-full text-left rounded-xl border p-3 flex items-center justify-between text-xs transition ${
                  selectedStageDetail === "savings"
                    ? "border-emerald-500 bg-emerald-50/80 ring-1 ring-emerald-500 text-emerald-950 dark:border-emerald-400 dark:bg-emerald-500/20 dark:text-white shadow-inner"
                    : "border-emerald-200/80 bg-emerald-50/40 hover:bg-emerald-50/70 text-emerald-900 hover:border-emerald-300 dark:border-emerald-500/30 dark:bg-emerald-950/20 dark:hover:bg-emerald-950/40 dark:text-emerald-200"
                }`}
              >
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400 flex-shrink-0" />
                  <div>
                    <span className="font-bold block text-emerald-950 dark:text-white">Prompt Cache Savings</span>
                    <span className="text-[10px] text-emerald-700/80 dark:text-emerald-300/80">Zero duplicate billing</span>
                  </div>
                </div>
                <span className="font-mono font-black text-emerald-800 bg-emerald-100/90 dark:text-emerald-200 dark:bg-emerald-500/30 px-2.5 py-1 rounded-md text-[11px] shadow-xs border border-emerald-200 dark:border-emerald-400/40">
                  ${financial.stage_costs.cached_savings_usd.toFixed(4)} saved
                </span>
              </button>
            </div>

            {/* Expandable Granular Breakdown when a channel is clicked */}
            <Collapse open={selectedStageDetail !== null}>
              <div className={`mt-3 p-3 text-xs text-fg ${studioTile}`}>
                <div className="flex items-center justify-between font-bold text-accent-fg mb-2">
                  <span>Stage Detail Breakdown</span>
                  <button
                    type="button"
                    onClick={() => setSelectedStageDetail(null)}
                    className="text-[11px] text-fg-subtle hover:text-fg transition"
                  >
                    Close
                  </button>
                </div>

                {selectedStageDetail === "llm" && (
                  <div className="space-y-1.5 text-[11px]">
                    <div className="flex justify-between">
                      <span className="text-fg-subtle">Prompt Context Cost:</span>
                      <span className="font-mono font-bold text-fg">${financial.stage_costs.prompt_uncached_cost.toFixed(5)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-fg-subtle">Reasoning Trace Cost:</span>
                      <span className="font-mono font-bold text-fg">${financial.stage_costs.reasoning_cost.toFixed(5)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-fg-subtle">Visible Output Cost:</span>
                      <span className="font-mono font-bold text-fg">${financial.stage_costs.visible_cost.toFixed(5)}</span>
                    </div>
                  </div>
                )}

                {selectedStageDetail === "embed" && (
                  <div className="space-y-1.5 text-[11px]">
                    <div className="flex justify-between">
                      <span className="text-fg-subtle">Query Embed Cost:</span>
                      <span className="font-mono font-bold text-fg">${financial.stage_costs.query_embed_cost.toFixed(5)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-fg-subtle">Guardrails Embed Cost:</span>
                      <span className="font-mono font-bold text-fg">${financial.stage_costs.guardrail_embed_cost.toFixed(5)}</span>
                    </div>
                  </div>
                )}

                {selectedStageDetail === "savings" && (
                  <div className="space-y-1 text-[11px] text-status-ok-fg">
                    <p>Cached prompt tokens are billed at 50% discount (${pricing.price_cached_per_million || 0.125}/1M vs ${pricing.price_prompt_per_million}/1M).</p>
                    <p className="font-bold text-fg">Total net saved: ${financial.stage_costs.cached_savings_usd.toFixed(4)}</p>
                  </div>
                )}
              </div>
            </Collapse>
          </div>

          <div className="relative z-10 mt-4 pt-3 border-t border-teal-100 dark:border-teal-500/20 flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400">
            <span>Avg / Query: ${(financial.avg_cost_per_query_usd).toFixed(5)}</span>
            <span className="text-emerald-700 dark:text-emerald-300 font-mono font-bold">100% Real Accounting</span>
          </div>
        </div>

        {/* ----------------------------------------------------------------------- */}
        {/* DOMAIN 2: TOKEN VOLUME TELEMETRY — COGNITIVE PIPELINE */}
        {/* ----------------------------------------------------------------------- */}
        <div data-tour="usage-token-rings"
          id="telemetry-tokens"
          className={`${dimUnless("tokens")} relative rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-surface-card p-6 text-slate-900 shadow-card dark:border-teal-500/25 dark:bg-gradient-to-br dark:from-brand-950/95 dark:via-surface-muted/98 dark:to-surface-page dark:text-white dark:shadow-2xl flex flex-col justify-between transition-all overflow-hidden hover:border-teal-400 dark:hover:border-teal-400/60 ${
            activeDomainFilter === "tokens"
              ? "ring-2 ring-teal-500 border-teal-500 dark:ring-teal-400/80 dark:border-teal-400/60"
              : "dark:shadow-[0_0_30px_rgba(20,184,166,0.12),inset_0_1px_0_0_rgba(45,212,191,0.25)]"
          }`}
        >
          {/* Luminous ambient teal glow in the corner */}
          <div className="absolute -top-12 -right-12 w-36 h-36 bg-teal-400/10 dark:bg-teal-500/15 rounded-full blur-2xl pointer-events-none" />

          <div className="relative z-10">
            {/* Header with Production Aurora Glass Branding */}
            <div className="flex items-center justify-between pb-3 border-b border-teal-100 dark:border-teal-500/20">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-xl bg-teal-50 border border-teal-200/80 text-teal-600 dark:bg-teal-500/15 dark:border-teal-500/30 dark:text-teal-300 shadow-xs flex items-center justify-center">
                  <Cpu className="w-5 h-5" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h4 className="text-sm font-extrabold text-slate-900 dark:text-white tracking-tight">The Thinking Pipeline</h4>
                    <span className="text-[9px] uppercase tracking-wider font-mono font-bold px-2 py-0.5 rounded bg-teal-50 text-teal-700 border border-teal-200/80 dark:bg-teal-500/20 dark:text-teal-300 dark:border-teal-500/30 shadow-xs">
                      Token Telemetry
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-500 dark:text-teal-200/70 font-medium">Token Volume Across Stages</p>
                </div>
              </div>

              {/* View Toggle (Frosted Glass Pill) */}
              <div className="flex bg-slate-100 dark:bg-surface-muted/80 p-0.5 rounded-lg text-[10px] border border-slate-200/80 dark:border-teal-500/25">
                <button
                  type="button"
                  onClick={() => setTokenViewMode("absolute")}
                  className={`px-2 py-1 rounded font-medium transition ${
                    tokenViewMode === "absolute"
                      ? "bg-teal-600 text-white font-bold shadow-xs dark:bg-teal-400 dark:text-teal-950 dark:shadow-[0_0_10px_rgba(45,212,191,0.5)]"
                      : "text-slate-600 dark:text-teal-300/80 hover:text-slate-900 dark:hover:text-white font-medium"
                  }`}
                >
                  Vol
                </button>
                <button
                  type="button"
                  onClick={() => setTokenViewMode("percentage")}
                  className={`px-2 py-1 rounded font-medium transition ${
                    tokenViewMode === "percentage"
                      ? "bg-teal-600 text-white font-bold shadow-xs dark:bg-teal-400 dark:text-teal-950 dark:shadow-[0_0_10px_rgba(45,212,191,0.5)]"
                      : "text-slate-600 dark:text-teal-300/80 hover:text-slate-900 dark:hover:text-white font-medium"
                  }`}
                >
                  %
                </button>
              </div>
            </div>

            {/* Central Radial Rings & Stage Pipeline */}
            <div className="mt-5 flex items-center gap-4">
              {/* Concentric Circular Rings SVG with Glowing Strokes */}
              <div className="relative w-28 h-28 flex-shrink-0 flex items-center justify-center">
                <svg className="w-full h-full -rotate-90" viewBox="0 0 100 100">
                  {/* Outer Ring: Prompt */}
                  <circle cx="50" cy="50" r="42" fill="none" stroke="currentColor" className="text-chart-track" strokeWidth="6" />
                  <circle
                    cx="50"
                    cy="50"
                    r="42"
                    fill="none"
                    stroke={promptColor}
                    strokeWidth="6"
                    strokeDasharray={2 * Math.PI * 42}
                    strokeDashoffset={2 * Math.PI * 42 * (1 - (promptTokens / totalTokens))}
                    strokeLinecap="round"
                    style={glow(promptColor)}
                  />
                  {/* Middle Ring: Reasoning */}
                  <circle cx="50" cy="50" r="32" fill="none" stroke="currentColor" className="text-chart-track" strokeWidth="6" />
                  <circle
                    cx="50"
                    cy="50"
                    r="32"
                    fill="none"
                    stroke={reasoningColor}
                    strokeWidth="6"
                    strokeDasharray={2 * Math.PI * 32}
                    strokeDashoffset={2 * Math.PI * 32 * (1 - (reasoningTokens / totalTokens))}
                    strokeLinecap="round"
                    style={glow(reasoningColor)}
                  />
                  {/* Inner Ring: Output */}
                  <circle cx="50" cy="50" r="22" fill="none" stroke="currentColor" className="text-chart-track" strokeWidth="6" />
                  <circle
                    cx="50"
                    cy="50"
                    r="22"
                    fill="none"
                    stroke={outputColor}
                    strokeWidth="6"
                    strokeDasharray={2 * Math.PI * 22}
                    strokeDashoffset={2 * Math.PI * 22 * (1 - (outputTokens / totalTokens))}
                    strokeLinecap="round"
                    style={glow(outputColor)}
                  />
                </svg>
                {/* Center Core Badge */}
                <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
                  <span className="text-[10px] text-slate-400 dark:text-teal-300/70 font-bold tracking-wider uppercase">Billed</span>
                  <span className="text-xs font-black font-mono text-slate-900 dark:text-white drop-shadow-[0_0_6px_rgba(255,255,255,0.4)]">
                    {(tokens.total_billed_tokens / 1000).toFixed(1)}k
                  </span>
                </div>
              </div>

              {/* Step-by-Step Stage Flow (Frosted Glass Rows) */}
              <div className="flex-1 space-y-2 text-xs">
                {/* 1. Prompt Context */}
                <div className="flex items-center justify-between p-2 rounded-lg bg-slate-50/70 hover:bg-slate-100/70 border border-slate-200/60 dark:bg-teal-950/40 dark:border-teal-500/20 dark:hover:border-teal-400/80 transition shadow-2xs">
                  <span className="flex items-center gap-1.5 text-slate-800 dark:text-cyan-200 font-semibold">
                    <span className="w-2 h-2 rounded-full" style={dot(promptColor, 8)} />
                    Prompt Context
                  </span>
                  <span className="font-mono font-bold text-slate-900 dark:text-white">
                    {tokenViewMode === "absolute" ? promptTokens.toLocaleString() : `${promptPct}%`}
                  </span>
                </div>

                {/* 2. Reasoning */}
                <div className="flex items-center justify-between p-2 rounded-lg bg-slate-50/70 hover:bg-slate-100/70 border border-slate-200/60 dark:bg-teal-950/40 dark:border-teal-500/20 dark:hover:border-teal-400/80 transition shadow-2xs">
                  <span className="flex items-center gap-1.5 text-slate-800 dark:text-teal-200 font-semibold">
                    <span className="w-2 h-2 rounded-full" style={dot(reasoningColor, 8)} />
                    Reasoning Trace
                  </span>
                  <span className="font-mono font-bold text-teal-700 dark:text-teal-200">
                    {tokenViewMode === "absolute" ? reasoningTokens.toLocaleString() : `${reasoningPct}%`}
                  </span>
                </div>

                {/* 3. Visible Output */}
                <div className="flex items-center justify-between p-2 rounded-lg bg-slate-50/70 hover:bg-slate-100/70 border border-slate-200/60 dark:bg-teal-950/40 dark:border-teal-500/20 dark:hover:border-teal-400/80 transition shadow-2xs">
                  <span className="flex items-center gap-1.5 text-slate-800 dark:text-emerald-200 font-semibold">
                    <span className="w-2 h-2 rounded-full" style={dot(outputColor, 8)} />
                    Visible Output
                  </span>
                  <span className="font-mono font-bold text-emerald-700 dark:text-emerald-300">
                    {tokenViewMode === "absolute" ? outputTokens.toLocaleString() : `${outputPct}%`}
                  </span>
                </div>
              </div>
            </div>

            {/* Compression Ratio Callout */}
            <div className="mt-4 p-2.5 rounded-xl bg-slate-50 border border-slate-200/70 dark:bg-surface-muted/80 dark:border-teal-500/20 shadow-2xs flex items-center justify-between text-xs">
              <span className="text-slate-700 dark:text-teal-200/90 font-medium flex items-center gap-1.5">
                <Zap className="w-3.5 h-3.5 text-amber-500 dark:text-amber-400 drop-shadow-[0_0_4px_rgba(245,158,11,0.6)]" />
                Context-to-Answer Synthesis:
              </span>
              <span className="font-black text-slate-900 dark:text-white font-mono">
                {(promptTokens / Math.max(outputTokens, 1)).toFixed(1)}:1 Ratio
              </span>
            </div>
          </div>

          <div className="relative z-10 mt-4 pt-3 border-t border-slate-100 dark:border-teal-500/20 flex items-center justify-between text-[11px] text-slate-500 dark:text-teal-300/70">
            <span>Avg Turn Budget: {Math.round(quality.avg_billed_tokens_per_turn || 0).toLocaleString()} tok</span>
            <span className="text-teal-600 dark:text-teal-300 font-bold drop-shadow-[0_0_6px_rgba(45,212,191,0.4)]">Cognitive Pipeline</span>
          </div>
        </div>

        {/* ----------------------------------------------------------------------- */}
        {/* DOMAIN 3: GENERATION QUALITY & LATENCY — TELEMETRY COCKPIT */}
        {/* ----------------------------------------------------------------------- */}
        <div data-tour="usage-latency-cockpit"
          id="telemetry-quality"
          className={`${dimUnless("quality")} relative rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-surface-card p-6 text-slate-900 dark:text-white shadow-card dark:shadow-xl flex flex-col justify-between transition-all overflow-hidden hover:border-teal-400 dark:hover:border-teal-400/60 ${
            activeDomainFilter === "quality"
              ? "ring-2 ring-cyan-500 border-cyan-500 dark:ring-cyan-400/80 dark:border-cyan-400/60"
              : ""
          }`}
        >
          {/* Ambient Corner Glow in top right */}
          <div className="absolute -top-12 -right-12 w-36 h-36 bg-cyan-400/10 dark:bg-cyan-500/15 rounded-full blur-2xl pointer-events-none" />

          <div className="relative z-10">
            <div className="flex items-center justify-between pb-3 border-b border-teal-100 dark:border-teal-500/20">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-xl bg-cyan-50 border border-cyan-200/80 text-cyan-600 dark:bg-cyan-500/15 dark:border-cyan-500/30 dark:text-cyan-300 flex items-center justify-center shadow-xs">
                  <Activity className="w-5 h-5" />
                </div>
                <div>
                  <h4 className="text-sm font-extrabold text-slate-900 dark:text-white tracking-tight">Performance Cockpit</h4>
                  <p className="text-[11px] text-slate-500 dark:text-teal-200/70">Speedometer & Citations</p>
                </div>
              </div>

              <div className="text-xs font-mono font-bold text-teal-800 dark:text-teal-200 bg-teal-50/70 dark:bg-teal-500/15 px-2.5 py-0.5 rounded-lg border border-teal-200/80 dark:border-teal-500/30 shadow-2xs">
                {quality.total_queries} turns
              </div>
            </div>

            {/* Cockpit Gauges (Speedometer & Groundedness Dial) */}
            <div className="mt-5 grid grid-cols-2 gap-4">
              {/* Gauge 1: Semi-Circular Speedometer for Latency */}
              <div className="flex flex-col items-center">
                <div className="relative w-32 h-20 overflow-hidden flex items-end justify-center">
                  <svg className="w-32 h-32 absolute top-0" viewBox="0 0 120 120">
                    <path d="M 20 80 A 40 40 0 0 1 50 40" fill="none" stroke={ct.status.ok} strokeWidth="8" strokeLinecap="round" />
                    <path d="M 50 40 A 40 40 0 0 1 80 45" fill="none" stroke={ct.status.warn} strokeWidth="8" />
                    <path d="M 80 45 A 40 40 0 0 1 100 80" fill="none" stroke={ct.status.danger} strokeWidth="8" strokeLinecap="round" />

                    {/* Needle 1: P50 (Teal) */}
                    <line
                      x1="60"
                      y1="75"
                      x2="60"
                      y2="42"
                      stroke={ct.categorical[0]}
                      strokeWidth="2.5"
                      strokeLinecap="round"
                      transform={`rotate(${p50Angle} 60 75)`}
                      className="transition-transform duration-500"
                    />

                    {/* Needle 2: P95 Tail (Amber) */}
                    <line
                      x1="60"
                      y1="75"
                      x2="60"
                      y2="38"
                      stroke={ct.status.warn}
                      strokeWidth="2.5"
                      strokeLinecap="round"
                      transform={`rotate(${p95Angle} 60 75)`}
                      className="transition-transform duration-500"
                    />

                    <circle cx="60" cy="75" r="4" className="fill-slate-900 dark:fill-white" />
                  </svg>
                </div>

                <div className="mt-1 text-center">
                  <span className="text-[10px] text-slate-400 dark:text-fg-muted block font-medium">Turn Speedometer</span>
                  <div className="flex items-center gap-2 text-xs font-mono">
                    <span className="text-cyan-600 dark:text-cyan-400 font-bold" title="Median P50">P50: {toSeconds(p50Ms)}s</span>
                    <span className="text-amber-600 dark:text-amber-400 font-bold" title="Tail P95">P95: {toSeconds(p95Ms)}s</span>
                  </div>
                </div>
              </div>

              {/* Gauge 2: Groundedness Pass Rate Shield Ring */}
              <div className="flex flex-col items-center">
                <div className="relative w-20 h-20 flex items-center justify-center">
                  <svg className="w-full h-full -rotate-90" viewBox="0 0 100 100">
                    <circle cx="50" cy="50" r="40" fill="none" stroke="currentColor" className="text-slate-200 dark:text-slate-800" strokeWidth="8" />
                    <circle
                      cx="50"
                      cy="50"
                      r="40"
                      fill="none"
                      stroke={ct.status.ok}
                      strokeWidth="8"
                      strokeDasharray={groundedCircumference}
                      strokeDashoffset={groundedCircumference - groundedStrokeDash}
                      strokeLinecap="round"
                      className="transition-all duration-700"
                    />
                  </svg>
                  <div className="absolute inset-0 flex flex-col items-center justify-center">
                    <ShieldCheck className="w-4 h-4 text-emerald-500 mb-0.5" />
                    <span className="text-xs font-bold font-mono text-slate-900 dark:text-white">
                      {groundedPassPct.toFixed(1)}%
                    </span>
                  </div>
                </div>

                <div className="mt-1 text-center">
                  <span className="text-[10px] text-slate-400 dark:text-fg-muted block font-medium">Groundedness</span>
                  <span className="text-xs font-semibold text-emerald-700 dark:text-emerald-400">Verified Citations</span>
                </div>
              </div>
            </div>

            {/* Clickable Outcome Filter Pills */}
            <div className="mt-4 pt-3 border-t border-slate-100 dark:border-white/[0.08]">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400 dark:text-fg-muted block mb-2">
                Click Outcome to Filter Real Query Feed
              </span>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <button
                  type="button"
                  onClick={() => setSelectedOutcome(selectedOutcome === "answered" ? "all" : "answered")}
                  className={`flex items-center justify-between px-2.5 py-1.5 rounded-lg border transition ${
                    selectedOutcome === "answered"
                      ? "bg-emerald-600 text-white font-bold border-emerald-700 shadow-sm"
                      : "bg-emerald-50 border-emerald-100 text-emerald-800 hover:bg-emerald-100 dark:bg-emerald-950/40 dark:border-emerald-500/25 dark:text-emerald-300 dark:hover:bg-emerald-950/60"
                  }`}
                >
                  <span className="flex items-center gap-1.5">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    <span>Answered</span>
                  </span>
                  <span className="font-mono">{outcomes.answered ?? 0}</span>
                </button>

                <button
                  type="button"
                  onClick={() => setSelectedOutcome(selectedOutcome === "ungrounded" ? "all" : "ungrounded")}
                  className={`flex items-center justify-between px-2.5 py-1.5 rounded-lg border transition ${
                    selectedOutcome === "ungrounded"
                      ? "bg-amber-600 text-white font-bold border-amber-700 shadow-sm"
                      : "bg-amber-50 border-amber-100 text-amber-800 hover:bg-amber-100 dark:bg-amber-950/40 dark:border-amber-500/25 dark:text-amber-300 dark:hover:bg-amber-950/60"
                  }`}
                >
                  <span className="flex items-center gap-1.5">
                    <AlertTriangle className="w-3.5 h-3.5" />
                    <span>Ungrounded</span>
                  </span>
                  <span className="font-mono">{outcomes.ungrounded ?? 0}</span>
                </button>

                <button
                  type="button"
                  onClick={() => setSelectedOutcome(selectedOutcome === "blocked" ? "all" : "blocked")}
                  className={`flex items-center justify-between px-2.5 py-1.5 rounded-lg border transition ${
                    selectedOutcome === "blocked"
                      ? "bg-rose-600 text-white font-bold border-rose-700 shadow-sm"
                      : "bg-rose-50 border-rose-100 text-rose-800 hover:bg-rose-100 dark:bg-rose-950/40 dark:border-rose-500/25 dark:text-rose-300 dark:hover:bg-rose-950/60"
                  }`}
                >
                  <span className="flex items-center gap-1.5">
                    <XCircle className="w-3.5 h-3.5" />
                    <span>Blocked</span>
                  </span>
                  <span className="font-mono">{outcomes.blocked ?? 0}</span>
                </button>

                <button
                  type="button"
                  onClick={() => setSelectedOutcome(selectedOutcome === "conversational" ? "all" : "conversational")}
                  className={`flex items-center justify-between px-2.5 py-1.5 rounded-lg border transition ${
                    selectedOutcome === "conversational"
                      ? "bg-blue-600 text-white font-bold border-blue-700 shadow-sm"
                      : "bg-blue-50 border-blue-100 text-blue-800 hover:bg-blue-100 dark:bg-blue-950/40 dark:border-blue-500/25 dark:text-blue-300 dark:hover:bg-blue-950/60"
                  }`}
                >
                  <span className="flex items-center gap-1.5">
                    <HelpCircle className="w-3.5 h-3.5" />
                    <span>Chit-chat</span>
                  </span>
                  <span className="font-mono">{outcomes.conversational ?? 0}</span>
                </button>
              </div>
            </div>
          </div>

          <div className="mt-4 pt-3 border-t border-teal-100 dark:border-teal-500/20 flex items-center justify-between text-[11px] text-slate-500 dark:text-fg-muted">
            <span>Accuracy: {accounting.actual_pct.toFixed(1)}% verified</span>
            <span className="text-teal-600 dark:text-cyan-400 font-bold">Cockpit Online</span>
          </div>
        </div>

      </div>

      {/* ========================================================================= */}
      {/* 3. REAL CHATBOT CONVERSATIONAL QUERY STREAM (Option 2 Aurora Glass) */}
      {/* ========================================================================= */}
      <div data-tour="usage-feed" data-feed-state={loadingQueries || feed.isPlaceholderData ? "pending" : "ready"} className="rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-surface-card p-6 shadow-card dark:shadow-xl space-y-4 transition-all hover:border-teal-400 dark:hover:border-teal-400/60">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-teal-100 dark:border-teal-500/20 pb-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-brand-600 dark:text-teal-400" />
              <h4 className="text-sm font-extrabold text-slate-900 dark:text-white tracking-tight">
                Live Chatbot Conversational Stream & Audit Feed
              </h4>
              <span className="rounded-full bg-teal-50/70 dark:bg-teal-500/15 px-2.5 py-0.5 text-[10px] font-mono font-bold text-teal-800 dark:text-teal-200 border border-teal-200/80 dark:border-teal-500/30">
                {selectedOutcome === "all" ? "All Turns" : `Filtered: ${selectedOutcome}`}
              </span>
            </div>
            <p className="text-xs text-slate-500 dark:text-teal-200/70">
              Real user prompts, grounded assistant responses, reasoning tokens, and latency traces straight from the production database.
            </p>
          </div>

          {/* Quick Outcome Filter Toggle */}
          <div className="flex items-center gap-1.5">
            {selectedOutcome !== "all" && (
              <button
                type="button"
                onClick={() => setSelectedOutcome("all")}
                className="text-xs text-slate-500 dark:text-fg-muted hover:text-slate-900 dark:hover:text-white bg-slate-100 dark:bg-white/10 px-2.5 py-1 rounded-lg font-medium transition"
              >
                Clear Filter (Show All)
              </button>
            )}
            <span className="text-xs text-slate-400 dark:text-fg-subtle font-mono">
              {realQueries.length} recent turns
            </span>
          </div>
        </div>

        {/* Loading / Error States */}
        {loadingQueries && (
          <div className="flex h-36 items-center justify-center gap-2 text-xs text-slate-500 dark:text-fg-muted">
            <RefreshCw className="w-4 h-4 animate-spin text-brand-primary dark:text-teal-400" />
            <span>Loading real chatbot queries...</span>
          </div>
        )}

        {queriesError && (
          <div className="p-4 rounded-xl bg-rose-50 border border-rose-200 dark:bg-rose-950/40 dark:border-rose-900 text-xs text-rose-700 dark:text-rose-300">
            {queriesError}
          </div>
        )}

        {/* Real Queries Table List */}
        {!loadingQueries && !queriesError && realQueries.length === 0 && (
          <div className="py-8 text-center text-xs text-slate-400 dark:text-fg-subtle">
            No chatbot turns found matching the selected outcome.
          </div>
        )}

        {!loadingQueries && !queriesError && realQueries.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-surface-studio text-fg-muted font-semibold border-b border-line-brand/70 dark:border-brand-500/20">
                <tr>
                  <th className="px-4 py-2.5">User Prompt & Chatbot Answer</th>
                  <th className="px-3 py-2.5">Outcome</th>
                  <th className="px-3 py-2.5 text-right">Reasoning Tokens</th>
                  <th className="px-3 py-2.5 text-right">Output Tokens</th>
                  <th className="px-3 py-2.5 text-right">Speed</th>
                  <th className="px-3 py-2.5 text-right">Cost (USD)</th>
                  <th className="px-4 py-2.5 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-white/[0.06] text-slate-700 dark:text-fg">
                {realQueries.map((q) => (
                  <tr
                    key={q.request_id}
                    className="hover:bg-teal-50/40 dark:hover:bg-white/[0.04] transition cursor-pointer"
                    onClick={() => setInspectRequestId(q.request_id)}
                    {...feedRowIntent(q)}
                  >
                    {/* Prompt & Answer */}
                    <td className="px-4 py-3 max-w-sm sm:max-w-md">
                      <div className="font-semibold text-slate-900 dark:text-white truncate" title={q.query}>
                        &ldquo;{q.query}&rdquo;
                      </div>
                      <div className="text-[11px] text-slate-500 dark:text-fg-muted truncate mt-0.5" title={q.answer}>
                        {q.answer || "No response generated"}
                      </div>
                      <div className="text-[10px] text-slate-400 dark:text-fg-subtle font-mono mt-0.5 flex items-center gap-2">
                        <span>IP: {q.ip_address}</span>
                        <span>•</span>
                        <span>Session: {q.session_title || q.session_id.slice(0, 8)}</span>
                      </div>
                    </td>

                    {/* Outcome Badge */}
                    <td className="px-3 py-3 whitespace-nowrap">
                      <OutcomeBadge outcome={q.outcome} />
                    </td>

                    {/* Reasoning Tokens */}
                    <td className="px-3 py-3 text-right font-mono font-medium whitespace-nowrap">
                      {formatTokens(q.reasoning_tokens, "reasoning")}
                    </td>

                    {/* Output Tokens */}
                    <td className="px-3 py-3 text-right font-mono font-medium whitespace-nowrap">
                      {formatTokens(q.output_tokens, "output")}
                    </td>

                    {/* Latency */}
                    <td className="px-3 py-3 text-right font-mono text-slate-600 dark:text-fg-muted whitespace-nowrap">
                      {q.latency_ms ? `${toSeconds(q.latency_ms)} s` : "—"}
                    </td>

                    {/* Cost */}
                    <td className="px-3 py-3 text-right font-mono font-bold whitespace-nowrap">
                      {formatCurrency(q.cost_usd, 4)}
                    </td>

                    {/* Action */}
                    <td className="px-4 py-3 text-right whitespace-nowrap">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setInspectRequestId(q.request_id);
                        }}
                        className="inline-flex items-center gap-1 rounded-lg border border-slate-200 dark:border-white/15 bg-white dark:bg-white/[0.06] px-2.5 py-1 text-[11px] font-semibold text-brand-primary dark:text-teal-300 shadow-2xs hover:bg-slate-50 dark:hover:bg-white/10 transition"
                      >
                        <Eye className="w-3 h-3" />
                        <span>Inspect Trace</span>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Inspector Slide-Over Drawer (Unblurred) */}
      {inspectRequestId && (
        <QueryInspectorDrawer
          requestId={inspectRequestId}
          token={token}
          onClose={() => setInspectRequestId(null)}
        />
      )}
    </div>
  );
}
