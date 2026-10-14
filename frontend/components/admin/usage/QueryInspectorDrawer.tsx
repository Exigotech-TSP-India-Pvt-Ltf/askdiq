"use client";

import React, { useEffect, useRef, useState } from "react";
import { X, RefreshCw, Copy, Check, Clock, ShieldCheck, Zap } from "lucide-react";
import { api, ApiError } from "@/lib/api";

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

  const drawerRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const triggerRef = useRef<Element | null>(null);

  // Capture trigger element to restore focus on unmount
  useEffect(() => {
    triggerRef.current = document.activeElement;
    closeButtonRef.current?.focus();
    return () => {
      if (triggerRef.current instanceof HTMLElement) {
        triggerRef.current.focus();
      }
    };
  }, []);

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
  }, [token, requestId]);

  // Esc key & Focus trap
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }

      if (e.key === "Tab" && drawerRef.current) {
        const focusableElements = drawerRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (focusableElements.length === 0) return;
        const first = focusableElements[0];
        const last = focusableElements[focusableElements.length - 1];

        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

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

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="inspector-title"
      className="fixed inset-0 z-50 flex justify-end bg-slate-900/40 backdrop-blur-sm transition-opacity"
      onClick={onClose}
    >
      <div
        ref={drawerRef}
        onClick={(e) => e.stopPropagation()}
        className="flex h-full w-full max-w-2xl flex-col bg-white shadow-2xl overflow-hidden"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4 bg-slate-50/50">
          <div>
            <h2 id="inspector-title" className="text-sm font-semibold text-slate-900">
              Single-Query Economic Inspector
            </h2>
            <p className="text-xs text-slate-500 font-mono">Request ID: {requestId}</p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition"
            aria-label="Close inspector"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6 text-sm">
          {loading && (
            <div className="flex h-48 flex-col items-center justify-center gap-2">
              <RefreshCw className="h-6 w-6 animate-spin text-brand-indigo" />
              <p className="text-xs text-slate-500">Loading query accounting & traces...</p>
            </div>
          )}

          {error && (
            <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-xs text-rose-700">
              <p className="font-semibold">Failed to load query breakdown</p>
              <p>{error}</p>
            </div>
          )}

          {!loading && !error && detail && (
            <>
              {/* Metadata strip */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 rounded-xl border border-slate-200 bg-slate-50/70 p-3.5 text-xs">
                <div>
                  <span className="text-slate-500 block text-[11px]">Client IP</span>
                  <span className="font-mono font-medium text-slate-800">
                    {detail.session?.ip || detail.ip_address || "—"}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block text-[11px]">Session</span>
                  <span
                    className="font-medium text-slate-800 truncate block"
                    title={detail.session?.title || detail.session_title}
                  >
                    {detail.session?.title || detail.session_title || detail.session?.id || "—"}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block text-[11px]">Turn Cost</span>
                  <span className="font-mono font-bold text-slate-900">
                    ${(detail.cost_usd ?? totalCalculatedCost).toFixed(4)}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 block text-[11px]">Groundedness</span>
                  <span
                    className={`inline-flex items-center gap-1 font-semibold text-xs ${
                      detail.grounded ? "text-emerald-700" : "text-amber-700"
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
                    <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                      User Prompt
                    </span>
                    <button
                      type="button"
                      onClick={() => handleCopy(detail.user_query || detail.query, "query")}
                      className="inline-flex items-center gap-1 text-[11px] text-slate-500 hover:text-slate-800"
                    >
                      {copiedQuery ? (
                        <>
                          <Check className="w-3 h-3 text-emerald-600" />
                          <span className="text-emerald-600">Copied</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-3 h-3" />
                          <span>Copy Prompt</span>
                        </>
                      )}
                    </button>
                  </div>
                  <div className="rounded-xl border border-slate-200 bg-slate-50/50 p-3.5 text-xs text-slate-800 whitespace-pre-wrap font-sans leading-relaxed">
                    {detail.user_query || detail.query || "—"}
                  </div>
                </div>

                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                      Assistant Response
                    </span>
                    <button
                      type="button"
                      onClick={() => handleCopy(detail.assistant_answer || detail.answer, "answer")}
                      className="inline-flex items-center gap-1 text-[11px] text-slate-500 hover:text-slate-800"
                    >
                      {copiedAnswer ? (
                        <>
                          <Check className="w-3 h-3 text-emerald-600" />
                          <span className="text-emerald-600">Copied</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-3 h-3" />
                          <span>Copy Answer</span>
                        </>
                      )}
                    </button>
                  </div>
                  <div className="rounded-xl border border-indigo-100 bg-indigo-50/30 p-3.5 text-xs text-slate-900 whitespace-pre-wrap font-sans leading-relaxed max-h-60 overflow-y-auto">
                    {detail.assistant_answer || detail.answer || "—"}
                  </div>
                </div>
              </div>

              {/* Token Allocation Bar */}
              <div className="space-y-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500 block">
                  Token Budget Allocation ({(tokens.total_billed || 0).toLocaleString()} billed tokens)
                </span>
                <div className="h-4 w-full overflow-hidden rounded-md bg-slate-100 flex text-[10px] font-semibold text-white">
                  {tokens.prompt > 0 && (
                    <div
                      style={{
                        width: `${Math.max(5, (tokens.prompt / (tokens.total_billed || 1)) * 100)}%`,
                      }}
                      className="bg-indigo-600 flex items-center justify-center truncate px-1"
                      title={`Prompt Context: ${tokens.prompt.toLocaleString()}`}
                    >
                      Prompt
                    </div>
                  )}
                  {tokens.cached_prompt > 0 && (
                    <div
                      style={{
                        width: `${Math.max(5, (tokens.cached_prompt / (tokens.total_billed || 1)) * 100)}%`,
                      }}
                      className="bg-teal-600 flex items-center justify-center truncate px-1"
                      title={`Cached Prompt: ${tokens.cached_prompt.toLocaleString()}`}
                    >
                      Cached
                    </div>
                  )}
                  {tokens.reasoning > 0 && (
                    <div
                      style={{
                        width: `${Math.max(5, (tokens.reasoning / (tokens.total_billed || 1)) * 100)}%`,
                      }}
                      className="bg-purple-600 flex items-center justify-center truncate px-1"
                      title={`Reasoning: ${tokens.reasoning.toLocaleString()}`}
                    >
                      Reasoning
                    </div>
                  )}
                  {tokens.visible > 0 && (
                    <div
                      style={{
                        width: `${Math.max(5, (tokens.visible / (tokens.total_billed || 1)) * 100)}%`,
                      }}
                      className="bg-emerald-600 flex items-center justify-center truncate px-1"
                      title={`Visible Output: ${tokens.visible.toLocaleString()}`}
                    >
                      Output
                    </div>
                  )}
                  {(tokens.query_embed > 0 || tokens.guardrail_embed > 0) && (
                    <div
                      style={{
                        width: `${Math.max(
                          3,
                          (((tokens.query_embed || 0) + (tokens.guardrail_embed || 0)) /
                            (tokens.total_billed || 1)) *
                            100
                        )}%`,
                      }}
                      className="bg-cyan-600 flex items-center justify-center truncate px-1"
                      title="Embeddings"
                    >
                      Embed
                    </div>
                  )}
                </div>
              </div>

              {/* Mathematical Pricing Breakdown Table using Dynamic Rate Card */}
              <div className="space-y-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-500 block">
                  Stage-by-Stage Mathematical Pricing (Rate Card)
                </span>
                <div className="overflow-hidden rounded-xl border border-slate-200">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-slate-50 text-slate-600 uppercase text-[10px]">
                      <tr>
                        <th className="px-3.5 py-2 font-medium">Stage</th>
                        <th className="px-3.5 py-2 font-medium">Tokens</th>
                        <th className="px-3.5 py-2 font-medium">Rate / 1M</th>
                        <th className="px-3.5 py-2 font-medium text-right">Cost (USD)</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      <tr>
                        <td className="px-3.5 py-2 font-medium text-slate-800">1. Query Embedding</td>
                        <td className="px-3.5 py-2 font-mono text-slate-600">
                          {(tokens.query_embed || 0).toLocaleString()}
                        </td>
                        <td className="px-3.5 py-2 text-slate-500">
                          ${pricing.price_embed_per_million.toFixed(2)}
                        </td>
                        <td className="px-3.5 py-2 text-right font-mono font-medium text-slate-800">
                          ${queryEmbedCost.toFixed(5)}
                        </td>
                      </tr>
                      <tr>
                        <td className="px-3.5 py-2 font-medium text-slate-800">2. Prompt Context (Uncached)</td>
                        <td className="px-3.5 py-2 font-mono text-slate-600">
                          {(tokens.prompt || 0).toLocaleString()}
                        </td>
                        <td className="px-3.5 py-2 text-slate-500">
                          ${pricing.price_input_per_million.toFixed(2)}
                        </td>
                        <td className="px-3.5 py-2 text-right font-mono font-medium text-slate-800">
                          ${promptCost.toFixed(5)}
                        </td>
                      </tr>
                      {tokens.cached_prompt > 0 && (
                        <tr>
                          <td className="px-3.5 py-2 font-medium text-teal-800">2b. Cached Context</td>
                          <td className="px-3.5 py-2 font-mono text-teal-700">
                            {tokens.cached_prompt.toLocaleString()}
                          </td>
                          <td className="px-3.5 py-2 text-slate-500">
                            ${(pricing.price_cached_per_million || 0.0625).toFixed(4)}
                          </td>
                          <td className="px-3.5 py-2 text-right font-mono font-medium text-teal-800">
                            ${cachedCost.toFixed(5)}
                          </td>
                        </tr>
                      )}
                      <tr>
                        <td className="px-3.5 py-2 font-medium text-slate-800">3. LLM Reasoning</td>
                        <td className="px-3.5 py-2 font-mono text-slate-600">
                          {(tokens.reasoning || 0).toLocaleString()}
                        </td>
                        <td className="px-3.5 py-2 text-slate-500">
                          ${pricing.price_output_per_million.toFixed(2)}
                        </td>
                        <td className="px-3.5 py-2 text-right font-mono font-medium text-slate-800">
                          ${reasoningCost.toFixed(5)}
                        </td>
                      </tr>
                      <tr>
                        <td className="px-3.5 py-2 font-medium text-slate-800">4. Visible Output</td>
                        <td className="px-3.5 py-2 font-mono text-slate-600">
                          {(tokens.visible || 0).toLocaleString()}
                        </td>
                        <td className="px-3.5 py-2 text-slate-500">
                          ${pricing.price_output_per_million.toFixed(2)}
                        </td>
                        <td className="px-3.5 py-2 text-right font-mono font-medium text-slate-800">
                          ${visibleCost.toFixed(5)}
                        </td>
                      </tr>
                      <tr>
                        <td className="px-3.5 py-2 font-medium text-slate-800">5. Guardrail Embedding</td>
                        <td className="px-3.5 py-2 font-mono text-slate-600">
                          {(tokens.guardrail_embed || 0).toLocaleString()}
                        </td>
                        <td className="px-3.5 py-2 text-slate-500">
                          ${pricing.price_embed_per_million.toFixed(2)}
                        </td>
                        <td className="px-3.5 py-2 text-right font-mono font-medium text-slate-800">
                          ${guardrailEmbedCost.toFixed(5)}
                        </td>
                      </tr>
                      <tr className="bg-slate-50 font-semibold text-slate-900">
                        <td className="px-3.5 py-2.5">Total Exact Turn Billed</td>
                        <td className="px-3.5 py-2.5 font-mono">
                          {(tokens.total_billed || 0).toLocaleString()}
                        </td>
                        <td className="px-3.5 py-2.5 text-slate-400">—</td>
                        <td className="px-3.5 py-2.5 text-right font-mono text-emerald-700">
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
                  <span className="text-xs font-semibold uppercase tracking-wider text-slate-500 flex items-center gap-1.5">
                    <Clock className="w-3.5 h-3.5" />
                    Turn Latency Stages ({detail.latency.total_ms ?? "—"} ms total)
                  </span>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-xs">
                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-2.5">
                      <span className="text-slate-500 block text-[11px]">Retrieval & Search</span>
                      <span className="font-semibold text-slate-900 font-mono">
                        {detail.latency.retrieval_ms ?? "—"} ms
                      </span>
                    </div>
                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-2.5">
                      <span className="text-slate-500 block text-[11px]">Guardrails Pre</span>
                      <span className="font-semibold text-slate-900 font-mono">
                        {detail.latency.guardrail_pre_ms ?? detail.latency.guardrails_ms ?? "—"} ms
                      </span>
                    </div>
                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-2.5">
                      <span className="text-slate-500 block text-[11px]">LLM Generation</span>
                      <span className="font-semibold text-slate-900 font-mono">
                        {detail.latency.llm_generation_ms ?? detail.latency.llm_ms ?? "—"} ms
                      </span>
                    </div>
                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-2.5">
                      <span className="text-slate-500 block text-[11px]">Guardrails Post</span>
                      <span className="font-semibold text-slate-900 font-mono">
                        {detail.latency.guardrail_post_ms ?? "—"} ms
                      </span>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
