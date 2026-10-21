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
/** Room right of the longest bar for the count and conversion text. */
const VALUE_WIDTH = 72;
const MAX_BAR = 26;
const pct = new Intl.NumberFormat("en", { style: "percent", maximumFractionDigits: 0 });

/**
 * Stage conversion (triage: new, reviewed, promoted; access requests), as
 * horizontal bars on one left baseline with the count at each bar's end.
 * A centred trapezoid read as an inverted triangle when a middle stage was
 * 0 (3 -> 0 -> 0 -> 3), and the counts sat far from the shapes (VA-45).
 */
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
    const x0 = labelW + 8;
    const plotW = Math.max(1, width - x0 - VALUE_WIDTH);
    const rowH = h / data.length;
    const barH = Math.min(MAX_BAR, rowH * 0.62);
    const barW = (v: number) => (v > 0 ? Math.max(2, (v / max) * plotW) : 0);
    return { labelW, x0, rowH, barH, barW, plotW };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { x0, rowH, barW } = layout(size);
    const d = data[active];
    return {
      x: x0 + Math.max(barW(d.value), 24) / 2,
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
        const { labelW, x0, rowH, barH, barW } = layout(size);
        return (
          <>
            {data.map((d, i) => {
              const y = rowH * i + (rowH - barH) / 2;
              const w = barW(d.value);
              const shade = ct.sequential[Math.max(0, ct.sequential.length - 1 - i) % ct.sequential.length];
              return (
                <g key={i} {...getMarkProps(i)} opacity={opacityFor(i)}>
                  <rect x={0} y={rowH * i} width={size.width} height={rowH} fill="transparent" />
                  <text x={labelW} y={rowH * i + rowH / 2} dy="0.32em" textAnchor="end" fontSize={AXIS_FONT_SIZE} fill={ct.axis}>
                    {d.label}
                  </text>
                  {w > 0 && <rect x={x0} y={y} width={w} height={barH} rx={4} fill={shade} data-testid="funnel-bar" />}
                  <text
                    x={x0 + w + 6}
                    y={rowH * i + rowH / 2}
                    dy="0.32em"
                    fontSize={AXIS_FONT_SIZE}
                    fill={ct.axis}
                    className="tabular-nums"
                  >
                    {formatValue(d.value)}
                    {i > 0 && first > 0 ? <tspan>{` · ${conversion(d)}`}</tspan> : null}
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
