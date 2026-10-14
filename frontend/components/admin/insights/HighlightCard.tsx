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
      className={`group relative flex flex-col justify-between rounded-xl border border-slate-200/80 bg-white p-4 shadow-sm transition ${
        isClickable
          ? "cursor-pointer hover:border-brand-300 hover:shadow-md"
          : ""
      } ${className}`}
    >
      <div>
        <div className="flex items-center justify-between gap-2">
          {rank !== undefined && (
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-100 text-[10px] font-bold text-slate-600">
              #{rank}
            </span>
          )}
          <span className="text-[11px] font-medium text-slate-400 truncate max-w-[150px]">
            {subtitle}
          </span>
          {isClickable && (
            <ArrowRight className="h-3.5 w-3.5 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-brand-600 ml-auto" />
          )}
        </div>

        <h4 className="mt-2 text-sm font-semibold text-slate-900 truncate" title={title}>
          {title}
        </h4>
      </div>

      <div className="mt-3 flex items-baseline justify-between border-t border-slate-100 pt-2 text-xs">
        <span className="text-slate-500">{metricLabel}</span>
        <div className="flex items-center gap-1.5">
          <span className="font-bold text-slate-900">
            {typeof metricValue === "number" ? metricValue.toLocaleString() : metricValue}
          </span>
          {secondaryMetric && (
            <span className="text-[10px] text-slate-400">({secondaryMetric})</span>
          )}
        </div>
      </div>
    </div>
  );
}
