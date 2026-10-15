"use client";

import { useCallback, useMemo } from "react";
import { useChartTheme } from "@/lib/use-chart-theme";
import type { ChartTheme } from "@/lib/theme-tokens";
import {
  AXIS_FONT_SIZE,
  ChartEmpty,
  ChartFrame,
  TooltipBody,
  formatCompact,
  scaleLinear,
  useChartMarks,
  type ChartSize,
  type SelectHandler,
} from "./primitives";

export interface WaterfallStep {
  label: string;
  /** Duration or delta. Negative values draw leftwards in the danger colour. */
  value: number;
  /** Explicit offset (trace spans). Omitted = starts where the previous step ended. */
  start?: number;
  tone?: keyof ChartTheme["status"];
}

export interface WaterfallProps {
  data: readonly WaterfallStep[];
  onSelect?: SelectHandler<WaterfallStep>;
  label?: string;
  height?: number;
  formatValue?: (value: number) => string;
}

const LABEL_WIDTH = 110;
const ROW_GAP = 0.3;

/** Per-stage waterfall (request stages, trace spans, cost deltas). */
export function Waterfall({
  data,
  onSelect,
  label = "Waterfall",
  height,
  formatValue = formatCompact,
}: WaterfallProps) {
  const ct = useChartTheme();
  const describe = useCallback(
    (d: WaterfallStep) => `${d.label}: ${formatValue(d.value)}`,
    [formatValue],
  );
  const { active, getMarkProps, opacityFor } = useChartMarks(data, describe, onSelect, ct.dimOpacity);

  const spans = useMemo(() => {
    let cursor = 0;
    return data.map((d) => {
      const from = d.start ?? cursor;
      const to = from + d.value;
      cursor = to;
      return [Math.min(from, to), Math.max(from, to)] as const;
    });
  }, [data]);

  if (data.length === 0) return <ChartEmpty height={height} />;

  const lo = Math.min(0, ...spans.map(([a]) => a));
  const hi = Math.max(...spans.map(([, b]) => b), lo + 1);
  const colorFor = (d: WaterfallStep) =>
    d.tone ? ct.status[d.tone] : d.value < 0 ? ct.status.danger : ct.categorical[0];

  const layout = ({ width, height: h }: ChartSize) => {
    const labelW = Math.min(LABEL_WIDTH, width * 0.35);
    const x = scaleLinear([lo, hi], [labelW + 8, Math.max(labelW + 9, width - 48)]);
    const rowH = h / data.length;
    return { labelW, x, rowH };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { x, rowH } = layout(size);
    const [a, b] = spans[active];
    const d = data[active];
    return {
      x: (x(a) + x(b)) / 2,
      y: rowH * active + rowH * (ROW_GAP / 2),
      content: (
        <TooltipBody
          title={d.label}
          rows={[
            { label: "Value", value: formatValue(d.value), color: colorFor(d) },
            { label: "Starts at", value: formatValue(d.value < 0 ? b : a) },
          ]}
        />
      ),
    };
  };

  return (
    <ChartFrame label={label} height={height} tooltip={tooltip}>
      {(size) => {
        const { labelW, x, rowH } = layout(size);
        const barH = Math.max(2, rowH * (1 - ROW_GAP));
        return (
          <>
            <line x1={x(lo)} x2={x(lo)} y1={0} y2={size.height} stroke={ct.grid} aria-hidden="true" />
            {data.map((d, i) => {
              const [a, b] = spans[i];
              const cy = rowH * i + rowH / 2;
              return (
                <g key={i} {...getMarkProps(i)} opacity={opacityFor(i)}>
                  <rect x={0} y={rowH * i} width={size.width} height={rowH} fill="transparent" />
                  <text
                    x={labelW}
                    y={cy}
                    dy="0.32em"
                    textAnchor="end"
                    fontSize={AXIS_FONT_SIZE}
                    fill={ct.axis}
                  >
                    {d.label}
                  </text>
                  <rect
                    x={x(a)}
                    y={cy - barH / 2}
                    width={Math.max(2, x(b) - x(a))}
                    height={barH}
                    rx={3}
                    fill={colorFor(d)}
                  />
                  <text
                    x={x(b) + 4}
                    y={cy}
                    dy="0.32em"
                    fontSize={AXIS_FONT_SIZE}
                    fill={ct.axis}
                    aria-hidden="true"
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
