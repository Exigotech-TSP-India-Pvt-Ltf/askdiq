"use client";

import React from "react";

export interface OutcomePillBarProps {
  outcomes: Record<string, number>;
  selectedOutcome?: string | null;
  onSelectOutcome?: (outcome: string) => void;
  className?: string;
}

const OUTCOME_CONFIG: Record<
  string,
  { label: string; bg: string; text: string; activeBorder: string }
> = {
  answered: {
    label: "Answered",
    bg: "bg-emerald-50 dark:bg-emerald-500/15",
    text: "text-emerald-700 dark:text-emerald-300",
    activeBorder: "border-emerald-500 ring-2 ring-emerald-200",
  },
  ungrounded: {
    label: "Ungrounded",
    bg: "bg-amber-50 dark:bg-amber-500/15",
    text: "text-amber-700 dark:text-amber-300",
    activeBorder: "border-amber-500 ring-2 ring-amber-200",
  },
  blocked: {
    label: "Blocked",
    bg: "bg-rose-50 dark:bg-rose-500/15",
    text: "text-rose-700 dark:text-rose-300",
    activeBorder: "border-rose-500 ring-2 ring-rose-200",
  },
  conversational: {
    label: "Conversational",
    bg: "bg-blue-50 dark:bg-blue-500/15",
    text: "text-blue-700 dark:text-blue-300",
    activeBorder: "border-blue-500 ring-2 ring-blue-200",
  },
  llm_failed: {
    label: "LLM Failed",
    bg: "bg-purple-50 dark:bg-purple-500/15",
    text: "text-purple-700 dark:text-purple-300",
    activeBorder: "border-purple-500 ring-2 ring-purple-200",
  },
  clarify: {
    label: "Clarifying question",
    bg: "bg-sky-50 dark:bg-sky-500/15",
    text: "text-sky-700 dark:text-sky-300",
    activeBorder: "border-sky-500 ring-2 ring-sky-200",
  },
};

export function OutcomePillBar({
  outcomes,
  selectedOutcome,
  onSelectOutcome,
  className = "",
}: OutcomePillBarProps) {
  const total = Object.values(outcomes).reduce((acc, val) => acc + val, 0);

  return (
    <div className={`flex flex-wrap items-center gap-2 ${className}`}>
      {/* All / Total Pill */}
      <button
        type="button"
        onClick={() => onSelectOutcome?.("all")}
        className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition ${
          !selectedOutcome || selectedOutcome === "all"
            ? "border-slate-800 bg-slate-800 text-white shadow-sm dark:border-brand-400 dark:bg-brand-600"
            : "border-line bg-surface-card text-fg-muted hover:bg-surface-muted"
        }`}
      >
        <span>All</span>
        <span
          className={`rounded-full px-1.5 py-0.2 text-[10px] font-bold ${
            !selectedOutcome || selectedOutcome === "all"
              ? "bg-white/20 text-white"
              : "bg-surface-muted text-fg-muted"
          }`}
        >
          {total.toLocaleString()}
        </span>
      </button>

      {/* Individual Outcome Pills */}
      {Object.entries(OUTCOME_CONFIG).map(([key, config]) => {
        const count = outcomes[key] ?? 0;
        const isSelected = selectedOutcome === key;

        return (
          <button
            key={key}
            type="button"
            onClick={() => onSelectOutcome?.(isSelected ? "all" : key)}
            className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition ${
              isSelected
                ? `${config.activeBorder} ${config.bg} ${config.text} font-bold shadow-sm`
                : `${config.bg} ${config.text} border-transparent hover:opacity-90`
            }`}
          >
            <span>{config.label}</span>
            <span
              className={`rounded-full px-1.5 py-0.2 text-[10px] font-bold ${
                isSelected ? "bg-white/40 dark:bg-white/10" : "bg-black/5 dark:bg-white/10"
              }`}
            >
              {count.toLocaleString()}
            </span>
          </button>
        );
      })}
    </div>
  );
}
