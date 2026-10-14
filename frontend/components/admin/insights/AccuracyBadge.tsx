"use client";

import React from "react";
import { CheckCircle2, AlertTriangle } from "lucide-react";

export interface AccuracyBadgeProps {
  actualPct: number;
  actualCount?: number;
  totalCount?: number;
  className?: string;
}

export function AccuracyBadge({
  actualPct,
  actualCount,
  totalCount,
  className = "",
}: AccuracyBadgeProps) {
  const isHighAccuracy = actualPct >= 99.0;

  return (
    <span
      title={
        actualCount !== undefined && totalCount !== undefined
          ? `${actualCount.toLocaleString()} of ${totalCount.toLocaleString()} queries use actual token telemetry`
          : `${actualPct}% queries use actual token telemetry`
      }
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${
        isHighAccuracy
          ? "bg-emerald-50 text-emerald-700 border border-emerald-200/80"
          : "bg-amber-50 text-amber-700 border border-amber-200/80"
      } ${className}`}
    >
      {isHighAccuracy ? (
        <CheckCircle2 className="h-3 w-3 text-emerald-600" />
      ) : (
        <AlertTriangle className="h-3 w-3 text-amber-600" />
      )}
      <span>{actualPct}% Telemetry Accuracy</span>
    </span>
  );
}
