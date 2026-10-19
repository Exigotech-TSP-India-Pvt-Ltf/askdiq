"use client";

import { useCallback, useMemo } from "react";
import { useChartTheme } from "@/lib/use-chart-theme";
import {
  AXIS_FONT_SIZE,
  ChartEmpty,
  ChartFrame,
  TooltipBody,
  formatCompact,
  useChartMarks,
  type ChartSize,
  type SelectHandler,
} from "./primitives";

export interface FunnelStage {
  label: string;
  value: number;
}

export interface FunnelProps {
  data: readonly FunnelStage[];
  onSelect?: SelectHandler<FunnelStage>;
  label?: string;
  height?: number;
  formatValue?: (value: number) => string;
  /** Tooltip name of the stage value (default "Count"), e.g. "Users". */
  valueLabel?: string;
}

const LABEL_WIDTH = 110;
const pct = new Intl.NumberFormat("en", { style: "percent", maximumFractionDigits: 0 });

/** Stage conversion (triage: new, reviewed, promoted; access requests). */
export function Funnel({
  data,
  onSelect,
  label = "Funnel",
  height,
  formatValue = formatCompact,
  valueLabel = "Count",
}: FunnelProps) {
  const ct = useChartTheme();
  const first = data[0]?.value ?? 0;
  const conversion = useCallback(
    (d: FunnelStage) => (first > 0 ? pct.format(d.value / first) : "—"),
    [first],
  );
  const describe = useCallback(
    (d: FunnelStage) => `${d.label}: ${formatValue(d.value)} (${conversion(d)} of first stage)`,
    [conversion, formatValue],
  );
  const { active, getMarkProps, opacityFor } = useChartMarks(data, describe, onSelect, ct.dimOpacity);
  const max = useMemo(() => Math.max(1, ...data.map((d) => d.value)), [data]);

  if (data.length === 0) return <ChartEmpty height={height} />;

  const layout = ({ width, height: h }: ChartSize) => {
    const labelW = Math.min(LABEL_WIDTH, width * 0.3);
    const plotW = Math.max(1, width - labelW - 56);
    const cx = labelW + 8 + plotW / 2;
    const rowH = h / data.length;
    const half = (v: number) => Math.max(1, (v / max) * (plotW / 2));
    return { labelW, cx, rowH, half, plotW };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { cx, rowH } = layout(size);
    const d = data[active];
    return {
      x: cx,
      y: rowH * active + rowH * 0.15,
      content: (
        <TooltipBody
          title={d.label}
          rows={[
            { label: valueLabel, value: formatValue(d.value), color: ct.sequential[ct.sequential.length - 1] },
            { label: "Of first stage", value: conversion(d) },
          ]}
        />
      ),
    };
  };

  return (
    <ChartFrame label={label} height={height} tooltip={tooltip}>
      {(size) => {
        const { labelW, cx, rowH, half, plotW } = layout(size);
        const gap = Math.min(4, rowH * 0.1);
        return (
          <>
            {data.map((d, i) => {
              const next = data[i + 1]?.value ?? d.value;
              const top = rowH * i + gap / 2;
              const bottom = rowH * (i + 1) - gap / 2;
              const w0 = half(d.value);
              const w1 = half(Math.min(d.value, next));
              const shade = ct.sequential[Math.max(0, ct.sequential.length - 1 - i) % ct.sequential.length];
              return (
                <g key={i} {...getMarkProps(i)} opacity={opacityFor(i)}>
                  <rect x={0} y={rowH * i} width={size.width} height={rowH} fill="transparent" />
                  <text x={labelW} y={rowH * i + rowH / 2} dy="0.32em" textAnchor="end" fontSize={AXIS_FONT_SIZE} fill={ct.axis}>
                    {d.label}
                  </text>
                  <path
                    d={`M${cx - w0},${top} L${cx + w0},${top} L${cx + w1},${bottom} L${cx - w1},${bottom} Z`}
                    fill={shade}
                  />
                  <text
                    x={labelW + 8 + plotW + 6}
                    y={rowH * i + rowH / 2}
                    dy="0.32em"
                    fontSize={AXIS_FONT_SIZE}
                    fill={ct.axis}
                    className="tabular-nums"
                  >
                    {formatValue(d.value)}
                  </text>
                </g>
              );
            })}
          </>
        );
      }}
    </ChartFrame>
  );
}
