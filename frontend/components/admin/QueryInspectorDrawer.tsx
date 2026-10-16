"use client";

import React, { useEffect, useState } from "react";
import {
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
import { entityHref } from "@/lib/entity-links";
import { DrawerShell } from "@/components/ui/DrawerShell";
import { CopyButton } from "@/components/ui/CopyButton";

type GuardrailTone = "ok" | "bad" | "unknown";

const GUARDRAIL_DOT: Record<GuardrailTone, string> = {
  ok: "bg-emerald-500",
  bad: "bg-rose-500",
  unknown: "bg-slate-400",
};

/** Guardrail status from the record's flags and booleans; "Not available" when it was never checked. */
export function guardrailRows(
  data: { safety_passed?: unknown; grounded?: unknown } | null | undefined,
  flags: readonly string[],
): { label: string; value: string; tone: GuardrailTone }[] {
  const scanned = typeof data?.safety_passed === "boolean";
  const scan = (flag: string, hit: string, clean: string) =>
    flags.includes(flag)
      ? { value: hit, tone: "bad" as const }
      : scanned
        ? { value: clean, tone: "ok" as const }
        : { value: "Not available", tone: "unknown" as const };
  const grounded =
    data?.grounded === true
      ? { value: "Verified", tone: "ok" as const }
      : data?.grounded === false
        ? { value: "Not grounded", tone: "bad" as const }
        : { value: "Not evaluated", tone: "unknown" as const };
  return [
    { label: "PII Leakage", ...scan("pii_leak_detected", "Detected", "Clean") },
    { label: "Secret Scanning", ...scan("secret_leak_detected", "Detected", "Clean") },
    { label: "Prompt Injection", ...scan("unsafe_input_detected", "Blocked", "None detected") },
    { label: "Groundedness", ...grounded },
  ];
}

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
  const [loadError, setLoadError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [activeTab, setActiveTab] = useState<"overview" | "judge" | "sources" | "latency">("overview");

  useEffect(() => {
    if (initialData) {
      setData(initialData);
    }
    if (!isOpen || !recordId || !token) return;

    if (!initialData || !initialData.retrieved_chunks?.length) {
      setLoading(true);
      setLoadError(null);
      api
        .evaluationRecordDetail(token, recordId)
        .then((res) => {
          setData(res);
          setLoading(false);
        })
        .catch((err: unknown) => {
          setLoadError(err instanceof Error && err.message ? err.message : "Couldn't load this record.");
          setLoading(false);
        });
    }
  }, [isOpen, recordId, initialData, token]);

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Unreported values show "—", never a made-up score.
  const passed: boolean | null = typeof data?.passed === "boolean" ? data.passed : null;
  const isPassed = passed !== false;
  const score = (v: unknown, digits: number) => (typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "—");
  const share = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : "—");
  const faith = score(data?.faithfulness, 1);
  const rel = score(data?.relevance, 1);
  const comp = score(data?.completeness, 1);
  const citPrec = share(data?.citation_precision);
  const chunks = data?.retrieved_chunks ?? [];
  const latency = data?.latency_ms ? formatLatencyMs(data.latency_ms) : "—";

  const requestId: string | null = data?.request_id || recordId;
  const flags: string[] = Array.isArray(data?.guardrail_flags) ? data.guardrail_flags : [];
  const judgeRan = typeof data?.faithfulness === "number" || Boolean(data?.judge_reason);
  const guardrails = guardrailRows(data, flags);

  const title = (
    <>
      <span
        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold ${
          isPassed
            ? "bg-emerald-50 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 border border-emerald-200/80 dark:border-emerald-800/80"
            : "bg-rose-50 dark:bg-rose-950/60 text-rose-700 dark:text-rose-300 border border-rose-200/80 dark:border-rose-800/80"
        }`}
      >
        {isPassed ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertTriangle className="w-3.5 h-3.5" />}
        {passed === null ? "Not evaluated" : passed ? "Evaluation Passed" : "Issue Detected"}
      </span>
      <span className="text-xs font-normal font-mono text-slate-500 dark:text-fg-muted truncate max-w-[200px]">
        ID: {requestId}
      </span>
      {requestId ? <CopyButton value={requestId} label="Copy request id" /> : null}
    </>
  );

  // Navigation sub-tabs (fixed above the scrolling body)
  const subTabs = (
    <div className="flex border-b border-teal-100 dark:border-teal-500/20 px-6 gap-6 bg-surface-card text-xs font-medium">
      <button
        onClick={() => setActiveTab("overview")}
        className={`py-3 border-b-2 transition-colors ${
          activeTab === "overview"
            ? "border-teal-500 text-teal-600 dark:text-teal-400 font-bold"
            : "border-transparent text-fg-subtle hover:text-slate-800 dark:hover:text-slate-200"
        }`}
      >
        Interaction & Audit
      </button>
      <button
        onClick={() => setActiveTab("judge")}
        className={`py-3 border-b-2 transition-colors ${
          activeTab === "judge"
            ? "border-teal-500 text-teal-600 dark:text-teal-400 font-bold"
            : "border-transparent text-fg-subtle hover:text-slate-800 dark:hover:text-slate-200"
        }`}
      >
        LLM Judge Breakdown
      </button>
      <button
        onClick={() => setActiveTab("sources")}
        className={`py-3 border-b-2 transition-colors ${
          activeTab === "sources"
            ? "border-teal-500 text-teal-600 dark:text-teal-400 font-bold"
            : "border-transparent text-fg-subtle hover:text-slate-800 dark:hover:text-slate-200"
        }`}
      >
        Sources & Citations ({chunks.length})
      </button>
      <button
        onClick={() => setActiveTab("latency")}
        className={`py-3 border-b-2 transition-colors ${
          activeTab === "latency"
            ? "border-teal-500 text-teal-600 dark:text-teal-400 font-bold"
            : "border-transparent text-fg-subtle hover:text-slate-800 dark:hover:text-slate-200"
        }`}
      >
        Latency & Ops
      </button>
    </div>
  );

  const footer = (
    <div className="flex items-center justify-between text-xs text-slate-500 dark:text-fg-muted">
      <span>Continuous Evaluator v2.0</span>
      <button
        onClick={onClose}
        className="px-4 py-2 bg-teal-600 hover:bg-teal-700 text-white rounded-lg font-medium transition-colors shadow-sm"
      >
        Close Inspector
      </button>
    </div>
  );

  return (
    <DrawerShell
      open={isOpen}
      onClose={onClose}
      title={title}
      closeLabel="Close inspector"
      fullViewHref={requestId ? entityHref({ kind: "trace", id: requestId }) : undefined}
      toolbar={subTabs}
      footer={footer}
    >
      {loading && (
        <div className="flex items-center justify-center h-48 text-slate-400 dark:text-fg-subtle text-sm">
          <Sparkles className="w-5 h-5 animate-spin mr-2 text-teal-500" />
          Loading deep inspection telemetry...
        </div>
      )}

      {!loading && loadError && (
        <p role="alert" className="mx-6 mt-4 rounded-lg border border-status-danger/40 bg-status-danger-bg p-3 text-xs text-status-danger-fg">
          {loadError}. Showing what the list row already had.
        </p>
      )}

      {!loading && activeTab === "overview" && (
        <>
          {/* Query Box */}
          <div className="space-y-1.5">
            <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-fg-muted">User Query</label>
            <div className="p-3.5 bg-teal-50/30 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20 text-sm font-medium text-fg select-text">
              {data?.query || "No query available"}
            </div>
          </div>

          {/* Response Box */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-fg-muted">Generated Assistant Response</label>
              <button
                onClick={() => copyToClipboard(data?.answer || "")}
                className="inline-flex items-center gap-1 text-[11px] text-teal-600 dark:text-teal-400 hover:text-teal-700 dark:hover:text-teal-300 font-medium"
              >
                {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                {copied ? "Copied" : "Copy Response"}
              </button>
            </div>
            <div className="p-4 bg-surface-card rounded-xl border border-teal-200/70 dark:border-teal-500/20 text-xs sm:text-sm text-fg leading-relaxed max-h-72 overflow-y-auto whitespace-pre-wrap select-text">
              {data?.answer || "No response text available."}
            </div>
          </div>

          {/* Quick Metrics Bar */}
          <div className="grid grid-cols-3 gap-3">
            <div className="p-3 bg-teal-50/30 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20">
              <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-fg-muted">Faithfulness</p>
              <p className="text-lg font-black font-mono text-teal-600 dark:text-teal-400 mt-0.5">{faith} / 5.0</p>
            </div>
            <div className="p-3 bg-teal-50/30 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20">
              <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-fg-muted">Citation Prec.</p>
              <p className="text-lg font-black font-mono text-emerald-600 dark:text-emerald-400 mt-0.5">{citPrec}</p>
            </div>
            <div className="p-3 bg-teal-50/30 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20">
              <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-fg-muted">Total Latency</p>
              <p className="text-lg font-black font-mono text-fg mt-0.5">{latency}</p>
            </div>
          </div>

          {/* Security & Safety Audit Status: from the record's own flags */}
          <div className="p-4 bg-teal-50/30 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20 space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-700 dark:text-teal-300 flex items-center gap-1.5">
              {guardrails.some((g) => g.tone === "bad") ? (
                <ShieldAlert className="w-4 h-4 text-rose-600 dark:text-rose-400" />
              ) : (
                <ShieldCheck className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
              )}
              Security & Governance Guardrails
            </p>
            <div className="grid grid-cols-2 gap-2 text-xs pt-1" data-testid="inspector-guardrails">
              {guardrails.map((g) => (
                <div key={g.label} className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full ${GUARDRAIL_DOT[g.tone]}`} />
                  <span className="text-slate-500 dark:text-fg-muted">{g.label}:</span>
                  <span className="font-semibold text-fg">{g.value}</span>
                </div>
              ))}
            </div>
            {flags.length > 0 ? (
              <p className="text-[11px] text-fg-muted">
                Flags: <span className="font-mono">{flags.join(", ")}</span>
              </p>
            ) : null}
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
              <div className="bg-surface-card p-3 rounded-lg border border-teal-100 dark:border-teal-500/20 shadow-sm text-center">
                <p className="text-[10px] font-bold uppercase text-slate-500 dark:text-fg-muted">Faithfulness</p>
                <p className="text-2xl font-black font-mono text-teal-600 dark:text-teal-400 mt-1">{faith}</p>
                <p className="text-[10px] text-slate-400 dark:text-fg-subtle mt-0.5">Claims backed by docs</p>
              </div>
              <div className="bg-surface-card p-3 rounded-lg border border-teal-100 dark:border-teal-500/20 shadow-sm text-center">
                <p className="text-[10px] font-bold uppercase text-slate-500 dark:text-fg-muted">Relevance</p>
                <p className="text-2xl font-black font-mono text-teal-600 dark:text-teal-400 mt-1">{rel}</p>
                <p className="text-[10px] text-slate-400 dark:text-fg-subtle mt-0.5">Direct query answer</p>
              </div>
              <div className="bg-surface-card p-3 rounded-lg border border-teal-100 dark:border-teal-500/20 shadow-sm text-center">
                <p className="text-[10px] font-bold uppercase text-slate-500 dark:text-fg-muted">Completeness</p>
                <p className="text-2xl font-black font-mono text-teal-600 dark:text-teal-400 mt-1">{comp}</p>
                <p className="text-[10px] text-slate-400 dark:text-fg-subtle mt-0.5">Coverage of facts</p>
              </div>
            </div>
          </div>

          {/* Judge Reasoning */}
          <div className="space-y-1.5">
            <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-fg-muted">Judge Rationale & Synthesis</label>
            <div className="p-4 bg-teal-50/30 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20 text-xs sm:text-sm text-fg leading-relaxed select-text">
              {data?.judge_reason || (
                <span className="text-fg-muted">No judge rationale recorded. The LLM judge did not run for this answer.</span>
              )}
            </div>
          </div>

          {/* Unsupported Claims (if any) */}
          <div className="space-y-1.5">
            <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-fg-muted">Unsupported Claims Detected</label>
            {data?.unsupported_claims && data.unsupported_claims.length > 0 ? (
              <div className="space-y-2">
                {data.unsupported_claims.map((claim: string, idx: number) => (
                  <div key={idx} className="p-2.5 bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 rounded-lg text-xs text-rose-700 dark:text-rose-300 flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                    <span>{claim}</span>
                  </div>
                ))}
              </div>
            ) : judgeRan ? (
              <div className="p-3 bg-emerald-50/80 dark:bg-emerald-950/40 border border-emerald-200/80 dark:border-emerald-800/60 rounded-xl text-xs text-emerald-800 dark:text-emerald-300 flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                <span>The judge reported no unsupported claims.</span>
              </div>
            ) : (
              <div className="p-3 rounded-xl border border-line text-xs text-fg-muted flex items-center gap-2">
                <HelpCircle className="w-4 h-4" />
                <span>Not evaluated: the LLM judge did not run for this answer.</span>
              </div>
            )}
          </div>
        </div>
      )}

      {!loading && activeTab === "sources" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between pb-1">
            <span className="text-xs font-semibold text-fg-muted">Retrieved Context Passages ({chunks.length})</span>
            <span className="text-xs text-slate-400 dark:text-fg-subtle">Top-k Similarity Reranked</span>
          </div>

          {chunks.length === 0 ? (
            <div className="p-6 text-center text-slate-400 dark:text-fg-subtle text-xs bg-teal-50/20 dark:bg-white/[0.02] rounded-xl border border-teal-200/70 dark:border-teal-500/20">
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
                    <span className="text-xs font-bold text-fg">{c.source_name || "Enterprise Document"}</span>
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
                <p className="text-xs text-fg-muted leading-relaxed bg-surface-card p-3 rounded-lg border border-teal-100 dark:border-teal-500/10 select-text font-sans">
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

            <p className="text-xs text-fg-muted" data-testid="inspector-stage-latency">
              Per-stage timings are not available on evaluation records; only the end-to-end total is stored. Open the
              full trace for the measured embedding, retrieval, rerank and LLM stages.
            </p>
          </div>
        </div>
      )}
    </DrawerShell>
  );
}
