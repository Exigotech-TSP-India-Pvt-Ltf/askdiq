"use client";

import React from "react";
import { studioCardClass } from "@/components/ui/StudioCard";
import { studioToneClasses } from "@/components/ui/styles";

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

/** KPI tile on the studio surface (Usage Live Telemetry look). */
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
    <div className={`group p-5 ${studioCardClass({ interactive: true })} ${className}`}>
      <div className="relative z-10 flex items-start justify-between gap-2">
        <span className="text-[10px] font-bold uppercase tracking-wider text-fg-subtle dark:text-brand-200/80">
          {title}
        </span>
        {Icon && (
          <div className={`flex h-8 w-8 items-center justify-center rounded-xl border shadow-sm ${studioToneClasses.brand}`}>
            <Icon className="h-4 w-4" />
          </div>
        )}
      </div>

      <div className="relative z-10 mt-3 flex items-baseline justify-between gap-2">
        <span className="font-mono text-2xl font-black tracking-tight text-fg">
          {typeof value === "number" ? value.toLocaleString() : value}
        </span>
        {badge}
      </div>

      {(subtitle || trend) && (
        <div className="relative z-10 mt-2 flex items-center gap-1.5 text-[11px] text-fg-subtle">
          {trend && (
            <span
              className={`rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold ${
                trend.isPositive ? studioToneClasses.ok : studioToneClasses.warn
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
