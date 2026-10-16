"use client";

import React, { useEffect, useState } from "react";
import { RefreshCw, Copy, Check, Clock, ShieldCheck } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { entityHref } from "@/lib/entity-links";
import { DrawerShell } from "@/components/ui/DrawerShell";
import { ErrorState } from "@/components/ui/ErrorState";

interface QueryInspectorDrawerProps {
  requestId: string;
  token: string;
  onClose: () => void;
}

export function QueryInspectorDrawer({
  requestId,
  token,
  onClose,
}: QueryInspectorDrawerProps) {
  const [detail, setDetail] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copiedQuery, setCopiedQuery] = useState(false);
  const [copiedAnswer, setCopiedAnswer] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  // Fetch query detail
  useEffect(() => {
    if (!token || !requestId) return;
    setLoading(true);
    setError(null);

    const abortController = new AbortController();
    api
      .getQueryDetail(token, requestId, abortController.signal)
      .then((res) => {
        setDetail(res);
      })
      .catch((err) => {
        if (err.name !== "AbortError") {
          setError(
            err instanceof ApiError ? err.message : "Failed to load query breakdown."
          );
        }
      })
      .finally(() => setLoading(false));

    return () => abortController.abort();
  }, [token, requestId, reloadKey]);

  const handleCopy = (text: string, type: "query" | "answer") => {
    navigator.clipboard.writeText(text);
    if (type === "query") {
      setCopiedQuery(true);
      setTimeout(() => setCopiedQuery(false), 2000);
    } else {
      setCopiedAnswer(true);
      setTimeout(() => setCopiedAnswer(false), 2000);
    }
  };

  const pricing = detail?.pricing_rate_card || {
    price_embed_per_million: 0.02,
    price_input_per_million: 0.25,
    price_output_per_million: 2.0,
    price_cached_per_million: 0.0625,
  };

  const tokens = detail?.tokens || {
    prompt: 0,
    cached_prompt: 0,
    reasoning: 0,
    visible: 0,
    query_embed: 0,
    guardrail_embed: 0,
    total_billed: 0,
  };

  const promptCost = ((tokens.prompt || 0) / 1_000_000) * pricing.price_input_per_million;
  const cachedCost =
    ((tokens.cached_prompt || 0) / 1_000_000) * (pricing.price_cached_per_million || 0.0625);
  const reasoningCost =
    ((tokens.reasoning || 0) / 1_000_000) * pricing.price_output_per_million;
  const visibleCost =
    ((tokens.visible || 0) / 1_000_000) * pricing.price_output_per_million;
  const queryEmbedCost =
    ((tokens.query_embed || 0) / 1_000_000) * pricing.price_embed_per_million;
  const guardrailEmbedCost =
    ((tokens.guardrail_embed || 0) / 1_000_000) * pricing.price_embed_per_million;

  const totalCalculatedCost =
    promptCost +
    cachedCost +
    reasoningCost +
    visibleCost +
    queryEmbedCost +
    guardrailEmbedCost;

  const embeddingsTokens = (tokens.query_embed || 0) + (tokens.guardrail_embed || 0);
  const totalBilledTokens =
    tokens.total_billed ||
    ((tokens.prompt || 0) +
      (tokens.cached_prompt || 0) +
      (tokens.reasoning || 0) +
      (tokens.visible || 0) +
      embeddingsTokens) ||
    1;

  const promptPct = ((tokens.prompt || 0) / totalBilledTokens) * 100;
  const cachedPct = ((tokens.cached_prompt || 0) / totalBilledTokens) * 100;
  const reasoningPct = ((tokens.reasoning || 0) / totalBilledTokens) * 100;
  const visiblePct = ((tokens.visible || 0) / totalBilledTokens) * 100;
  const embedPct = (embeddingsTokens / totalBilledTokens) * 100;

  const tokenCategories = [
    {
      id: "prompt",
      label: "Prompt Context",
      shortLabel: "Prompt",
      tokens: tokens.prompt || 0,
      pct: promptPct,
      cost: promptCost,
      barColor: "bg-teal-600 dark:bg-teal-500",
      dotColor: "bg-teal-600 dark:bg-teal-400",
      textColor: "text-teal-700 dark:text-teal-300",
      badgeBg: "bg-teal-50 dark:bg-teal-950/50 text-teal-700 dark:text-teal-300 border-teal-200/80 dark:border-teal-800/60",
    },
    ...(tokens.cached_prompt > 0
      ? [
          {
            id: "cached",
            label: "Cached Context",
            shortLabel: "Cached",
            tokens: tokens.cached_prompt,
            pct: cachedPct,
            cost: cachedCost,
            barColor: "bg-sky-600 dark:bg-sky-500",
            dotColor: "bg-sky-600 dark:bg-sky-400",
            textColor: "text-sky-700 dark:text-sky-300",
            badgeBg: "bg-sky-50 dark:bg-sky-950/50 text-sky-700 dark:text-sky-300 border-sky-200/80 dark:border-sky-800/60",
          },
        ]
      : []),
    {
      id: "reasoning",
      label: "LLM Reasoning",
      shortLabel: "Reasoning",
      tokens: tokens.reasoning || 0,
      pct: reasoningPct,
      cost: reasoningCost,
      barColor: "bg-purple-600 dark:bg-purple-500",
      dotColor: "bg-purple-600 dark:bg-purple-400",
      textColor: "text-purple-700 dark:text-purple-300",
      badgeBg: "bg-purple-50 dark:bg-purple-950/50 text-purple-700 dark:text-purple-300 border-purple-200/80 dark:border-purple-800/60",
    },
    {
      id: "visible",
      label: "Visible Output",
      shortLabel: "Output",
      tokens: tokens.visible || 0,
      pct: visiblePct,
      cost: visibleCost,
      barColor: "bg-emerald-600 dark:bg-emerald-500",
      dotColor: "bg-emerald-600 dark:bg-emerald-400",
      textColor: "text-emerald-700 dark:text-emerald-300",
      badgeBg: "bg-emerald-50 dark:bg-emerald-950/50 text-emerald-700 dark:text-emerald-300 border-emerald-200/80 dark:border-emerald-800/60",
    },
    {
      id: "embeddings",
      label: "Embeddings",
      shortLabel: "Embed",
      tokens: embeddingsTokens,
      pct: embedPct,
      cost: queryEmbedCost + guardrailEmbedCost,
      barColor: "bg-cyan-600 dark:bg-cyan-500",
      dotColor: "bg-cyan-600 dark:bg-cyan-400",
      textColor: "text-cyan-700 dark:text-cyan-300",
      badgeBg: "bg-cyan-50 dark:bg-cyan-950/50 text-cyan-700 dark:text-cyan-300 border-cyan-200/80 dark:border-cyan-800/60",
    },
  ];

  // Dynamically divide bar segments so text is always fully visible with guaranteed minimum width
  const activeCategories = tokenCategories.filter((cat) => cat.tokens > 0);
  const activeCount = activeCategories.length;
  // Dynamic minimum width allocation: ensures every active slice has enough room for its text
  const minPct = activeCount <= 1 ? 100 : Math.max(12, Math.min(22, 52 / activeCount));
  const reservedPct = minPct * activeCount;
  const remainingPct = Math.max(0, 100 - reservedPct);
  const totalActiveTokens =
    activeCategories.reduce((acc, c) => acc + c.tokens, 0) || 1;

  const dynamicBarSegments = activeCategories.map((cat) => {
    const proportionalShare = cat.tokens / totalActiveTokens;
    const dynamicWidthPct = minPct + proportionalShare * remainingPct;
    return {
      ...cat,
      dynamicWidthPct,
    };
  });

  return (
    <DrawerShell
      open
      onClose={onClose}
      title="Single-Query Economic Inspector"
      description={<span className="font-mono">Request ID: {requestId}</span>}
      closeLabel="Close inspector"
      fullViewHref={entityHref({ kind: "trace", id: requestId })}
      bodyClassName="p-6 space-y-6 text-sm"
    >
      {loading && (
        <div className="flex h-48 flex-col items-center justify-center gap-2">
          <RefreshCw className="h-6 w-6 animate-spin text-brand-600" />
          <p className="text-xs text-slate-500">Loading query accounting & traces...</p>
        </div>
      )}

      {error && (
        <ErrorState
          compact
          title="Failed to load query breakdown"
          message={error}
          requestId={requestId}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      )}

      {!loading && !error && detail && (
        <>
          {/* Metadata strip */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 rounded-xl border border-line bg-slate-50/70 dark:bg-dark-card p-3.5 text-xs">
            <div>
              <span className="text-fg-subtle block text-[11px]">Client IP</span>
              <span className="font-mono font-medium text-fg">
                {detail.session?.ip || detail.ip_address || "—"}
              </span>
            </div>
            <div>
              <span className="text-fg-subtle block text-[11px]">Session</span>
              <span
                className="font-medium text-fg truncate block"
                title={detail.session?.title || detail.session_title}
              >
                {detail.session?.title || detail.session_title || detail.session?.id || "—"}
              </span>
            </div>
            <div>
              <span className="text-fg-subtle block text-[11px]">Turn Cost</span>
              <span className="font-mono tabular-nums font-bold text-fg">
                ${(detail.cost_usd ?? totalCalculatedCost).toFixed(4)}
              </span>
            </div>
            <div>
              <span className="text-fg-subtle block text-[11px]">Groundedness</span>
              <span
                className={`inline-flex items-center gap-1 font-semibold text-xs ${
                  detail.grounded ? "text-emerald-700 dark:text-emerald-400" : "text-amber-700 dark:text-amber-400"
                }`}
              >
                <ShieldCheck className="w-3.5 h-3.5" />
                {detail.grounded ? "Verified" : "Ungrounded"}
              </span>
            </div>
          </div>

          {/* Prompt and Answer with Copy actions */}
          <div className="space-y-4">
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">
                  User Prompt
                </span>
                <button
                  type="button"
                  onClick={() => handleCopy(detail.user_query || detail.query, "query")}
                  className="inline-flex items-center gap-1 text-[11px] text-fg-subtle hover:text-slate-800 dark:hover:text-slate-200"
                >
                  {copiedQuery ? (
                    <>
                      <Check className="w-3 h-3 text-emerald-600 dark:text-emerald-400" />
                      <span className="text-emerald-600 dark:text-emerald-400">Copied</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3 h-3" />
                      <span>Copy Prompt</span>
                    </>
                  )}
                </button>
              </div>
              <div className="rounded-xl border border-line bg-slate-50/50 dark:bg-dark-card p-3.5 text-xs text-fg whitespace-pre-wrap font-sans leading-relaxed">
                {detail.user_query || detail.query || "—"}
              </div>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">
                  Assistant Response
                </span>
                <button
                  type="button"
                  onClick={() => handleCopy(detail.assistant_answer || detail.answer, "answer")}
                  className="inline-flex items-center gap-1 text-[11px] text-fg-subtle hover:text-slate-800 dark:hover:text-slate-200"
                >
                  {copiedAnswer ? (
                    <>
                      <Check className="w-3 h-3 text-emerald-600 dark:text-emerald-400" />
                      <span className="text-emerald-600 dark:text-emerald-400">Copied</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3 h-3" />
                      <span>Copy Answer</span>
                    </>
                  )}
                </button>
              </div>
              <div className="rounded-xl border border-teal-100 dark:border-teal-900/40 bg-teal-50/30 dark:bg-teal-950/20 p-3.5 text-xs text-fg whitespace-pre-wrap font-sans leading-relaxed max-h-60 overflow-y-auto">
                {detail.assistant_answer || detail.answer || "—"}
              </div>
            </div>
          </div>

          {/* Token Allocation Bar */}
          <div className="space-y-2.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">
                Token Budget Allocation ({(tokens.total_billed || totalBilledTokens).toLocaleString()} billed tokens)
              </span>
              <span className="text-[11px] font-mono text-fg-subtle">
                100% of Turn Volume
              </span>
            </div>

            {/* Progress Bar Line Containing Dynamically Divided Text */}
            <div className="h-7 w-full overflow-hidden rounded-lg bg-slate-100 dark:bg-slate-800 p-0.5 flex gap-0.5 border border-line shadow-inner">
              {dynamicBarSegments.map((cat) => (
                <div
                  key={cat.id}
                  style={{
                    width: `${cat.dynamicWidthPct}%`,
                  }}
                  className={`${cat.barColor} h-full flex items-center justify-center rounded-[3px] transition-all hover:brightness-110 cursor-pointer overflow-hidden px-1.5`}
                  title={`${cat.label}: ${cat.tokens.toLocaleString()} tokens (${cat.pct.toFixed(1)}%) · $${cat.cost.toFixed(5)}`}
                >
                  <span className="truncate text-[11px] font-semibold text-white tracking-tight select-none">
                    {cat.shortLabel}: {cat.tokens.toLocaleString()}
                    {cat.dynamicWidthPct >= 24 ? ` (${cat.pct.toFixed(1)}%)` : ""}
                  </span>
                </div>
              ))}
            </div>

            {/* Clean Inline Legend Strip */}
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 pt-0.5 text-xs text-fg-muted">
              {tokenCategories.map((cat) => (
                <div key={cat.id} className="flex items-center gap-1.5">
                  <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${cat.dotColor}`} />
                  <span className="font-medium text-fg">{cat.label}:</span>
                  <span className="font-mono font-bold text-fg tabular-nums">
                    {cat.tokens.toLocaleString()}
                  </span>
                  <span className="text-[11px] font-medium text-fg-subtle">
                    ({cat.pct.toFixed(1)}%)
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Mathematical Pricing Breakdown Table using Dynamic Rate Card */}
          <div className="space-y-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-fg-subtle block">
              Stage-by-Stage Mathematical Pricing (Rate Card)
            </span>
            <div className="overflow-hidden rounded-xl border border-line">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 dark:bg-dark-card text-fg-muted uppercase text-[10px] border-b border-line">
                  <tr>
                    <th className="px-3.5 py-2 font-medium">Stage</th>
                    <th className="px-3.5 py-2 font-medium text-right">Tokens</th>
                    <th className="px-3.5 py-2 font-medium text-right">Rate / 1M</th>
                    <th className="px-3.5 py-2 font-medium text-right">Cost (USD)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/70">
                  <tr>
                    <td className="px-3.5 py-2 font-medium text-fg">1. Query Embedding</td>
                    <td className="px-3.5 py-2 text-right font-mono tabular-nums text-fg-muted">
                      {(tokens.query_embed || 0).toLocaleString()}
                    </td>
                    <td className="px-3.5 py-2 text-right font-mono tabular-nums text-fg-subtle">
                      ${pricing.price_embed_per_million.toFixed(2)}
                    </td>
                    <td className="px-3.5 py-2 text-right font-mono tabular-nums font-medium text-fg">
                      ${queryEmbedCost.toFixed(5)}
                    </td>
                  </tr>
                  <tr>
                    <td className="px-3.5 py-2 font-medium text-fg">2. Prompt Context (Uncached)</td>
                    <td className="px-3.5 py-2 text-right font-mono tabular-nums text-fg-muted">
                      {(tokens.prompt || 0).toLocaleString()}
                    </td>
                    <td className="px-3.5 py-2 text-right font-mono tabular-nums text-fg-subtle">
                      ${pricing.price_input_per_million.toFixed(2)}
                    </td>
                    <td className="px-3.5 py-2 text-right font-mono tabular-nums font-medium text-fg">
                      ${promptCost.toFixed(5)}
                    </td>
                  </tr>
                  {tokens.cached_prompt > 0 && (
                    <tr>
                      <td className="px-3.5 py-2 font-medium text-teal-800 dark:text-teal-400">2b. Cached Context</td>
                      <td className="px-3.5 py-2 text-right font-mono tabular-nums text-teal-700 dark:text-teal-300">
                        {tokens.cached_prompt.toLocaleString()}
                      </td>
                      <td className="px-3.5 py-2 text-right font-mono tabular-nums text-fg-subtle">
                        ${(pricing.price_cached_per_million || 0.0625).toFixed(4)}
                      </td>
                      <td className="px-3.5 py-2 text-right font-mono tabular-nums font-medium text-teal-800 dark:text-teal-300">
                        ${cachedCost.toFixed(5)}
                      </td>
                    </tr>
                  )}
                  <tr>
                    <td className="px-3.5 py-2 font-medium text-fg">3. LLM Reasoning</td>
                    <td className="px-3.5 py-2 text-right font-mono tabular-nums text-fg-muted">
                      {(tokens.reasoning || 0).toLocaleString()}
                    </td>
                    <td className="px-3.5 py-2 text-right font-mono tabular-nums text-fg-subtle">
                      ${pricing.price_output_per_million.toFixed(2)}
                    </td>
                    <td className="px-3.5 py-2 text-right font-mono tabular-nums font-medium text-fg">
                      ${reasoningCost.toFixed(5)}
                    </td>
                  </tr>
                  <tr>
                    <td className="px-3.5 py-2 font-medium text-fg">4. Visible Output</td>
                    <td className="px-3.5 py-2 text-right font-mono tabular-nums text-fg-muted">
                      {(tokens.visible || 0).toLocaleString()}
                    </td>
                    <td className="px-3.5 py-2 text-right font-mono tabular-nums text-fg-subtle">
                      ${pricing.price_output_per_million.toFixed(2)}
                    </td>
                    <td className="px-3.5 py-2 text-right font-mono tabular-nums font-medium text-fg">
                      ${visibleCost.toFixed(5)}
                    </td>
                  </tr>
                  <tr>
                    <td className="px-3.5 py-2 font-medium text-fg">5. Guardrail Embedding</td>
                    <td className="px-3.5 py-2 text-right font-mono tabular-nums text-fg-muted">
                      {(tokens.guardrail_embed || 0).toLocaleString()}
                    </td>
                    <td className="px-3.5 py-2 text-right font-mono tabular-nums text-fg-subtle">
                      ${pricing.price_embed_per_million.toFixed(2)}
                    </td>
                    <td className="px-3.5 py-2 text-right font-mono tabular-nums font-medium text-fg">
                      ${guardrailEmbedCost.toFixed(5)}
                    </td>
                  </tr>
                  <tr className="bg-slate-50 dark:bg-dark-card font-semibold text-fg">
                    <td className="px-3.5 py-2.5">Total Exact Turn Billed</td>
                    <td className="px-3.5 py-2.5 text-right font-mono tabular-nums">
                      {(tokens.total_billed || 0).toLocaleString()}
                    </td>
                    <td className="px-3.5 py-2.5 text-right text-slate-400">—</td>
                    <td className="px-3.5 py-2.5 text-right font-mono tabular-nums text-emerald-700 dark:text-emerald-400">
                      ${(detail.cost_usd ?? totalCalculatedCost).toFixed(4)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          {/* Latency Breakdown */}
          {detail.latency && (
            <div className="space-y-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-fg-subtle flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5" />
                Turn Latency Stages ({detail.latency.total_ms ?? "—"} ms total)
              </span>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-xs">
                <div className="rounded-lg border border-line bg-slate-50 dark:bg-dark-card p-2.5">
                  <span className="text-fg-subtle block text-[11px]">Retrieval & Search</span>
                  <span className="font-semibold text-fg font-mono tabular-nums">
                    {detail.latency.retrieval_ms ?? "—"} ms
                  </span>
                </div>
                <div className="rounded-lg border border-line bg-slate-50 dark:bg-dark-card p-2.5">
                  <span className="text-fg-subtle block text-[11px]">Guardrails Pre</span>
                  <span className="font-semibold text-fg font-mono tabular-nums">
                    {detail.latency.guardrail_pre_ms ?? detail.latency.guardrails_ms ?? "—"} ms
                  </span>
                </div>
                <div className="rounded-lg border border-line bg-slate-50 dark:bg-dark-card p-2.5">
                  <span className="text-fg-subtle block text-[11px]">LLM Generation</span>
                  <span className="font-semibold text-fg font-mono tabular-nums">
                    {detail.latency.llm_generation_ms ?? detail.latency.llm_ms ?? "—"} ms
                  </span>
                </div>
                <div className="rounded-lg border border-line bg-slate-50 dark:bg-dark-card p-2.5">
                  <span className="text-fg-subtle block text-[11px]">Guardrails Post</span>
                  <span className="font-semibold text-fg font-mono tabular-nums">
                    {detail.latency.guardrail_post_ms ?? "—"} ms
                  </span>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </DrawerShell>
  );
}
