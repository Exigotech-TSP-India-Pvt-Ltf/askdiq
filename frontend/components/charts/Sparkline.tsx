"use client";

import { useCallback, useId, useMemo } from "react";
import { useChartTheme } from "@/lib/use-chart-theme";
import type { ChartTheme } from "@/lib/theme-tokens";
import {
  ChartFrame,
  TooltipBody,
  formatCompact,
  scaleLinear,
  useChartMarks,
  type ChartSize,
  type SelectHandler,
} from "./primitives";

/**
 * Empty sparkline: a quiet dashed baseline instead of a boxed placeholder.
 * Also used for a single point (a one-day range), where a lone dot read
 * as a rendering glitch: a trend needs at least two points.
 */
function SparkEmpty({ height, message = "No trend data" }: { height: number; message?: string }) {
  return (
    <div role="status" className="flex w-full items-end gap-2" style={{ height }}>
      <span aria-hidden="true" className="mb-1.5 h-px flex-1 border-t border-dashed border-line-brand dark:border-brand-500/25" />
      <span className="text-[11px] text-fg-subtle">{message}</span>
    </div>
  );
}

/** Points a sparkline needs to draw a trend. */
export const MIN_SPARK_POINTS = 2;

export interface SparkPoint {
  label: string;
  value: number;
}

export type SparkTone = "brand" | keyof ChartTheme["status"];

export interface SparklineProps {
  data: readonly SparkPoint[];
  onSelect?: SelectHandler<SparkPoint>;
  /** Accessible name, e.g. "Queries, last 7 days". */
  label?: string;
  tone?: SparkTone;
  /** Fill under the line. Default true. */
  area?: boolean;
  height?: number;
  formatValue?: (value: number) => string;
  className?: string;
}

const PAD = 3;

/** Tiny trend line for KPI cards and table cells. */
export function Sparkline({
  data,
  onSelect,
  label = "Trend",
  tone = "brand",
  area = true,
  height = 40,
  formatValue = formatCompact,
  className,
}: SparklineProps) {
  const ct = useChartTheme();
  const gradientId = useId();
  const color = tone === "brand" ? ct.categorical[0] : ct.status[tone];
  const describe = useCallback((d: SparkPoint) => `${d.label}: ${formatValue(d.value)}`, [formatValue]);
  const { active, getMarkProps } = useChartMarks(data, describe, onSelect, ct.dimOpacity);

  const [min, max] = useMemo(() => {
    const values = data.map((d) => d.value);
    return [Math.min(...values), Math.max(...values)];
  }, [data]);

  if (data.length === 0) return <SparkEmpty height={height} />;
  if (data.length < MIN_SPARK_POINTS) return <SparkEmpty height={height} message="Not enough data for a trend" />;

  const layout = ({ width, height: h }: ChartSize) => {
    const x = data.length === 1 ? () => width / 2 : scaleLinear([0, data.length - 1], [PAD, width - PAD]);
    const y = scaleLinear([min, max === min ? min + 1 : max], [h - PAD, PAD]);
    return data.map((d, i) => [x(i), y(d.value)] as const);
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const [px, py] = layout(size)[active];
    const d = data[active];
    return { x: px, y: py, content: <TooltipBody rows={[{ label: d.label, value: formatValue(d.value), color }]} /> };
  };

  return (
    <ChartFrame label={label} height={height} className={className} tooltip={tooltip}>
      {(size) => {
        const h = size.height;
        const points = layout(size);
        const line = points.map(([px, py], i) => `${i === 0 ? "M" : "L"}${px},${py}`).join(" ");
        const fill = `${line} L${points[points.length - 1][0]},${h} L${points[0][0]},${h} Z`;
        const step = data.length > 1 ? (size.width - 2 * PAD) / (data.length - 1) : size.width;
        return (
          <>
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.3} />
                <stop offset="100%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            {area && data.length > 1 && <path d={fill} fill={`url(#${gradientId})`} aria-hidden="true" />}
            {data.length > 1 && (
              <path
                d={line}
                fill="none"
                stroke={color}
                strokeWidth={1.75}
                strokeLinejoin="round"
                aria-hidden="true"
                data-testid="spark-line"
              />
            )}
            {points.map(([px, py], i) => (
              <g key={i} {...getMarkProps(i)}>
                <rect x={px - step / 2} y={0} width={step} height={h} fill="transparent" />
                {(active === i || data.length === 1 || i === data.length - 1) && (
                  <circle cx={px} cy={py} r={active === i ? 3.5 : 2.5} fill={color} />
                )}
              </g>
            ))}
          </>
        );
      }}
    </ChartFrame>
  );
}
