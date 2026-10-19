"use client";

import React from "react";
import { studioCardClass } from "@/components/ui/StudioCard";
import { studioToneClasses } from "@/components/ui/styles";
import { Sparkline, type SparkPoint, type SparkTone } from "@/components/charts/Sparkline";

export interface StatCardProps {
  title: string;
  value: React.ReactNode;
  /** One-line footnote (truncated; strings also get a tooltip). */
  subtitle?: React.ReactNode;
  icon?: React.ComponentType<{ className?: string; strokeWidth?: string | number }>;
  badge?: React.ReactNode;
  trend?: {
    value: string;
    isPositive?: boolean;
  };
  /** Optional mini chart (same sparkline as `KpiTrendCard`). */
  spark?: readonly SparkPoint[];
  sparkTone?: SparkTone;
  formatSparkValue?: (value: number) => string;
  className?: string;
}

/**
 * KPI tile on the studio surface. Same header, value and footnote layout as
 * `KpiTrendCard`, so a KPI row reads as one set whether or not a card has a
 * trend: sentence-case label, one-line value, one-line footnote.
 */
export function StatCard({
  title,
  value,
  subtitle,
  icon: Icon,
  badge,
  trend,
  spark,
  sparkTone = "brand",
  formatSparkValue,
  className = "",
}: StatCardProps) {
  return (
    <div className={`group flex flex-col gap-3 p-5 ${studioCardClass({ interactive: true })} ${className}`}>
      <div className="relative z-10 flex items-center justify-between gap-2">
        <p className="min-w-0 truncate text-sm font-medium text-fg-subtle">{title}</p>
        {Icon ? <Icon className="h-4 w-4 shrink-0 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" /> : null}
      </div>

      <div className="relative z-10 flex min-w-0 items-end justify-between gap-2">
        <p data-testid="kpi-value" className="min-w-0 truncate whitespace-nowrap text-2xl font-semibold tabular-nums text-fg">
          {typeof value === "number" ? value.toLocaleString() : value}
        </p>
        {badge}
      </div>

      {spark ? (
        <div className="relative z-10">
          <Sparkline data={spark} label={`${title} trend`} tone={sparkTone} height={36} formatValue={formatSparkValue} />
        </div>
      ) : null}

      {subtitle || trend ? (
        <div className="relative z-10 mt-auto flex min-w-0 items-center gap-1.5 text-[11px] text-fg-subtle">
          {trend ? (
            <span
              className={`shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold ${
                trend.isPositive ? studioToneClasses.ok : studioToneClasses.warn
              }`}
            >
              {trend.value}
            </span>
          ) : null}
          {subtitle ? (
            <span className="min-w-0 truncate whitespace-nowrap" title={typeof subtitle === "string" ? subtitle : undefined}>
              {subtitle}
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
