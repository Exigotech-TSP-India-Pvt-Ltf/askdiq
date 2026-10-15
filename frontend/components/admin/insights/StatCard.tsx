"use client";

import React from "react";

export interface StatCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon?: React.ComponentType<{ className?: string }>;
  badge?: React.ReactNode;
  trend?: {
    value: string;
    isPositive?: boolean;
  };
  className?: string;
}

export function StatCard({
  title,
  value,
  subtitle,
  icon: Icon,
  badge,
  trend,
  className = "",
}: StatCardProps) {
  return (
    <div
      className={`group relative overflow-hidden rounded-2xl border border-teal-300/80 dark:border-teal-500/25 bg-white dark:bg-[#0c0d10] p-5 shadow-card dark:shadow-xl transition-all hover:border-teal-400 dark:hover:border-teal-400/60 hover:shadow-md ${className}`}
    >
      {/* Ambient Aurora Corner Glow */}
      <div className="absolute -top-6 -right-6 w-20 h-20 bg-teal-400/10 dark:bg-teal-500/15 rounded-full blur-xl pointer-events-none transition-transform group-hover:scale-125" />

      <div className="relative z-10 flex items-start justify-between gap-2">
        <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-teal-200/80">
          {title}
        </span>
        {Icon && (
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-teal-50 dark:bg-teal-500/15 text-teal-700 dark:text-teal-300 border border-teal-200/80 dark:border-teal-500/30 shadow-2xs">
            <Icon className="h-4 w-4" />
          </div>
        )}
      </div>

      <div className="relative z-10 mt-3 flex items-baseline justify-between gap-2">
        <span className="text-2xl font-black font-mono tracking-tight text-slate-900 dark:text-white">
          {typeof value === "number" ? value.toLocaleString() : value}
        </span>
        {badge}
      </div>

      {(subtitle || trend) && (
        <div className="relative z-10 mt-2 flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
          {trend && (
            <span
              className={`font-semibold font-mono px-1.5 py-0.5 rounded text-[10px] ${
                trend.isPositive
                  ? "bg-emerald-50 text-emerald-700 border border-emerald-200/60 dark:bg-emerald-500/15 dark:text-emerald-300 dark:border-emerald-500/30"
                  : "bg-amber-50 text-amber-700 border border-amber-200/60 dark:bg-amber-500/15 dark:text-amber-300 dark:border-amber-500/30"
              }`}
            >
              {trend.value}
            </span>
          )}
          {subtitle && <span>{subtitle}</span>}
        </div>
      )}
    </div>
  );
}
