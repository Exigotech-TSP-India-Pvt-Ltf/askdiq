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
    bg: "bg-emerald-50",
    text: "text-emerald-700",
    activeBorder: "border-emerald-500 ring-2 ring-emerald-200",
  },
  ungrounded: {
    label: "Ungrounded",
    bg: "bg-amber-50",
    text: "text-amber-700",
    activeBorder: "border-amber-500 ring-2 ring-amber-200",
  },
  blocked: {
    label: "Blocked",
    bg: "bg-rose-50",
    text: "text-rose-700",
    activeBorder: "border-rose-500 ring-2 ring-rose-200",
  },
  conversational: {
    label: "Conversational",
    bg: "bg-blue-50",
    text: "text-blue-700",
    activeBorder: "border-blue-500 ring-2 ring-blue-200",
  },
  llm_failed: {
    label: "LLM Failed",
    bg: "bg-purple-50",
    text: "text-purple-700",
    activeBorder: "border-purple-500 ring-2 ring-purple-200",
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
            ? "border-slate-800 bg-slate-800 text-white shadow-sm"
            : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
        }`}
      >
        <span>All</span>
        <span
          className={`rounded-full px-1.5 py-0.2 text-[10px] font-bold ${
            !selectedOutcome || selectedOutcome === "all"
              ? "bg-white/20 text-white"
              : "bg-slate-100 text-slate-600"
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
                isSelected ? "bg-white/40" : "bg-black/5"
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
