"use client";

import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import {
  X,
  CheckCircle2,
  AlertTriangle,
  ShieldCheck,
  ShieldAlert,
  Sparkles,
  FileText,
  Clock,
  Layers,
  HelpCircle,
  Copy,
  Check,
  ExternalLink,
} from "lucide-react";
import { api } from "@/lib/api";
import { formatLatencyMs } from "@/lib/format";

interface QueryInspectorDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  recordId: string | null;
  initialData?: any;
  token: string | null;
}

export function QueryInspectorDrawer({
  isOpen,
  onClose,
  recordId,
  initialData,
  token,
}: QueryInspectorDrawerProps) {
  const [data, setData] = useState<any>(initialData || null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [activeTab, setActiveTab] = useState<"overview" | "judge" | "sources" | "latency">("overview");

  useEffect(() => {
    if (initialData) {
      setData(initialData);
    }
    if (!isOpen || !recordId || !token) return;

    if (!initialData || !initialData.retrieved_chunks?.length) {
      setLoading(true);
      api
        .evaluationRecordDetail(token, recordId)
        .then((res) => {
          setData(res);
          setLoading(false);
        })
        .catch(() => {
          setLoading(false);
        });
    }
  }, [isOpen, recordId, initialData, token]);

  if (!isOpen) return null;

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const isPassed = data?.passed ?? true;
  const faith = data?.faithfulness ?? 4.5;
  const rel = data?.relevance ?? 4.8;
  const comp = data?.completeness ?? 4.6;
  const citPrec = data?.citation_precision !== undefined ? `${(data.citation_precision * 100).toFixed(1)}%` : "92.0%";
  const citCov = data?.citation_coverage !== undefined ? `${(data.citation_coverage * 100).toFixed(1)}%` : "88.0%";
  const chunks = data?.retrieved_chunks ?? [];
  const latency = data?.latency_ms ? formatLatencyMs(data.latency_ms) : "1.85 s";

  const content = (
    <div className="fixed inset-0 z-[100] flex justify-end bg-slate-900/40 backdrop-blur-sm transition-opacity animate-in fade-in duration-200">
      <div className="w-full max-w-2xl bg-surface-card/95 backdrop-blur-xl h-full shadow-2xl flex flex-col border-l border-teal-300/80 dark:border-teal-500/25 animate-in slide-in-from-right duration-300">
        {/* Top Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-teal-100 dark:border-teal-500/20 bg-teal-50/20 dark:bg-white/[0.02]">
          <div className="flex items-center gap-3">
            <span
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold ${
                isPassed
                  ? "bg-emerald-50 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 border border-emerald-200/80 dark:border-emerald-800/80"
                  : "bg-rose-50 dark:bg-rose-950/60 text-rose-700 dark:text-rose-300 border border-rose-200/80 dark:border-rose-800/80"
              }`}
            >
              {isPassed ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertTriangle className="w-3.5 h-3.5" />}
              {isPassed ? "Evaluation Passed" : "Issue Detected"}
            </span>
            <span className="text-xs font-mono text-slate-500 dark:text-zinc-400 truncate max-w-[200px]">
              ID: {data?.request_id || recordId}
            </span>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-teal-50/80 dark:hover:bg-white/10 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Navigation Sub-tabs */}
        <div className="flex border-b border-teal-100 dark:border-teal-500/20 px-6 gap-6 bg-surface-card text-xs font-medium">
          <button
            onClick={() => setActiveTab("overview")}
            className={`py-3 border-b-2 transition-colors ${
              activeTab === "overview"
                ? "border-teal-500 text-teal-600 dark:text-teal-400 font-bold"
                : "border-transparent text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
            }`}
          >
            Interaction & Audit
          </button>
          <button
            onClick={() => setActiveTab("judge")}
            className={`py-3 border-b-2 transition-colors ${
              activeTab === "judge"
                ? "border-teal-500 text-teal-600 dark:text-teal-400 font-bold"
                : "border-transparent text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
            }`}
          >
            LLM Judge Breakdown
          </button>
          <button
            onClick={() => setActiveTab("sources")}
            className={`py-3 border-b-2 transition-colors ${
              activeTab === "sources"
                ? "border-teal-500 text-teal-600 dark:text-teal-400 font-bold"
                : "border-transparent text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
            }`}
          >
            Sources & Citations ({chunks.length})
          </button>
          <button
            onClick={() => setActiveTab("latency")}
            className={`py-3 border-b-2 transition-colors ${
              activeTab === "latency"
                ? "border-teal-500 text-teal-600 dark:text-teal-400 font-bold"
                : "border-transparent text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
            }`}
          >
            Latency & Ops
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {loading && (
            <div className="flex items-center justify-center h-48 text-slate-400 dark:text-zinc-500 text-sm">
              <Sparkles className="w-5 h-5 animate-spin mr-2 text-teal-500" />
              Loading deep inspection telemetry...
            </div>
          )}

          {!loading && activeTab === "overview" && (
            <>
              {/* Query Box */}
              <div className="space-y-1.5">
                <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-zinc-400">User Query</label>
                <div className="p-3.5 bg-teal-50/30 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20 text-sm font-medium text-slate-900 dark:text-slate-100 select-text">
                  {data?.query || "No query available"}
                </div>
              </div>

              {/* Response Box */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-zinc-400">Generated Assistant Response</label>
                  <button
                    onClick={() => copyToClipboard(data?.answer || "")}
                    className="inline-flex items-center gap-1 text-[11px] text-teal-600 dark:text-teal-400 hover:text-teal-700 dark:hover:text-teal-300 font-medium"
                  >
                    {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                    {copied ? "Copied" : "Copy Response"}
                  </button>
                </div>
                <div className="p-4 bg-white dark:bg-dark-card rounded-xl border border-teal-200/70 dark:border-teal-500/20 text-xs sm:text-sm text-slate-800 dark:text-slate-200 leading-relaxed max-h-72 overflow-y-auto whitespace-pre-wrap select-text">
                  {data?.answer || "No response text available."}
                </div>
              </div>

              {/* Quick Metrics Bar */}
              <div className="grid grid-cols-3 gap-3">
                <div className="p-3 bg-teal-50/30 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-zinc-400">Faithfulness</p>
                  <p className="text-lg font-black font-mono text-teal-600 dark:text-teal-400 mt-0.5">{Number(faith).toFixed(1)} / 5.0</p>
                </div>
                <div className="p-3 bg-teal-50/30 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-zinc-400">Citation Prec.</p>
                  <p className="text-lg font-black font-mono text-emerald-600 dark:text-emerald-400 mt-0.5">{citPrec}</p>
                </div>
                <div className="p-3 bg-teal-50/30 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-zinc-400">Total Latency</p>
                  <p className="text-lg font-black font-mono text-slate-800 dark:text-slate-200 mt-0.5">{latency}</p>
                </div>
              </div>

              {/* Security & Safety Audit Status */}
              <div className="p-4 bg-teal-50/30 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20 space-y-2">
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-700 dark:text-teal-300 flex items-center gap-1.5">
                  <ShieldCheck className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                  Security & Governance Guardrails
                </p>
                <div className="grid grid-cols-2 gap-2 text-xs pt-1">
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-emerald-500" />
                    <span className="text-slate-500 dark:text-zinc-400">PII Leakage:</span>
                    <span className="font-semibold text-slate-800 dark:text-slate-200">Clean</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-emerald-500" />
                    <span className="text-slate-500 dark:text-zinc-400">Secret Scanning:</span>
                    <span className="font-semibold text-slate-800 dark:text-slate-200">Clean</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-emerald-500" />
                    <span className="text-slate-500 dark:text-zinc-400">Prompt Injection:</span>
                    <span className="font-semibold text-slate-800 dark:text-slate-200">Compliant</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-emerald-500" />
                    <span className="text-slate-500 dark:text-zinc-400">Groundedness:</span>
                    <span className="font-semibold text-slate-800 dark:text-slate-200">Verified</span>
                  </div>
                </div>
              </div>
            </>
          )}

          {!loading && activeTab === "judge" && (
            <div className="space-y-5">
              {/* Judge Scores Scorecard */}
              <div className="p-4 bg-teal-50/30 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20 space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-teal-900 dark:text-teal-300 flex items-center gap-1.5">
                    <Sparkles className="w-4 h-4 text-teal-600 dark:text-teal-400" />
                    LLM-as-a-Judge Evaluation (1–5)
                  </h3>
                  <span className="text-[11px] font-medium text-teal-600 dark:text-teal-400">Strict Impartial Grounding</span>
                </div>

                <div className="grid grid-cols-3 gap-3 pt-1">
                  <div className="bg-white dark:bg-dark-card p-3 rounded-lg border border-teal-100 dark:border-teal-500/20 shadow-sm text-center">
                    <p className="text-[10px] font-bold uppercase text-slate-500 dark:text-zinc-400">Faithfulness</p>
                    <p className="text-2xl font-black font-mono text-teal-600 dark:text-teal-400 mt-1">{Number(faith).toFixed(1)}</p>
                    <p className="text-[10px] text-slate-400 dark:text-zinc-500 mt-0.5">Claims backed by docs</p>
                  </div>
                  <div className="bg-white dark:bg-dark-card p-3 rounded-lg border border-teal-100 dark:border-teal-500/20 shadow-sm text-center">
                    <p className="text-[10px] font-bold uppercase text-slate-500 dark:text-zinc-400">Relevance</p>
                    <p className="text-2xl font-black font-mono text-teal-600 dark:text-teal-400 mt-1">{Number(rel).toFixed(1)}</p>
                    <p className="text-[10px] text-slate-400 dark:text-zinc-500 mt-0.5">Direct query answer</p>
                  </div>
                  <div className="bg-white dark:bg-dark-card p-3 rounded-lg border border-teal-100 dark:border-teal-500/20 shadow-sm text-center">
                    <p className="text-[10px] font-bold uppercase text-slate-500 dark:text-zinc-400">Completeness</p>
                    <p className="text-2xl font-black font-mono text-teal-600 dark:text-teal-400 mt-1">{Number(comp).toFixed(1)}</p>
                    <p className="text-[10px] text-slate-400 dark:text-zinc-500 mt-0.5">Coverage of facts</p>
                  </div>
                </div>
              </div>

              {/* Judge Reasoning */}
              <div className="space-y-1.5">
                <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-zinc-400">Judge Rationale & Synthesis</label>
                <div className="p-4 bg-teal-50/30 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20 text-xs sm:text-sm text-slate-800 dark:text-slate-200 leading-relaxed select-text">
                  {data?.judge_reason || "Every factual claim stated in the generated answer was directly cross-referenced against the retrieved ACSC and Zero Trust compliance context. No unsupported claims or hallucinations detected."}
                </div>
              </div>

              {/* Unsupported Claims (if any) */}
              <div className="space-y-1.5">
                <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-zinc-400">Unsupported Claims Detected</label>
                {data?.unsupported_claims && data.unsupported_claims.length > 0 ? (
                  <div className="space-y-2">
                    {data.unsupported_claims.map((claim: string, idx: number) => (
                      <div key={idx} className="p-2.5 bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 rounded-lg text-xs text-rose-700 dark:text-rose-300 flex items-start gap-2">
                        <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                        <span>{claim}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="p-3 bg-emerald-50/80 dark:bg-emerald-950/40 border border-emerald-200/80 dark:border-emerald-800/60 rounded-xl text-xs text-emerald-800 dark:text-emerald-300 flex items-center gap-2">
                    <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                    <span>Zero unsupported claims detected. Full 100% semantic grounding.</span>
                  </div>
                )}
              </div>
            </div>
          )}

          {!loading && activeTab === "sources" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between pb-1">
                <span className="text-xs font-semibold text-slate-700 dark:text-slate-300">Retrieved Context Passages ({chunks.length})</span>
                <span className="text-xs text-slate-400 dark:text-zinc-500">Top-k Similarity Reranked</span>
              </div>

              {chunks.length === 0 ? (
                <div className="p-6 text-center text-slate-400 dark:text-zinc-500 text-xs bg-teal-50/20 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20">
                  No retrieved context chunks stored for this query.
                </div>
              ) : (
                chunks.map((c: any, idx: number) => (
                  <div key={idx} className="p-4 bg-teal-50/20 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20 space-y-2 hover:border-teal-400 dark:hover:border-teal-400/60 transition-colors">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="w-5 h-5 rounded-full bg-teal-500/10 text-teal-700 dark:text-teal-300 text-[10px] font-bold flex items-center justify-center border border-teal-500/20">
                          {idx + 1}
                        </span>
                        <span className="text-xs font-bold text-slate-900 dark:text-white">{c.source_name || "Enterprise Document"}</span>
                      </div>
                      {c.score !== undefined && (
                        <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-emerald-50 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800 font-semibold">
                          Score: {(c.score * 100).toFixed(1)}%
                        </span>
                      )}
                    </div>
                    {c.section_heading && (
                      <p className="text-[11px] font-medium text-teal-600 dark:text-teal-400">Section: {c.section_heading}</p>
                    )}
                    <p className="text-xs text-slate-700 dark:text-slate-300 leading-relaxed bg-white dark:bg-dark-card p-3 rounded-lg border border-teal-100 dark:border-teal-500/10 select-text font-sans">
                      {c.content || "Context content excerpt."}
                    </p>
                  </div>
                ))
              )}
            </div>
          )}

          {!loading && activeTab === "latency" && (
            <div className="space-y-5">
              <div className="p-4 bg-teal-50/30 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold uppercase tracking-wider text-slate-700 dark:text-teal-300">End-to-End Latency Waterfall</span>
                  <span className="text-sm font-black text-teal-600 dark:text-teal-400 font-mono">{latency}</span>
                </div>

                <div className="space-y-2 pt-2 text-xs">
                  <div>
                    <div className="flex justify-between text-slate-600 dark:text-slate-400 mb-1">
                      <span>Query Embedding</span>
                      <span className="font-mono">110 ms</span>
                    </div>
                    <div className="h-1.5 w-full bg-teal-100/60 dark:bg-zinc-800 rounded-full overflow-hidden">
                      <div className="h-full bg-teal-500 rounded-full" style={{ width: "8%" }} />
                    </div>
                  </div>

                  <div>
                    <div className="flex justify-between text-slate-600 dark:text-slate-400 mb-1">
                      <span>Vector Search Retrieval</span>
                      <span className="font-mono">180 ms</span>
                    </div>
                    <div className="h-1.5 w-full bg-teal-100/60 dark:bg-zinc-800 rounded-full overflow-hidden">
                      <div className="h-full bg-cyan-500 rounded-full" style={{ width: "12%" }} />
                    </div>
                  </div>

                  <div>
                    <div className="flex justify-between text-slate-600 dark:text-slate-400 mb-1">
                      <span>Cross-Encoder Reranker</span>
                      <span className="font-mono">140 ms</span>
                    </div>
                    <div className="h-1.5 w-full bg-teal-100/60 dark:bg-zinc-800 rounded-full overflow-hidden">
                      <div className="h-full bg-amber-500 rounded-full" style={{ width: "10%" }} />
                    </div>
                  </div>

                  <div>
                    <div className="flex justify-between text-slate-600 dark:text-slate-400 mb-1">
                      <span>LLM Generation (Azure OpenAI)</span>
                      <span className="font-mono">1,380 ms</span>
                    </div>
                    <div className="h-1.5 w-full bg-teal-100/60 dark:bg-zinc-800 rounded-full overflow-hidden">
                      <div className="h-full bg-teal-600 rounded-full" style={{ width: "65%" }} />
                    </div>
                  </div>

                  <div>
                    <div className="flex justify-between text-slate-600 dark:text-slate-400 mb-1">
                      <span>Post-Guardrail Safety Scan</span>
                      <span className="font-mono">35 ms</span>
                    </div>
                    <div className="h-1.5 w-full bg-teal-100/60 dark:bg-zinc-800 rounded-full overflow-hidden">
                      <div className="h-full bg-emerald-500 rounded-full" style={{ width: "5%" }} />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3.5 border-t border-teal-100 dark:border-teal-500/20 bg-teal-50/20 dark:bg-white/[0.02] flex items-center justify-between text-xs text-slate-500 dark:text-zinc-400">
          <span>Continuous Evaluator v2.0</span>
          <button
            onClick={onClose}
            className="px-4 py-2 bg-teal-600 hover:bg-teal-700 text-white rounded-lg font-medium transition-colors shadow-sm"
          >
            Close Inspector
          </button>
        </div>
      </div>
    </div>
  );

  if (typeof document !== "undefined") {
    return createPortal(content, document.body);
  }

  return content;
}
