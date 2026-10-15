"use client";

import React from "react";
import { ArrowRight } from "lucide-react";

export interface HighlightCardProps {
  rank?: number;
  title: string;
  subtitle?: string;
  metricLabel: string;
  metricValue: string | number;
  secondaryMetric?: string;
  onClick?: () => void;
  className?: string;
}

export function HighlightCard({
  rank,
  title,
  subtitle,
  metricLabel,
  metricValue,
  secondaryMetric,
  onClick,
  className = "",
}: HighlightCardProps) {
  const isClickable = Boolean(onClick);

  return (
    <div
      onClick={onClick}
      className={`group relative flex flex-col justify-between overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-surface-card p-4 shadow-card dark:shadow-xl transition-all hover:border-teal-400 dark:hover:border-teal-400/60 ${
        isClickable ? "cursor-pointer hover:shadow-md" : ""
      } ${className}`}
    >
      {/* Ambient Aurora Corner Glow */}
      <div className="absolute -top-6 -right-6 w-20 h-20 bg-teal-400/10 dark:bg-teal-500/15 rounded-full blur-xl pointer-events-none transition-transform group-hover:scale-125" />

      <div className="relative z-10">
        <div className="flex items-center justify-between gap-2">
          {rank !== undefined && (
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-teal-50 dark:bg-teal-500/20 text-[10px] font-mono font-bold text-teal-700 dark:text-teal-300 border border-teal-200/80 dark:border-teal-500/30">
              #{rank}
            </span>
          )}
          <span className="text-[11px] font-medium text-slate-400 dark:text-teal-200/70 truncate max-w-[150px]">
            {subtitle}
          </span>
          {isClickable && (
            <ArrowRight className="h-3.5 w-3.5 text-slate-400 dark:text-slate-500 transition group-hover:translate-x-0.5 group-hover:text-teal-600 dark:group-hover:text-teal-300 ml-auto" />
          )}
        </div>

        <h4 className="mt-2 text-sm font-semibold text-slate-900 dark:text-white truncate" title={title}>
          {title}
        </h4>
      </div>

      <div className="relative z-10 mt-3 flex items-baseline justify-between border-t border-teal-100 dark:border-teal-500/20 pt-2 text-xs">
        <span className="text-slate-500 dark:text-slate-400">{metricLabel}</span>
        <div className="flex items-center gap-1.5">
          <span className="font-mono font-bold text-slate-900 dark:text-white">
            {typeof metricValue === "number" ? metricValue.toLocaleString() : metricValue}
          </span>
          {secondaryMetric && (
            <span className="text-[10px] text-slate-400 dark:text-slate-500">({secondaryMetric})</span>
          )}
        </div>
      </div>
    </div>
  );
}
