"use client";

import { useCallback, useMemo } from "react";
import { AXIS_FONT_SIZE, ChartEmpty, ChartFrame, TooltipBody, useChartMarks } from "@/components/charts/primitives";
import { arcPath } from "./arc";
import { useInsightsPalette } from "./colors";

export interface HourOfDayChartProps {
  /** 24 counts, index = hour in the viewer's zone (local time). */
  hours: readonly number[];
  height?: number;
}

interface HourDatum {
  hour: number;
  value: number;
}

const HOURS = 24;
const GAP = 0.018;
const HOUR_LABELS = [0, 6, 12, 18];

/** Radial bar chart of substantive questions by hour of day (the HTML's `radial()`). */
export function HourOfDayChart({ hours, height = 300 }: HourOfDayChartProps) {
  const palette = useInsightsPalette();
  const { ct } = palette;
  const data = useMemo<HourDatum[]>(
    () => Array.from({ length: HOURS }, (_, hour) => ({ hour, value: hours[hour] ?? 0 })),
    [hours],
  );
  const total = data.reduce((s, d) => s + d.value, 0);
  const max = Math.max(1, ...data.map((d) => d.value));
  const peak = data.find((d) => d.value === max && total > 0)?.hour ?? null;
  const describe = useCallback((d: HourDatum) => `${d.hour}:00, ${d.value} questions`, []);
  const { active, getMarkProps, opacityFor } = useChartMarks(data, describe, undefined, ct.dimOpacity);

  if (total === 0) return <ChartEmpty message="No questions in this selection." height={height} />;

  const geometry = (width: number, h: number) => {
    const side = Math.min(width, h);
    return { cx: width / 2, cy: h / 2, r0: side * 0.17, r1: side * 0.46 };
  };
  // Square-root radius, like d3.scaleSqrt().
  const radius = (v: number, r0: number, r1: number) => r0 + Math.sqrt(v / max) * (r1 - r0);
  const angle = (h: number) => (h / HOURS) * 2 * Math.PI;

  return (
    <ChartFrame
      label={`Radial chart of questions by hour of day, 24 hours${peak === null ? "" : `, peak hour ${peak}:00 with ${max} questions`}.`}
      height={height}
      tooltip={({ width, height: h }) => {
        if (active === null) return null;
        const { cx, cy, r0, r1 } = geometry(width, h);
        const d = data[active];
        const a = angle(d.hour + 0.5);
        const r = radius(d.value, r0, r1);
        return {
          x: cx + r * Math.sin(a),
          y: cy - r * Math.cos(a),
          content: (
            <TooltipBody
              title={`${d.hour}:00 – ${d.hour + 1}:00`}
              caption="Hour of day (local time)"
              rows={[{ label: "Questions", value: String(d.value) }]}
            />
          ),
        };
      }}
    >
      {({ width, height: h }) => {
        const { cx, cy, r0, r1 } = geometry(width, h);
        return (
          <>
            <g transform={`translate(${cx},${cy})`}>
              {data.map((d, i) => (
                <path
                  key={d.hour}
                  {...getMarkProps(i)}
                  d={arcPath(r0, radius(d.value, r0, r1), angle(d.hour), angle(d.hour + 1) - GAP)}
                  fill={palette.hour(d.value, max)}
                  opacity={opacityFor(i)}
                />
              ))}
              <text textAnchor="middle" y={-2} fontSize={16} fontWeight={600} fill={ct.tooltip.text}>
                {total}
              </text>
              <text textAnchor="middle" y={13} fontSize={AXIS_FONT_SIZE} fill={ct.axis}>
                questions
              </text>
            </g>
            {HOUR_LABELS.map((hr) => {
              const a = angle(hr);
              return (
                <text
                  key={hr}
                  x={cx + Math.sin(a) * (r1 + 14)}
                  y={cy - Math.cos(a) * (r1 + 14) + 3}
                  textAnchor="middle"
                  fontSize={AXIS_FONT_SIZE}
                  fill={ct.axis}
                >
                  {hr}:00
                </text>
              );
            })}
          </>
        );
      }}
    </ChartFrame>
  );
}
