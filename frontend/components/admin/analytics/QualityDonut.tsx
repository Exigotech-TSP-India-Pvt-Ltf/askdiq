"use client";

import { useCallback, useMemo } from "react";
import { AXIS_FONT_SIZE, ChartEmpty, ChartFrame, TooltipBody, useChartMarks } from "@/components/charts/primitives";
import type { QualityItem } from "@/lib/insights-api";
import { arcPath } from "./arc";
import { useInsightsPalette } from "./colors";

/** Fixed slice order of the HTML's `donut()`; codes not listed follow in API order. */
export const QUALITY_ORDER = ["OK", "CORR", "SCOPE", "PART", "FB", "CLAR", "OFF", "FALSE"] as const;

export interface QualityDonutProps {
  items: readonly QualityItem[];
  /** Highlighted (filtered) code. */
  selected?: string;
  onSelect?: (item: QualityItem) => void;
  height?: number;
}

const PAD = 0.012;

export function orderQuality(items: readonly QualityItem[]): QualityItem[] {
  const rank = (code: string) => {
    const i = (QUALITY_ORDER as readonly string[]).indexOf(code);
    return i < 0 ? QUALITY_ORDER.length : i;
  };
  return items.filter((i) => i.questions > 0).sort((a, b) => rank(a.quality) - rank(b.quality));
}

/** How questions were answered: bot-quality donut with a counted legend. */
export function QualityDonut({ items, selected, onSelect, height = 220 }: QualityDonutProps) {
  const palette = useInsightsPalette();
  const { ct } = palette;
  const data = useMemo(() => orderQuality(items), [items]);
  const total = data.reduce((s, d) => s + d.questions, 0);
  const describe = useCallback((d: QualityItem) => `${d.label}: ${d.questions} questions.`, []);
  const { active, getMarkProps, opacityFor } = useChartMarks(data, describe, onSelect, ct.dimOpacity);

  if (total === 0) return <ChartEmpty message="No questions in this selection." height={height} />;

  const angles: [number, number][] = [];
  let acc = 0;
  for (const d of data) {
    const sweep = (d.questions / total) * 2 * Math.PI;
    angles.push([acc + PAD / 2, acc + sweep - PAD / 2]);
    acc += sweep;
  }
  const geometry = (width: number, h: number) => {
    const r = Math.max(10, Math.min(width, h) / 2 - 6);
    return { r, cx: r + 6, cy: h / 2 };
  };

  return (
    <ChartFrame
      label={`Donut of answer types for ${total} questions.`}
      height={height}
      tooltip={({ width, height: h }) => {
        if (active === null) return null;
        const { r, cx, cy } = geometry(width, h);
        const d = data[active];
        const mid = (angles[active][0] + angles[active][1]) / 2;
        return {
          x: cx + r * 0.8 * Math.sin(mid),
          y: cy - r * 0.8 * Math.cos(mid),
          content: (
            <TooltipBody
              title={d.label}
              rows={[{ label: "Questions", value: `${d.questions}, ${((100 * d.questions) / total).toFixed(0)}%`, color: palette.quality(d.quality, active) }]}
            />
          ),
        };
      }}
      footer={
        <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1" aria-label="Legend">
          {data.map((d, i) => (
            <li key={d.quality} className="inline-flex items-center text-[11px] text-fg-muted">
              <span className="mr-1.5 h-2.5 w-2.5 rounded-[3px]" style={{ backgroundColor: palette.quality(d.quality, i) }} aria-hidden="true" />
              {d.label}
              <b className="ml-1 font-semibold text-fg tabular-nums">{d.questions}</b>
            </li>
          ))}
        </ul>
      }
    >
      {({ width, height: h }) => {
        const { r, cx, cy } = geometry(width, h);
        return (
          <g transform={`translate(${cx},${cy})`}>
            {data.map((d, i) => (
              <path
                key={d.quality}
                {...getMarkProps(i)}
                aria-pressed={onSelect ? selected === d.quality : undefined}
                d={arcPath(r * 0.56, selected === d.quality ? r + 3 : r, angles[i][0], angles[i][1])}
                fill={palette.quality(d.quality, i)}
                opacity={opacityFor(i)}
              />
            ))}
            <text textAnchor="middle" y={-4} fontSize={17} fontWeight={600} fill={ct.tooltip.text}>
              {total}
            </text>
            <text textAnchor="middle" y={13} fontSize={AXIS_FONT_SIZE} fill={ct.axis}>
              questions
            </text>
          </g>
        );
      }}
    </ChartFrame>
  );
}
