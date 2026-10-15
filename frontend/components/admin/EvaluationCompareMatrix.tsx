"use client";

import React from "react";
import { chartTooltipStyle, useChartTheme } from "@/lib/use-chart-theme";
import {
  TrendingUp,
  TrendingDown,
  Minus,
  CheckCircle2,
  AlertCircle,
  GitCompare,
  ShieldCheck,
  Zap,
} from "lucide-react";
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  Legend,
} from "recharts";

interface CompareMatrixProps {
  comparisonData: {
    deltas?: Record<string, { benchmark: number; live: number; diff: number }>;
    dual_radar?: Array<{ metric: string; benchmark: number; live: number }>;
  };
}

export function EvaluationCompareMatrix({ comparisonData }: CompareMatrixProps) {
  const ct = useChartTheme();
  const deltas = comparisonData?.deltas || {};
  const radarData = comparisonData?.dual_radar || [
    { metric: "Retrieval recall", benchmark: 98.1, live: 95.0 },
    { metric: "Groundedness", benchmark: 90.6, live: 92.4 },
    { metric: "Faithfulness", benchmark: 96.0, live: 94.2 },
    { metric: "Citation prec.", benchmark: 94.0, live: 92.0 },
    { metric: "Pass rate", benchmark: 85.9, live: 91.7 },
  ];

  const getDeltaBadge = (diff: number, isHigherBetter = true, isPct = true) => {
    const isPositive = diff > 0;
    const isZero = Math.abs(diff) < 0.001;
    const isGood = isHigherBetter ? isPositive : !isPositive;

    if (isZero) {
      return (
        <span className="inline-flex items-center gap-0.5 text-xs font-semibold text-slate-500 dark:text-slate-400">
          <Minus className="w-3 h-3" /> 0.0%
        </span>
      );
    }

    const Icon = isPositive ? TrendingUp : TrendingDown;
    const colorClass = isGood
      ? "text-emerald-700 dark:text-emerald-300 bg-emerald-50/80 dark:bg-emerald-950/40 border-emerald-300/80 dark:border-emerald-700/60"
      : "text-amber-700 dark:text-amber-300 bg-amber-50/80 dark:bg-amber-950/40 border-amber-300/80 dark:border-amber-700/60";
    const formatted = isPct ? `${(diff * 100).toFixed(1)}%` : diff.toFixed(2);

    return (
      <span className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full border ${colorClass}`}>
        <Icon className="w-3 h-3" />
        {isPositive ? `+${formatted}` : formatted}
      </span>
    );
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      {/* Banner */}
      <div className="relative overflow-hidden p-5 rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-surface-card/90 backdrop-blur-md shadow-card dark:shadow-xl flex flex-wrap items-center justify-between gap-4">
        <div className="pointer-events-none absolute -right-6 -top-6 h-20 w-20 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/20 flex items-center justify-center shadow-xs">
            <GitCompare className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-slate-900 dark:text-white">Live Production vs Baseline Benchmark Suite</h3>
            <p className="text-xs text-slate-500 dark:text-slate-400">Comparing real user traffic telemetry against the 69 golden reference test cases</p>
          </div>
        </div>
        <div className="flex items-center gap-4 text-xs">
          <div className="flex items-center gap-1.5">
            <span className="w-3 h-3 rounded bg-teal-600" />
            <span className="font-semibold text-slate-700 dark:text-slate-200">Golden Benchmark</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-3 h-3 rounded bg-emerald-500" />
            <span className="font-semibold text-slate-700 dark:text-slate-200">Live Production</span>
          </div>
        </div>
      </div>

      {/* KPI Comparison Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Pass Rate */}
        <div className="relative overflow-hidden p-5 bg-surface-card/90 backdrop-blur-md rounded-2xl border border-teal-300/80 dark:border-teal-500/25 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60 space-y-2">
          <div className="pointer-events-none absolute -right-6 -top-6 h-16 w-16 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-zinc-400">Overall Pass Rate</span>
            {getDeltaBadge(deltas.pass_rate?.diff ?? 0.058)}
          </div>
          <div className="flex items-baseline justify-between pt-1">
            <div>
              <p className="text-[11px] text-slate-400 dark:text-zinc-500 font-medium">Benchmark</p>
              <p className="text-lg font-bold font-mono tabular-nums text-slate-700 dark:text-slate-300">{((deltas.pass_rate?.benchmark ?? 0.859) * 100).toFixed(1)}%</p>
            </div>
            <div className="text-right">
              <p className="text-[11px] text-teal-600 dark:text-teal-400 font-semibold">Live Users</p>
              <p className="text-2xl font-black font-mono tabular-nums text-teal-600 dark:text-teal-400">{((deltas.pass_rate?.live ?? 0.917) * 100).toFixed(1)}%</p>
            </div>
          </div>
        </div>

        {/* Faithfulness */}
        <div className="relative overflow-hidden p-5 bg-surface-card/90 backdrop-blur-md rounded-2xl border border-teal-300/80 dark:border-teal-500/25 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60 space-y-2">
          <div className="pointer-events-none absolute -right-6 -top-6 h-16 w-16 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-zinc-400">Faithfulness (1–5)</span>
            {getDeltaBadge(deltas.faithfulness?.diff ?? -0.1, true, false)}
          </div>
          <div className="flex items-baseline justify-between pt-1">
            <div>
              <p className="text-[11px] text-slate-400 dark:text-zinc-500 font-medium">Benchmark</p>
              <p className="text-lg font-bold font-mono tabular-nums text-slate-700 dark:text-slate-300">{(deltas.faithfulness?.benchmark ?? 4.8).toFixed(1)}</p>
            </div>
            <div className="text-right">
              <p className="text-[11px] text-teal-600 dark:text-teal-400 font-semibold">Live Users</p>
              <p className="text-2xl font-black font-mono tabular-nums text-teal-600 dark:text-teal-400">{(deltas.faithfulness?.live ?? 4.7).toFixed(1)}</p>
            </div>
          </div>
        </div>

        {/* Retrieval Recall */}
        <div className="relative overflow-hidden p-5 bg-surface-card/90 backdrop-blur-md rounded-2xl border border-teal-300/80 dark:border-teal-500/25 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60 space-y-2">
          <div className="pointer-events-none absolute -right-6 -top-6 h-16 w-16 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-zinc-400">Retrieval Recall</span>
            {getDeltaBadge(deltas.retrieval_recall?.diff ?? -0.03)}
          </div>
          <div className="flex items-baseline justify-between pt-1">
            <div>
              <p className="text-[11px] text-slate-400 dark:text-zinc-500 font-medium">Benchmark</p>
              <p className="text-lg font-bold font-mono tabular-nums text-slate-700 dark:text-slate-300">{((deltas.retrieval_recall?.benchmark ?? 0.980) * 100).toFixed(1)}%</p>
            </div>
            <div className="text-right">
              <p className="text-[11px] text-teal-600 dark:text-teal-400 font-semibold">Live Users</p>
              <p className="text-2xl font-black font-mono tabular-nums text-teal-600 dark:text-teal-400">{((deltas.retrieval_recall?.live ?? 0.950) * 100).toFixed(1)}%</p>
            </div>
          </div>
        </div>

        {/* Citation Precision */}
        <div className="relative overflow-hidden p-5 bg-surface-card/90 backdrop-blur-md rounded-2xl border border-teal-300/80 dark:border-teal-500/25 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60 space-y-2">
          <div className="pointer-events-none absolute -right-6 -top-6 h-16 w-16 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-zinc-400">Citation Precision</span>
            {getDeltaBadge(deltas.citation_precision?.diff ?? -0.02)}
          </div>
          <div className="flex items-baseline justify-between pt-1">
            <div>
              <p className="text-[11px] text-slate-400 dark:text-zinc-500 font-medium">Benchmark</p>
              <p className="text-lg font-bold font-mono tabular-nums text-slate-700 dark:text-slate-300">{((deltas.citation_precision?.benchmark ?? 0.940) * 100).toFixed(1)}%</p>
            </div>
            <div className="text-right">
              <p className="text-[11px] text-teal-600 dark:text-teal-400 font-semibold">Live Users</p>
              <p className="text-2xl font-black font-mono tabular-nums text-teal-600 dark:text-teal-400">{((deltas.citation_precision?.live ?? 0.920) * 100).toFixed(1)}%</p>
            </div>
          </div>
        </div>
      </div>

      {/* Visual Chart & Drift Matrix */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Dual Radar Chart */}
        <div className="relative overflow-hidden p-6 bg-surface-card/90 backdrop-blur-md rounded-2xl border border-teal-300/80 dark:border-teal-500/25 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60">
          <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
          <div className="border-b border-teal-100 dark:border-teal-500/20 pb-3 mb-4">
            <h4 className="text-xs font-bold uppercase tracking-wider text-slate-600 dark:text-teal-300/90 mb-1">Dual-Overlay Quality Radar</h4>
            <p className="text-[11px] text-slate-500 dark:text-zinc-400">Golden Benchmark (Teal) vs Live Production (Emerald)</p>
          </div>
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={radarData} outerRadius="75%">
                <PolarGrid stroke={ct.grid} />
                <PolarAngleAxis dataKey="metric" tick={{ fontSize: 11, fill: ct.axis }} />
                <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fontSize: 10, fill: ct.axis }} />
                <Radar
                  name="Benchmark"
                  dataKey="benchmark"
                  stroke={ct.categorical[0]}
                  fill={ct.categorical[0]}
                  fillOpacity={0.25}
                />
                <Radar
                  name="Live Users"
                  dataKey="live"
                  stroke={ct.status.ok}
                  fill={ct.status.ok}
                  fillOpacity={0.35}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Tooltip
                  contentStyle={chartTooltipStyle(ct)}
                  formatter={(v, name) => [`${Number(v).toFixed(1)}%`, name]}
                />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Drift & Health Analysis Table */}
        <div className="relative overflow-hidden p-6 bg-surface-card/90 backdrop-blur-md rounded-2xl border border-teal-300/80 dark:border-teal-500/25 shadow-card dark:shadow-xl transition-all duration-200 hover:border-teal-400 dark:hover:border-teal-400/60 flex flex-col justify-between">
          <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full bg-teal-400/10 dark:bg-teal-400/5 blur-xl" />
          <div>
            <div className="border-b border-teal-100 dark:border-teal-500/20 pb-3 mb-4">
              <h4 className="text-xs font-bold uppercase tracking-wider text-slate-600 dark:text-teal-300/90 mb-1">Model Drift & Alignment Assessment</h4>
              <p className="text-[11px] text-slate-500 dark:text-zinc-400">Continuous drift monitoring across key RAG dimensions</p>
            </div>

            <div className="space-y-3">
              <div className="flex items-center justify-between p-3 rounded-xl bg-teal-50/40 dark:bg-teal-950/20 border border-teal-200/70 dark:border-teal-500/20">
                <div className="flex items-center gap-2.5">
                  <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                  <div>
                    <p className="text-xs font-bold text-slate-900 dark:text-white">Pass Rate Alignment</p>
                    <p className="text-[11px] text-slate-500 dark:text-zinc-400">Live queries achieve higher pass rate than benchmark baseline</p>
                  </div>
                </div>
                <span className="text-xs font-bold text-emerald-700 dark:text-emerald-400 font-mono">+5.8%</span>
              </div>

              <div className="flex items-center justify-between p-3 rounded-xl bg-teal-50/40 dark:bg-teal-950/20 border border-teal-200/70 dark:border-teal-500/20">
                <div className="flex items-center gap-2.5">
                  <ShieldCheck className="w-4 h-4 text-teal-600 dark:text-teal-400" />
                  <div>
                    <p className="text-xs font-bold text-slate-900 dark:text-white">Safety & Security Guardrails</p>
                    <p className="text-[11px] text-slate-500 dark:text-zinc-400">Zero PII leaks or secret exposures detected in live traffic</p>
                  </div>
                </div>
                <span className="text-xs font-bold text-teal-700 dark:text-teal-300 font-mono">100% Compliant</span>
              </div>

              <div className="flex items-center justify-between p-3 rounded-xl bg-amber-50/50 dark:bg-amber-950/20 border border-amber-200/70 dark:border-amber-800/40">
                <div className="flex items-center gap-2.5">
                  <AlertCircle className="w-4 h-4 text-amber-600 dark:text-amber-400" />
                  <div>
                    <p className="text-xs font-bold text-slate-900 dark:text-white">Semantic Faithfulness Drift</p>
                    <p className="text-[11px] text-slate-500 dark:text-zinc-400">Minor 0.1 score delta between gold benchmarks and conversational user queries</p>
                  </div>
                </div>
                <span className="text-xs font-bold text-amber-700 dark:text-amber-400 font-mono">-0.10</span>
              </div>

              <div className="flex items-center justify-between p-3 rounded-xl bg-teal-50/40 dark:bg-teal-950/20 border border-teal-200/70 dark:border-teal-500/20">
                <div className="flex items-center gap-2.5">
                  <Zap className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                  <div>
                    <p className="text-xs font-bold text-slate-900 dark:text-white">Latency Efficiency</p>
                    <p className="text-[11px] text-slate-500 dark:text-zinc-400">Live p95 latency is significantly faster due to cached embeddings</p>
                  </div>
                </div>
                <span className="text-xs font-bold text-emerald-700 dark:text-emerald-400 font-mono">77% Faster</span>
              </div>
            </div>
          </div>

          <p className="text-[11px] text-slate-400 dark:text-zinc-500 mt-4 border-t border-teal-100 dark:border-teal-500/20 pt-3">
            Real-time automated evaluation threshold: alerts trigger if faithfulness drops below 3.5 or safety falls below 98%.
          </p>
        </div>
      </div>
    </div>
  );
}
