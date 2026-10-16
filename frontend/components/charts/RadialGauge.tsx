"use client";

import { useCallback, useMemo } from "react";
import { useChartTheme } from "@/lib/use-chart-theme";
import type { ChartTheme } from "@/lib/theme-tokens";
import { ChartEmpty, ChartFrame, useChartMarks, type SelectHandler } from "./primitives";

export interface GaugeDatum {
  label: string;
  value: number;
  max: number;
}

export interface GaugeBands {
  /** Value where the gauge turns amber. */
  warn: number;
  /** Value where the gauge turns red. */
  danger: number;
}

export interface RadialGaugeProps {
  label: string;
  /** `null` renders the empty state (no measurement yet). */
  value: number | null;
  max?: number;
  bands?: GaugeBands;
  /** true: low values are bad (satisfaction); false: high values are bad (CPU). Default true. */
  higherIsBetter?: boolean;
  onSelect?: SelectHandler<GaugeDatum>;
  height?: number;
  formatValue?: (value: number, max: number) => string;
  /** Small text under the value. */
  caption?: string;
}

const SWEEP = 240;
const START = -120;

function polar(cx: number, cy: number, r: number, deg: number): [number, number] {
  const rad = ((deg - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

function arc(cx: number, cy: number, r: number, from: number, to: number): string {
  const [x0, y0] = polar(cx, cy, r, from);
  const [x1, y1] = polar(cx, cy, r, to);
  const large = to - from > 180 ? 1 : 0;
  return `M${x0},${y0} A${r},${r} 0 ${large} 1 ${x1},${y1}`;
}

function toneFor(
  ct: ChartTheme,
  value: number,
  bands: GaugeBands | undefined,
  higherIsBetter: boolean,
): string {
  if (!bands) return ct.categorical[0];
  if (higherIsBetter) {
    if (value < bands.danger) return ct.status.danger;
    if (value < bands.warn) return ct.status.warn;
    return ct.status.ok;
  }
  if (value >= bands.danger) return ct.status.danger;
  if (value >= bands.warn) return ct.status.warn;
  return ct.status.ok;
}

const defaultFormat = (value: number, max: number) =>
  max === 100 ? `${Math.round(value)}%` : `${Math.round(value)} / ${max}`;

/** Single-value gauge (satisfaction, CPU, RAM, quota, cache-hit ratio). */
export function RadialGauge({
  label,
  value,
  max = 100,
  bands,
  higherIsBetter = true,
  onSelect,
  height = 150,
  formatValue = defaultFormat,
  caption,
}: RadialGaugeProps) {
  const ct = useChartTheme();
  const data = useMemo<GaugeDatum[]>(() => (value === null ? [] : [{ label, value, max }]), [label, max, value]);
  const describe = useCallback((d: GaugeDatum) => `${d.label}: ${formatValue(d.value, d.max)}`, [formatValue]);
  const { getMarkProps } = useChartMarks(data, describe, onSelect, ct.dimOpacity);

  if (value === null || data.length === 0) return <ChartEmpty height={height} message="No measurement yet" />;

  const ratio = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  const color = toneFor(ct, value, bands, higherIsBetter);

  const frame = (
    <ChartFrame label={label} height={height}>
      {({ width, height: h }) => {
        const r = Math.max(10, Math.min(width / 2, h / 1.6) - 10);
        const cx = width / 2;
        const cy = r + 10;
        const stroke = Math.max(6, r * 0.16);
        return (
          <g {...getMarkProps(0)}>
            <rect x={0} y={0} width={width} height={h} fill="transparent" />
            <path d={arc(cx, cy, r, START, START + SWEEP)} fill="none" stroke={ct.track} strokeWidth={stroke} strokeLinecap="round" />
            {ratio > 0 && (
              <path
                d={arc(cx, cy, r, START, START + SWEEP * ratio)}
                fill="none"
                stroke={color}
                strokeWidth={stroke}
                strokeLinecap="round"
                data-testid="gauge-value"
              />
            )}
            <text
              x={cx}
              y={cy}
              textAnchor="middle"
              dy="0.32em"
              fontSize={Math.max(14, r * 0.38)}
              fontWeight={700}
              fill={ct.tooltip.text}
              className="tabular-nums"
            >
              {formatValue(value, max)}
            </text>
          </g>
        );
      }}
    </ChartFrame>
  );
  if (!caption) return frame;
  return (
    <div className="flex flex-col items-center">
      {frame}
      <p className="-mt-3 px-2 text-center text-xs tabular-nums text-fg-muted" data-testid="gauge-caption">
        {caption}
      </p>
    </div>
  );
}
