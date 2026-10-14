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
      className={`rounded-xl border border-slate-200/80 bg-white p-4 shadow-sm transition hover:shadow-md ${className}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-slate-500">{title}</span>
        {Icon && (
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-slate-50 text-slate-500 border border-slate-100">
            <Icon className="h-4 w-4" />
          </div>
        )}
      </div>

      <div className="mt-2 flex items-baseline gap-2">
        <span className="text-xl font-bold tracking-tight text-slate-900">
          {typeof value === "number" ? value.toLocaleString() : value}
        </span>
        {badge}
      </div>

      {(subtitle || trend) && (
        <div className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-500">
          {trend && (
            <span
              className={`font-semibold ${
                trend.isPositive ? "text-emerald-600" : "text-amber-600"
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
