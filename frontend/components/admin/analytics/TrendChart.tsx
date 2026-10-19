"use client";

import { useCallback, useId } from "react";
import {
  AXIS_FONT_SIZE,
  ChartEmpty,
  ChartFrame,
  TooltipBody,
  niceMax,
  scaleLinear,
  ticks,
  useChartMarks,
  type ChartSize,
} from "@/components/charts/primitives";
import { AXIS_TITLE_SPACE, AxisTitles, axisDescription } from "@/components/charts/cartesian";
import type { TrendDay } from "@/lib/insights-api";
import { useInsightsPalette } from "./colors";
import { dayLabel } from "./format";

export interface TrendChartProps {
  days: readonly TrendDay[];
  /** Day selected through `setDay()` (from = to = day), if any. */
  selectedDay?: string | null;
  onSelectDay?: (day: string) => void;
  height?: number;
}

const MARGIN = { t: 12, r: 14, b: 26 + AXIS_TITLE_SPACE, l: 30 + AXIS_TITLE_SPACE };
const AXIS_TITLES = { xAxisLabel: "Date", yAxisLabel: "Count per day" };
const MAX_X_LABELS = 7;
const Y_TICKS = 3;

/** Monotone cubic path (d3 `curveMonotoneX`) through the points. */
export function monotonePath(points: readonly [number, number][]): string {
  const n = points.length;
  if (n === 0) return "";
  if (n === 1) return `M${points[0][0]},${points[0][1]}`;
  const dx: number[] = [];
  const slope: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    dx.push(points[i + 1][0] - points[i][0]);
    slope.push(dx[i] === 0 ? 0 : (points[i + 1][1] - points[i][1]) / dx[i]);
  }
  const tangent = points.map((_, i) => {
    if (i === 0) return slope[0];
    if (i === n - 1) return slope[n - 2];
    const a = slope[i - 1];
    const b = slope[i];
    return a * b <= 0 ? 0 : (3 * (dx[i - 1] + dx[i])) / ((2 * dx[i] + dx[i - 1]) / a + (dx[i] + 2 * dx[i - 1]) / b);
  });
  let d = `M${points[0][0]},${points[0][1]}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    const [x0, y0] = points[i];
    const [x1, y1] = points[i + 1];
    d += `C${x0 + h},${y0 + h * tangent[i]} ${x1 - h},${y1 - h * tangent[i + 1]} ${x1},${y1}`;
  }
  return d;
}

/**
 * Daily substantive questions (area + line) and conversations (bars). Each
 * day is a button: click / Enter selects that single day, again clears it
 * (the HTML's `setDay()`).
 */
export function TrendChart({ days, selectedDay = null, onSelectDay, height }: TrendChartProps) {
  const { ct } = useInsightsPalette();
  const gradientId = useId().replace(/:/g, "");
  const describe = useCallback(
    (d: TrendDay) => `${dayLabel(d.day)}: ${d.questions} questions, ${d.conversations} conversations.`,
    [],
  );
  const select = useCallback((d: TrendDay) => onSelectDay?.(d.day), [onSelectDay]);
  const { active, getMarkProps } = useChartMarks(days, describe, onSelectDay ? select : undefined, ct.dimOpacity);

  if (days.length === 0) return <ChartEmpty message="Choose a valid date range." height={height} />;

  const peak = Math.max(1, ...days.map((d) => Math.max(d.questions, d.conversations)));
  const yMax = niceMax(peak);
  const layout = ({ width, height: h }: ChartSize) => {
    const x = scaleLinear([0, Math.max(1, days.length - 1)], [MARGIN.l, width - MARGIN.r]);
    const y = scaleLinear([0, yMax], [h - MARGIN.b, MARGIN.t]);
    const band = (width - MARGIN.l - MARGIN.r) / Math.max(1, days.length);
    return { x, y, band, h, width };
  };

  return (
    <ChartFrame
      label={`Daily substantive questions, ${days.length} days, peak ${Math.max(...days.map((d) => d.questions))}.`}
      height={height}
      description={axisDescription(AXIS_TITLES)}
      tooltip={(size) => {
        if (active === null) return null;
        const { x, y } = layout(size);
        const d = days[active];
        return {
          x: x(active),
          y: y(d.questions),
          content: (
            <TooltipBody
              title={dayLabel(d.day)}
              rows={[
                { label: "Questions", value: String(d.questions), color: ct.categorical[0] },
                { label: "Conversations", value: String(d.conversations), color: ct.sequential[1] },
              ]}
            />
          ),
        };
      }}
      footer={
        <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-fg-muted" aria-hidden="true">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: ct.categorical[0] }} />
            Substantive questions
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm" style={{ backgroundColor: ct.sequential[1] }} />
            Conversations
          </span>
        </div>
      }
    >
      {(size) => {
        const { x, y, band, h, width } = layout(size);
        const pts = days.map((d, i) => [x(i), y(d.questions)] as [number, number]);
        const line = monotonePath(pts);
        const area = `${line}L${x(days.length - 1)},${h - MARGIN.b}L${x(0)},${h - MARGIN.b}Z`;
        const step = Math.max(1, Math.ceil(days.length / MAX_X_LABELS));
        const barW = Math.max(1, Math.min(14, band * 0.6));
        return (
          <>
            <defs>
              <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor={ct.categorical[0]} stopOpacity={0.3} />
                <stop offset="100%" stopColor={ct.categorical[0]} stopOpacity={0} />
              </linearGradient>
            </defs>
            {ticks(yMax, Y_TICKS).map((t) => (
              <g key={t}>
                <line x1={MARGIN.l} x2={width - MARGIN.r} y1={y(t)} y2={y(t)} stroke={ct.grid} />
                <text x={MARGIN.l - 6} y={y(t) + 3} textAnchor="end" fontSize={AXIS_FONT_SIZE} fill={ct.axis}>
                  {Math.round(t)}
                </text>
              </g>
            ))}
            {days.map((d, i) =>
              i % step === 0 || i === days.length - 1 ? (
                <text key={d.day} x={x(i)} y={h - MARGIN.b + 19} textAnchor="middle" fontSize={AXIS_FONT_SIZE} fill={ct.axis}>
                  {dayLabel(d.day)}
                </text>
              ) : null,
            )}
            {days.map((d, i) =>
              d.conversations > 0 ? (
                <rect
                  key={`c-${d.day}`}
                  x={x(i) - barW / 2}
                  y={y(d.conversations)}
                  width={barW}
                  height={h - MARGIN.b - y(d.conversations)}
                  rx={2}
                  fill={ct.sequential[1]}
                  opacity={0.7}
                />
              ) : null,
            )}
            <AxisTitles
              {...AXIS_TITLES}
              area={{ x0: MARGIN.l, x1: width - MARGIN.r, y0: MARGIN.t, y1: h - MARGIN.b }}
              height={h}
              ct={ct}
            />
            <path d={area} fill={`url(#${gradientId})`} />
            <path d={line} fill="none" stroke={ct.categorical[0]} strokeWidth={2.2} />
            {days.map((d, i) => {
              const selected = d.day === selectedDay;
              return (
                <rect
                  key={d.day}
                  {...getMarkProps(i)}
                  data-day={d.day}
                  aria-pressed={onSelectDay ? selected : undefined}
                  x={x(i) - band / 2}
                  y={MARGIN.t}
                  width={band}
                  height={h - MARGIN.t - MARGIN.b}
                  fill={ct.categorical[0]}
                  fillOpacity={selected ? 0.14 : active === i ? 0.07 : 0}
                />
              );
            })}
          </>
        );
      }}
    </ChartFrame>
  );
}
