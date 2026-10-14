"use client";

import React from "react";
import type { UsageSummary } from "@/lib/types";
import {
  DollarSign,
  Cpu,
  Layers,
  Sparkles,
  Zap,
  Clock,
  ShieldAlert,
  HelpCircle,
  Database,
} from "lucide-react";
import { StatCard, AccuracyBadge, TokenDistributionBar } from "../insights";

interface OverviewTabProps {
  summary: UsageSummary | null;
  isLoading: boolean;
  error?: string | null;
  onRetry?: () => void;
}

export function OverviewTab({
  summary,
  isLoading,
  error,
  onRetry,
}: OverviewTabProps) {
  if (isLoading && !summary) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-28 bg-slate-200/60 rounded-xl" />
          ))}
        </div>
        <div className="h-64 bg-slate-200/60 rounded-xl" />
        <div className="h-64 bg-slate-200/60 rounded-xl" />
      </div>
    );
  }

  if (error || !summary) {
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50/50 p-8 text-center">
        <ShieldAlert className="w-8 h-8 text-rose-500 mx-auto mb-2" />
        <h3 className="text-sm font-semibold text-rose-900">
          Failed to load Stage Economics Summary
        </h3>
        <p className="text-xs text-rose-700 mt-1 max-w-md mx-auto">
          {error || "No usage data found for the selected audience scope."}
        </p>
        {onRetry && (
          <button
            onClick={onRetry}
            className="mt-4 px-4 py-1.5 bg-rose-600 text-white rounded-lg text-xs font-medium hover:bg-rose-700 transition"
          >
            Retry Analytics
          </button>
        )}
      </div>
    );
  }

  const { financial, tokens, pricing, accounting, quality, as_of, audience } = summary;

  return (
    <div className="space-y-8">
      {/* As of banner */}
      <div className="flex items-center justify-between text-xs text-slate-500 bg-white/70 backdrop-blur-sm border border-slate-200/60 px-4 py-2 rounded-lg">
        <div className="flex items-center gap-2">
          <Clock className="w-3.5 h-3.5 text-slate-400" />
          <span>
            Telemetry as of:{" "}
            <span className="font-medium text-slate-700">
              {as_of ? new Date(as_of).toLocaleTimeString() : "Just now"}
            </span>
          </span>
        </div>
        <AccuracyBadge
          actualPct={accounting.actual_pct}
          actualCount={accounting.actual_turns}
          totalCount={accounting.total_turns}
        />
      </div>

      {/* ========================================================================= */}
      {/* BLOCK 1: FINANCIAL COST ECONOMICS ($ USD) */}
      {/* ========================================================================= */}
      <section className="space-y-4">
        <div className="flex items-center justify-between border-b border-slate-200 pb-2">
          <div>
            <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
              <DollarSign className="w-5 h-5 text-emerald-600" />
              Financial Cost Economics (USD)
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">
              Net expenditure across LLM generation, vector embeddings, and ingestion platform operations.
            </p>
          </div>
        </div>

        {/* Financial Hero Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            title="Total Platform Cost"
            value={`$${financial.total_cost_usd.toFixed(4)}`}
            subtitle={`Avg $${financial.avg_cost_per_query_usd.toFixed(4)} / turn`}
            icon={DollarSign}
            className="border-emerald-200/80 bg-gradient-to-br from-white to-emerald-50/20"
          />

          <StatCard
            title="LLM Generation Cost"
            value={`$${financial.llm_cost_usd.toFixed(4)}`}
            subtitle={`${(
              (financial.llm_cost_usd / (financial.total_cost_usd || 1)) *
              100
            ).toFixed(1)}% of total cost`}
            icon={Cpu}
          />

          <StatCard
            title="Embeddings Pipeline Cost"
            value={`$${financial.embeddings_cost_usd.toFixed(4)}`}
            subtitle={`Query, guardrail & ingestion`}
            icon={Layers}
          />

          <StatCard
            title="Telemetry Accounting"
            value={`${accounting.actual_pct}%`}
            subtitle={`${accounting.actual_turns} actual, ${accounting.estimated_turns} estimated`}
            icon={Sparkles}
          />
        </div>

        {/* Financial Stage Breakdown Table */}
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="bg-slate-50/80 px-4 py-3 border-b border-slate-200 text-xs font-semibold text-slate-700 flex justify-between items-center">
            <span>Granular Cost Accounting by Pipeline Stage</span>
            <span className="text-slate-400 font-normal text-[11px]">Pricing Rate Card Active</span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50/50 text-slate-500 font-medium border-b border-slate-100">
                <tr>
                  <th className="px-4 py-2.5">Pipeline Stage</th>
                  <th className="px-4 py-2.5">Token Type</th>
                  <th className="px-4 py-2.5 text-right">Volume</th>
                  <th className="px-4 py-2.5 text-right">Unit Rate (per 1M)</th>
                  <th className="px-4 py-2.5 text-right">Total Cost (USD)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 text-slate-700">
                <tr>
                  <td className="px-4 py-2.5 font-medium flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-cyan-500" />
                    Query Embeddings
                  </td>
                  <td className="px-4 py-2.5 text-slate-500">Vector Embeddings</td>
                  <td className="px-4 py-2.5 text-right font-mono">
                    {tokens.embeddings_tokens.query_embed_tokens.toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-slate-500">
                    ${pricing.price_embed_per_million.toFixed(3)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-semibold font-mono text-slate-900">
                    ${financial.stage_costs.query_embed_cost.toFixed(5)}
                  </td>
                </tr>

                <tr>
                  <td className="px-4 py-2.5 font-medium flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-cyan-600" />
                    Guardrail Embeddings
                  </td>
                  <td className="px-4 py-2.5 text-slate-500">Vector Embeddings</td>
                  <td className="px-4 py-2.5 text-right font-mono">
                    {tokens.embeddings_tokens.guardrail_embed_tokens.toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-slate-500">
                    ${pricing.price_embed_per_million.toFixed(3)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-semibold font-mono text-slate-900">
                    ${financial.stage_costs.guardrail_embed_cost.toFixed(5)}
                  </td>
                </tr>

                <tr>
                  <td className="px-4 py-2.5 font-medium flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-indigo-500" />
                    Prompt Context
                  </td>
                  <td className="px-4 py-2.5 text-slate-500">LLM Prompt (Uncached)</td>
                  <td className="px-4 py-2.5 text-right font-mono">
                    {tokens.llm_tokens.prompt_context_tokens.toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-slate-500">
                    ${pricing.price_prompt_per_million.toFixed(3)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-semibold font-mono text-slate-900">
                    ${financial.stage_costs.prompt_uncached_cost.toFixed(5)}
                  </td>
                </tr>

                <tr>
                  <td className="px-4 py-2.5 font-medium flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-teal-400" />
                    Cached Prompt Context
                  </td>
                  <td className="px-4 py-2.5 text-slate-500">LLM Cached Context</td>
                  <td className="px-4 py-2.5 text-right font-mono">
                    {tokens.llm_tokens.cached_prompt_tokens.toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-slate-500">
                    ${pricing.price_cached_per_million.toFixed(3)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-semibold font-mono text-slate-900">
                    ${financial.stage_costs.cached_prompt_cost.toFixed(5)}
                  </td>
                </tr>

                <tr>
                  <td className="px-4 py-2.5 font-medium flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-purple-500" />
                    Reasoning Tokens
                  </td>
                  <td className="px-4 py-2.5 text-slate-500">LLM Thinking / Chain</td>
                  <td className="px-4 py-2.5 text-right font-mono">
                    {tokens.llm_tokens.reasoning_tokens.toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-slate-500">
                    ${pricing.price_output_per_million.toFixed(3)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-semibold font-mono text-slate-900">
                    ${financial.stage_costs.reasoning_cost.toFixed(5)}
                  </td>
                </tr>

                <tr>
                  <td className="px-4 py-2.5 font-medium flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-emerald-500" />
                    Visible Output
                  </td>
                  <td className="px-4 py-2.5 text-slate-500">LLM Rendered Output</td>
                  <td className="px-4 py-2.5 text-right font-mono">
                    {tokens.llm_tokens.visible_output_tokens.toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-slate-500">
                    ${pricing.price_output_per_million.toFixed(3)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-semibold font-mono text-slate-900">
                    ${financial.stage_costs.visible_cost.toFixed(5)}
                  </td>
                </tr>

                <tr className="bg-slate-50/30">
                  <td className="px-4 py-2.5 font-medium flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-slate-400" />
                    Document Ingestion Overhead
                  </td>
                  <td className="px-4 py-2.5 text-slate-500">Platform Indexing</td>
                  <td className="px-4 py-2.5 text-right font-mono">
                    {tokens.embeddings_tokens.ingestion_embed_tokens.toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-slate-500">
                    ${pricing.price_embed_per_million.toFixed(3)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-semibold font-mono text-slate-900">
                    ${financial.stage_costs.ingestion_overhead_cost.toFixed(5)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* ========================================================================= */}
      {/* BLOCK 2: TOKEN VOLUME BREAKDOWN */}
      {/* ========================================================================= */}
      <section className="space-y-4">
        <div className="flex items-center justify-between border-b border-slate-200 pb-2">
          <div>
            <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
              <Zap className="w-5 h-5 text-brand-600" />
              Token Volume Telemetry
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">
              Comprehensive volume of input prompt context, reasoning steps, visible response tokens, and vector embeddings.
            </p>
          </div>
        </div>

        {/* Token Volume Hero Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            title="Total Billed Tokens"
            value={tokens.total_billed_tokens}
            subtitle={`Avg ${quality.avg_billed_tokens_per_turn} / turn`}
            icon={Zap}
            className="border-brand-200/80 bg-gradient-to-br from-white to-brand-50/20"
          />

          <StatCard
            title="Input Prompt Context"
            value={tokens.llm_tokens.prompt_context_tokens}
            subtitle={`Context & system instructions`}
            icon={Database}
          />

          <StatCard
            title="Reasoning Chain Tokens"
            value={tokens.llm_tokens.reasoning_tokens}
            subtitle={`Deep-thinking reasoning trace`}
            icon={Cpu}
          />

          <StatCard
            title="Visible Output Tokens"
            value={tokens.llm_tokens.visible_output_tokens}
            subtitle={`Rendered user answers`}
            icon={Sparkles}
          />
        </div>

        {/* Interactive Visual Token Distribution Bar */}
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-3">
          <div className="flex items-center justify-between">
            <h4 className="text-xs font-semibold text-slate-800">
              Token Distribution Across Pipeline Stages
            </h4>
            <span className="text-[11px] text-slate-400">
              {tokens.total_billed_tokens.toLocaleString()} Total Tokens
            </span>
          </div>

          <TokenDistributionBar
            data={{
              prompt: tokens.llm_tokens.prompt_context_tokens,
              reasoning: tokens.llm_tokens.reasoning_tokens,
              visible: tokens.llm_tokens.visible_output_tokens,
              embeddings:
                tokens.embeddings_tokens.query_embed_tokens +
                tokens.embeddings_tokens.guardrail_embed_tokens,
              cached: tokens.llm_tokens.cached_prompt_tokens,
            }}
          />
        </div>
      </section>

      {/* Quality & Latency Summary Block */}
      <section className="space-y-4">
        <div className="flex items-center justify-between border-b border-slate-200 pb-2">
          <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-purple-600" />
            Generation Quality & Latency Performance
          </h3>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            title="Groundedness Pass Rate"
            value={`${quality.groundedness_pass_rate}%`}
            subtitle="Verified against retrieved citations"
            icon={Sparkles}
          />

          <StatCard
            title="Latency (P50 Median)"
            value={`${quality.latency.p50_ms} ms`}
            subtitle="Typical query response time"
            icon={Clock}
          />

          <StatCard
            title="Latency (P95 Tail)"
            value={`${quality.latency.p95_ms} ms`}
            subtitle="Worst 5% tail response latency"
            icon={Clock}
          />

          <StatCard
            title="Total Processed Queries"
            value={quality.total_queries}
            subtitle={`Audience: ${audience}`}
            icon={HelpCircle}
          />
        </div>
      </section>
    </div>
  );
}
