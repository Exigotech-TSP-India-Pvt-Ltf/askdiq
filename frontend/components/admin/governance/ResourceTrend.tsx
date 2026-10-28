"use client";

import React, { useId, useMemo, useState } from "react";
import { useChartTheme } from "@/lib/use-chart-theme";
import type { TrendPoint } from "@/lib/resource-gauges";

export interface ResourceTrendProps {
  /** Oldest first, values on the gauge's 0-100 scale. */
  points: readonly TrendPoint[];
  /** Warning threshold drawn as a faint dashed line. */
  warn: number;
  danger: number;
  label: string;
  height?: number;
}

const W = 200;
const PAD = 2;

function timeLabel(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function stats(points: readonly TrendPoint[]) {
  let min = points[0];
  let max = points[0];
  for (const p of points) {
    if (p.value < min.value) min = p;
    if (p.value > max.value) max = p;
  }
  return { min, max, now: points[points.length - 1] };
}

/**
 * 15-minute trend under a resource gauge: line on the gauge's 0-100 scale,
 * the warning threshold as a faint dashed line, and a tooltip with the
 * hovered sample plus min / max / now.
 */
export function ResourceTrend({ points, warn, danger, label, height = 40 }: ResourceTrendProps) {
  const ct = useChartTheme();
  const tipId = useId();
  const [hover, setHover] = useState<number | null>(null);
  const top = useMemo(() => Math.max(100, ...points.map((p) => p.value)), [points]);

  if (points.length < 2) {
    return (
      <div className="flex items-end gap-2" style={{ height }} data-testid="resource-trend-empty">
        <span aria-hidden="true" className="mb-1.5 h-px flex-1 border-t border-dashed border-line" />
        <span className="text-[11px] text-fg-muted">Collecting 15 min trend…</span>
      </div>
    );
  }

  const x = (i: number) => PAD + (i * (W - 2 * PAD)) / (points.length - 1);
  const y = (v: number) => PAD + (1 - Math.min(v, top) / top) * (height - 2 * PAD);
  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
  const area = `${line}L${x(points.length - 1).toFixed(1)},${height}L${x(0).toFixed(1)},${height}Z`;
  const { min, max, now } = stats(points);
  const tone = now.value >= danger ? ct.status.danger : now.value >= warn ? ct.status.warn : ct.categorical[0];
  const summary = `${label}, last 15 min: now ${now.display}, min ${min.display}, max ${max.display}`;
  const active = hover === null ? null : points[hover];

  const onMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - box.left) / Math.max(1, box.width);
    setHover(Math.max(0, Math.min(points.length - 1, Math.round(ratio * (points.length - 1)))));
  };

  return (
    <div className="relative" data-testid="resource-trend">
      <svg
        role="img"
        aria-label={summary}
        aria-describedby={hover === null ? undefined : tipId}
        tabIndex={0}
        viewBox={`0 0 ${W} ${height}`}
        preserveAspectRatio="none"
        className="block w-full cursor-crosshair rounded-sm focus:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-500"
        style={{ height }}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
        onFocus={() => setHover(points.length - 1)}
        onBlur={() => setHover(null)}
      >
        <line
          x1={0}
          x2={W}
          y1={y(warn)}
          y2={y(warn)}
          stroke={ct.status.warn}
          strokeOpacity={0.55}
          strokeDasharray="3 3"
          vectorEffect="non-scaling-stroke"
          data-testid="resource-trend-threshold"
        />
        <path d={area} fill={tone} fillOpacity={0.12} stroke="none" />
        <path d={line} fill="none" stroke={tone} strokeWidth={1.5} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        {active ? (
          <line x1={x(hover!)} x2={x(hover!)} y1={0} y2={height} stroke={ct.tooltip.text} strokeOpacity={0.35} vectorEffect="non-scaling-stroke" />
        ) : null}
      </svg>
      {active ? (
        <div
          id={tipId}
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-1 w-max max-w-[16rem] -translate-x-1/2 rounded-md border border-line bg-surface-card px-2 py-1 text-[11px] leading-4 text-fg shadow-lg"
        >
          <div className="font-semibold tabular-nums">
            {timeLabel(active.ts)} · {active.display}
          </div>
          <div className="tabular-nums text-fg-muted">
            Now {now.display} · Min {min.display} · Max {max.display}
          </div>
        </div>
      ) : null}
    </div>
  );
}
