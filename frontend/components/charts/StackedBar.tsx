"use client";

import { useCallback, useMemo, useState } from "react";
import { useChartTheme } from "@/lib/use-chart-theme";
import {
  AXIS_FONT_SIZE,
  ChartEmpty,
  ChartFrame,
  TooltipBody,
  formatCompact,
  niceMax,
  scaleLinear,
  useChartMarks,
  type ChartSize,
} from "./primitives";
import { Legend, XLabels, YGrid, num, plotArea, seriesColor, type SeriesDef } from "./cartesian";

/** One clickable segment: the row it belongs to and its series. */
export interface BarSegment<T> {
  row: T;
  series: string;
  value: number;
}

export interface StackedBarProps<T extends object> {
  /** One row per category, e.g. `{ day: "2026-10-09", new: 4, returning: 12 }`. */
  data: readonly T[];
  xKey: Extract<keyof T, string>;
  series: readonly SeriesDef<Extract<keyof T, string>>[];
  /** Click / Enter on a segment. */
  onSelect?: (segment: BarSegment<T>) => void;
  /** 100 % stacked (share of the row total). */
  percent?: boolean;
  /** "horizontal" draws ranked bars (labels on the left); default "vertical". */
  orientation?: "vertical" | "horizontal";
  /** Formats the category label (e.g. a day as "9 Oct"). */
  formatLabel?: (value: string) => string;
  label?: string;
  height?: number;
  formatValue?: (value: number) => string;
}

const pct = (v: number) => `${Math.round(v)}%`;
const BAR_GAP = 0.25;
const H_LABEL_WIDTH = 140;

/**
 * Stacked (or 100 % stacked) bars with one keyboard / click mark per segment,
 * so a segment click can filter by category AND series (newplan §15.4:
 * new vs returning, outcome 100 % bars, top-cited documents).
 */
export function StackedBar<T extends object>({
  data,
  xKey,
  series,
  onSelect,
  percent = false,
  orientation = "vertical",
  formatLabel = (v) => v,
  label = "Stacked bar chart",
  height,
  formatValue = formatCompact,
}: StackedBarProps<T>) {
  const ct = useChartTheme();
  const [hoverSeries, setHoverSeries] = useState<string | null>(null);
  const category = useCallback((row: T) => String((row as Record<string, unknown>)[xKey] ?? ""), [xKey]);

  const { segments, spans, max } = useMemo(() => {
    const segs: BarSegment<T>[] = [];
    const sp: { row: number; si: number; lo: number; hi: number }[] = [];
    let top = 0;
    data.forEach((row, ri) => {
      const total = series.reduce((sum, s) => sum + num(row, s.key), 0);
      let cursor = 0;
      series.forEach((s, si) => {
        const raw = num(row, s.key);
        const v = percent ? (total > 0 ? (raw / total) * 100 : 0) : raw;
        if (raw > 0) {
          segs.push({ row, series: s.key, value: raw });
          sp.push({ row: ri, si, lo: cursor, hi: cursor + v });
        }
        cursor += v;
      });
      top = Math.max(top, cursor);
    });
    return { segments: segs, spans: sp, max: percent ? 100 : niceMax(top) };
  }, [data, percent, series]);

  const seriesLabel = useCallback(
    (key: string) => series.find((s) => s.key === key)?.label ?? key,
    [series],
  );
  const describe = useCallback(
    (seg: BarSegment<T>) => `${formatLabel(category(seg.row))}, ${seriesLabel(seg.series)}: ${formatValue(seg.value)}`,
    [category, formatLabel, formatValue, seriesLabel],
  );
  const { active, getMarkProps, opacityFor } = useChartMarks(segments, describe, onSelect, ct.dimOpacity);

  if (data.length === 0 || series.length === 0 || segments.length === 0) return <ChartEmpty height={height} />;

  const horizontal = orientation === "horizontal";

  const layout = ({ width, height: h }: ChartSize) => {
    if (horizontal) {
      const labelW = Math.min(H_LABEL_WIDTH, width * 0.4);
      const band = h / data.length;
      const v = scaleLinear([0, max], [labelW + 8, Math.max(labelW + 9, width - 40)]);
      const rect = (s: (typeof spans)[number]) => ({
        x: v(s.lo),
        y: band * s.row + (band * BAR_GAP) / 2,
        w: Math.max(1, v(s.hi) - v(s.lo)),
        h: band * (1 - BAR_GAP),
      });
      return { labelW, band, v, rect, area: null };
    }
    const area = plotArea(width, h);
    const band = (area.x1 - area.x0) / data.length;
    const v = scaleLinear([0, max], [area.y1, area.y0]);
    const rect = (s: (typeof spans)[number]) => ({
      x: area.x0 + band * s.row + (band * BAR_GAP) / 2,
      y: v(s.hi),
      w: Math.max(1, band * (1 - BAR_GAP)),
      h: Math.max(0, v(s.lo) - v(s.hi)),
    });
    return { labelW: 0, band, v, rect, area };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { rect } = layout(size);
    const r = rect(spans[active]);
    const seg = segments[active];
    return {
      x: r.x + r.w / 2,
      y: r.y,
      content: (
        <TooltipBody
          title={formatLabel(category(seg.row))}
          rows={series.map((s, i) => ({
            label: s.label,
            value: formatValue(num(seg.row, s.key)),
            color: seriesColor(ct, s, i),
          }))}
        />
      ),
    };
  };

  return (
    <ChartFrame
      label={label}
      height={height}
      tooltip={tooltip}
      footer={series.length > 1 ? <Legend series={series} ct={ct} onHover={setHoverSeries} /> : undefined}
    >
      {(size) => {
        const { area, band, labelW, rect } = layout(size);
        return (
          <>
            {area ? (
              <>
                <YGrid area={area} max={max} ct={ct} format={percent ? pct : formatValue} />
                <XLabels
                  labels={data.map((row) => formatLabel(category(row)))}
                  xAt={(i) => area.x0 + band * i + band / 2}
                  area={area}
                  ct={ct}
                />
              </>
            ) : (
              data.map((row, i) => (
                <text
                  key={`l-${i}`}
                  x={labelW}
                  y={band * i + band / 2}
                  dy="0.32em"
                  textAnchor="end"
                  fontSize={AXIS_FONT_SIZE}
                  fill={ct.axis}
                  aria-hidden="true"
                >
                  {truncate(formatLabel(category(row)), 22)}
                </text>
              ))
            )}
            {spans.map((s, i) => {
              const r = rect(s);
              const color = seriesColor(ct, series[s.si], s.si);
              const dim = hoverSeries !== null && hoverSeries !== series[s.si].key;
              return (
                <rect
                  key={i}
                  {...getMarkProps(i)}
                  data-series={series[s.si].key}
                  x={r.x}
                  y={r.y}
                  width={r.w}
                  height={r.h}
                  rx={2}
                  fill={color}
                  opacity={dim ? ct.dimOpacity : opacityFor(i)}
                />
              );
            })}
          </>
        );
      }}
    </ChartFrame>
  );
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
