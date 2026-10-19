"use client";

import React from "react";
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  HelpCircle,
  ShieldAlert,
} from "lucide-react";

/** Standardized comma-formatted number renderer with tabular numerals */
export function formatNumber(
  val: number | null | undefined,
  fallback = "0"
): React.ReactNode {
  if (val === null || val === undefined || isNaN(val)) {
    return <span className="font-mono tabular-nums text-fg-subtle">{fallback}</span>;
  }
  return (
    <span className="font-mono tabular-nums font-medium text-fg">
      {val.toLocaleString()}
    </span>
  );
}

/** Standardized currency renderer with tabular numerals and high-contrast bold accent */
export function formatCurrency(
  val: number | null | undefined,
  decimals = 4,
  fallback = "$0.0000"
): React.ReactNode {
  if (val === null || val === undefined || isNaN(val)) {
    return <span className="font-mono tabular-nums text-fg-subtle">{fallback}</span>;
  }
  return (
    <span className="font-mono tabular-nums font-bold text-fg">
      ${val.toFixed(decimals)}
    </span>
  );
}

/** Standardized token count renderer */
export function formatTokens(
  val: number | null | undefined,
  colorScheme: "default" | "muted" | "input" | "reasoning" | "output" | "total" = "default"
): React.ReactNode {
  const num = val ?? 0;
  const formatted = num.toLocaleString();

  switch (colorScheme) {
    case "reasoning":
      return (
        <span
          className="font-mono tabular-nums font-medium text-purple-600 dark:text-purple-400"
          title="Turn-level reasoning tokens"
        >
          {formatted}
        </span>
      );
    case "output":
      return (
        <span
          className="font-mono tabular-nums font-medium text-emerald-600 dark:text-emerald-400"
          title="Generated output tokens"
        >
          {formatted}
        </span>
      );
    case "input":
      return (
        <span
          className="font-mono tabular-nums text-fg-muted"
          title="Input context tokens"
        >
          {formatted}
        </span>
      );
    case "muted":
      return (
        <span className="font-mono tabular-nums text-fg-subtle">
          {formatted}
        </span>
      );
    case "total":
      return (
        <span className="font-mono tabular-nums font-semibold text-fg">
          {formatted}
        </span>
      );
    default:
      return (
        <span className="font-mono tabular-nums text-fg-muted">
          {formatted}
        </span>
      );
  }
}

/** Standardized latency renderer */
export function formatLatency(
  ms: number | null | undefined,
  fallback = "—"
): React.ReactNode {
  if (ms === null || ms === undefined || isNaN(ms)) {
    return <span className="font-mono text-fg-subtle">{fallback}</span>;
  }
  return (
    <span className="font-mono tabular-nums text-fg-muted whitespace-nowrap">
      {ms.toLocaleString()} ms
    </span>
  );
}

export type BadgeVariant =
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "neutral"
  | "purple"
  | "teal";

export interface TableBadgeProps {
  label: string;
  variant?: BadgeVariant;
  icon?: React.ComponentType<{ className?: string }>;
  title?: string;
  className?: string;
}

/** Theme-aware semantic badge for status, outcomes, and audience scopes */
export function TableBadge({
  label,
  variant = "neutral",
  icon: Icon,
  title,
  className = "",
}: TableBadgeProps) {
  const variantStyles: Record<BadgeVariant, string> = {
    success:
      "bg-emerald-50 text-emerald-800 border-emerald-200/80 dark:bg-emerald-950/50 dark:text-emerald-300 dark:border-emerald-800/80",
    warning:
      "bg-amber-50 text-amber-800 border-amber-200/80 dark:bg-amber-950/50 dark:text-amber-300 dark:border-amber-800/80",
    danger:
      "bg-rose-50 text-rose-800 border-rose-200/80 dark:bg-rose-950/50 dark:text-rose-300 dark:border-rose-800/80",
    info:
      "bg-blue-50 text-blue-800 border-blue-200/80 dark:bg-blue-950/50 dark:text-blue-300 dark:border-blue-800/80",
    purple:
      "bg-purple-50 text-purple-800 border-purple-200/80 dark:bg-purple-950/50 dark:text-purple-300 dark:border-purple-800/80",
    teal:
      "bg-teal-50 text-teal-800 border-teal-200/80 dark:bg-teal-950/50 dark:text-teal-300 dark:border-teal-800/80",
    neutral:
      "bg-slate-100 text-slate-700 border-slate-200 dark:bg-white/5 dark:text-slate-300 dark:border-white/10",
  };

  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold border transition-colors ${variantStyles[variant]} ${className}`}
    >
      {Icon && <Icon className="w-3 h-3 shrink-0" />}
      <span className="capitalize">{label}</span>
    </span>
  );
}

/** Pre-configured OutcomeBadge matching AskDIQ chatbot outcomes */
export function OutcomeBadge({ outcome }: { outcome: string }) {
  const norm = outcome?.toLowerCase() || "";
  switch (norm) {
    case "answered":
      return (
        <TableBadge
          label="Answered"
          variant="success"
          icon={CheckCircle2}
          title="Query answered with grounding citation"
        />
      );
    case "ungrounded":
      return (
        <TableBadge
          label="Ungrounded"
          variant="warning"
          icon={AlertTriangle}
          title="Query answered with caveat or ungrounded synthesis"
        />
      );
    case "blocked_guardrail":
    case "blocked":
      return (
        <TableBadge
          label="Blocked"
          variant="danger"
          icon={XCircle}
          title="Request blocked by safety, injection, or privacy guardrails"
        />
      );
    case "clarify":
      return (
        <TableBadge
          label="Clarify"
          variant="info"
          icon={HelpCircle}
          title="Asked the user which topic they meant, with options"
        />
      );
    case "conversational":
      return (
        <TableBadge
          label="Chat"
          variant="info"
          icon={HelpCircle}
          title="Conversational pleasantry or meta question"
        />
      );
    case "llm_failed":
    case "failed":
      return (
        <TableBadge
          label="Failed"
          variant="danger"
          icon={ShieldAlert}
          title="Model or pipeline error"
        />
      );
    default:
      return <TableBadge label={outcome || "Unknown"} variant="neutral" />;
  }
}
