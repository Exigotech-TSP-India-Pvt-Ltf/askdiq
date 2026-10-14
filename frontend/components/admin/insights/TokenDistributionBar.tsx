"use client";

import React from "react";

export interface TokenBreakdownData {
  prompt: number;
  reasoning: number;
  visible: number;
  embeddings: number;
  cached?: number;
}

export interface TokenDistributionBarProps {
  data: TokenBreakdownData;
  className?: string;
  showLegend?: boolean;
}

export function TokenDistributionBar({
  data,
  className = "",
  showLegend = true,
}: TokenDistributionBarProps) {
  const total =
    data.prompt +
    data.reasoning +
    data.visible +
    data.embeddings +
    (data.cached || 0);

  const pct = (val: number) => (total > 0 ? (val / total) * 100 : 0);

  const segments = [
    {
      id: "prompt",
      label: "Input Context",
      tokens: data.prompt,
      pct: pct(data.prompt),
      color: "bg-indigo-500",
      textColor: "text-indigo-600",
    },
    {
      id: "reasoning",
      label: "Reasoning",
      tokens: data.reasoning,
      pct: pct(data.reasoning),
      color: "bg-purple-500",
      textColor: "text-purple-600",
    },
    {
      id: "visible",
      label: "Visible Output",
      tokens: data.visible,
      pct: pct(data.visible),
      color: "bg-emerald-500",
      textColor: "text-emerald-600",
    },
    {
      id: "embeddings",
      label: "Embeddings",
      tokens: data.embeddings,
      pct: pct(data.embeddings),
      color: "bg-cyan-500",
      textColor: "text-cyan-600",
    },
    ...(data.cached
      ? [
          {
            id: "cached",
            label: "Cached Context",
            tokens: data.cached,
            pct: pct(data.cached),
            color: "bg-teal-400",
            textColor: "text-teal-600",
          },
        ]
      : []),
  ];

  return (
    <div className={`flex flex-col gap-2.5 ${className}`}>
      {/* Progress Bar */}
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-slate-100 shadow-inner">
        {total === 0 ? (
          <div className="h-full w-full bg-slate-200" />
        ) : (
          segments.map((s) =>
            s.pct > 0 ? (
              <div
                key={s.id}
                style={{ width: `${s.pct}%` }}
                title={`${s.label}: ${s.tokens.toLocaleString()} (${s.pct.toFixed(1)}%)`}
                className={`h-full ${s.color} transition-all duration-300 first:rounded-l-full last:rounded-r-full`}
              />
            ) : null
          )
        )}
      </div>

      {/* Legend */}
      {showLegend && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
          {segments.map((s) => (
            <div key={s.id} className="flex items-center gap-1.5">
              <span className={`h-2.5 w-2.5 rounded-full ${s.color}`} />
              <span className="text-slate-500">{s.label}:</span>
              <span className="font-semibold text-slate-800">
                {s.tokens.toLocaleString()}
              </span>
              <span className="text-[10px] text-slate-400">({s.pct.toFixed(1)}%)</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
