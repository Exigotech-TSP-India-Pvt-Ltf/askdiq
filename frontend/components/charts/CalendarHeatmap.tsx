"use client";

import { useCallback, useMemo } from "react";
import { useChartTheme } from "@/lib/use-chart-theme";
import {
  AXIS_FONT_SIZE,
  ChartEmpty,
  ChartFrame,
  DEFAULT_CHART_WIDTH,
  TooltipBody,
  formatCompact,
  rampColor,
  useChartMarks,
  useElementSize,
  type ChartSize,
  type SelectHandler,
} from "./primitives";
import { AxisTitles, axisDescription, titleInsets, type AxisLabelProps } from "./cartesian";

export interface HeatCell {
  row: string;
  col: string;
  value: number;
}

/** `xAxisLabel` titles the columns (e.g. "Hour of day (UTC)"), `yAxisLabel` the rows (e.g. "Day of week"). */
export interface CalendarHeatmapProps extends AxisLabelProps {
  data: readonly HeatCell[];
  /** Row order (e.g. Mon..Sun). Defaults to first appearance in `data`. */
  rows?: readonly string[];
  /** Column order (e.g. 0..23). Defaults to first appearance in `data`. */
  cols?: readonly string[];
  onSelect?: SelectHandler<HeatCell>;
  label?: string;
  /**
   * Fixed height (px). Omitted with a finite `maxCellHeight`, the chart is as
   * tall as its rows + labels (no dead space); otherwise it fills the parent.
   */
  height?: number;
  formatValue?: (value: number) => string;
  /** Show every n-th column label. Default: about 12 labels. */
  colLabelEvery?: number;
  /** Tooltip name of the cell value (default "Value"), e.g. "Sessions". */
  valueLabel?: string;
  /**
   * Cap on a cell's width / height (px). Cells stretch to fill the width up
   * to the cap; a capped grid is centred with its axis titles and legend.
   */
  maxCellWidth?: number;
  maxCellHeight?: number;
  /** Narrowest cell (px): below it the grid scrolls sideways inside its card. */
  minCellWidth?: number;
  /** Gap between cells (px). */
  cellGap?: number;
  /** Value scale under the grid ("0 … max" swatches). */
  legend?: boolean;
}

const LEGEND_STEPS = 5;

function HeatLegend({ ramp, max, formatValue, valueLabel }: { ramp: readonly string[]; max: number; formatValue: (v: number) => string; valueLabel: string }) {
  const steps = Array.from({ length: LEGEND_STEPS }, (_, i) => (max * (i + 1)) / LEGEND_STEPS);
  return (
    <div className="mt-2 flex items-center gap-1.5 whitespace-nowrap text-[11px] text-fg-subtle" aria-label={`${valueLabel} scale: 0 to ${formatValue(max)}`} role="img">
      <span aria-hidden="true">{formatValue(0)}</span>
      {steps.map((v) => (
        <span key={v} aria-hidden="true" className="h-2.5 w-4 rounded-xs" style={{ backgroundColor: rampColor(ramp, v, max) }} />
      ))}
      <span aria-hidden="true">{formatValue(max)}</span>
      <span aria-hidden="true" className="ml-1">{valueLabel.toLowerCase()}</span>
    </div>
  );
}

const LEFT = 36;
const BOTTOM = 18;
const GAP = 2;
const MIN_CELL_WIDTH = 12;

function uniq(values: readonly string[]): string[] {
  return Array.from(new Set(values));
}

/** Weekday x hour (or any 2-D) intensity grid on the sequential teal ramp. */
export function CalendarHeatmap({
  data,
  rows,
  cols,
  onSelect,
  label = "Activity heatmap",
  height,
  formatValue = formatCompact,
  colLabelEvery,
  valueLabel = "Value",
  xAxisLabel,
  yAxisLabel,
  maxCellWidth = Number.POSITIVE_INFINITY,
  maxCellHeight = Number.POSITIVE_INFINITY,
  minCellWidth = MIN_CELL_WIDTH,
  cellGap = GAP,
  legend = false,
}: CalendarHeatmapProps) {
  const ct = useChartTheme();
  const titles = { xAxisLabel, yAxisLabel };
  const describe = useCallback(
    (c: HeatCell) => `${c.row} ${c.col}: ${formatValue(c.value)}`,
    [formatValue],
  );
  const { active, getMarkProps } = useChartMarks(data, describe, onSelect, ct.dimOpacity);
  const rowKeys = useMemo(() => rows ?? uniq(data.map((c) => c.row)), [data, rows]);
  const colKeys = useMemo(() => cols ?? uniq(data.map((c) => c.col)), [data, cols]);
  const max = useMemo(() => Math.max(0, ...data.map((c) => c.value)), [data]);
  // Width of the card body: below the grid's narrowest width it scrolls inside it.
  const [outerRef, outer] = useElementSize<HTMLDivElement>({ width: DEFAULT_CHART_WIDTH, height: 1 });

  const inset = titleInsets(titles);
  const left = LEFT + inset.left;
  const nRows = Math.max(1, rowKeys.length);
  const nCols = Math.max(1, colKeys.length);
  const minWidth = Math.ceil(left + nCols * minCellWidth);
  const scroll = outer.width < minWidth;
  const fitHeight =
    height ?? (Number.isFinite(maxCellHeight) ? Math.ceil(nRows * maxCellHeight + BOTTOM + inset.bottom) : undefined);

  const every = colLabelEvery ?? Math.max(1, Math.ceil(colKeys.length / 12));
  // Rough width of the longest column label (px): narrow cells label fewer columns so labels never overlap.
  const labelWidth = Math.max(0, ...colKeys.map((c) => c.length)) * AXIS_FONT_SIZE * 0.6 + 8;

  const layout = ({ width, height: h }: ChartSize) => {
    const availH = Math.max(2, h - BOTTOM - inset.bottom);
    const cw = Math.max(2, Math.min(maxCellWidth, (width - left) / nCols));
    const rh = Math.max(2, Math.min(maxCellHeight, availH / nRows));
    const gridW = cw * nCols;
    // Uncapped, this is the full available height; capped, the grid ends with its last row.
    const gridH = Math.min(availH, rh * nRows);
    // A capped grid (wide card) is centred, its row labels and y title with it.
    const shift = Math.max(0, (width - left - gridW) / 2);
    const pos = (c: HeatCell) => ({
      x: left + colKeys.indexOf(c.col) * cw,
      y: rowKeys.indexOf(c.row) * rh,
    });
    // Titles span the grid itself: x title centred under it, y title on its rows.
    const titleArea = { x0: left, x1: left + gridW, y0: 0, y1: gridH };
    const titleBottom = gridH + BOTTOM + inset.bottom;
    const stride = Math.max(every, Math.ceil(labelWidth / cw));
    return { cw, rh, gridW, shift, pos, stride, colLabelY: gridH + BOTTOM - 4, titleArea, titleBottom };
  };

  const tooltip = (size: ChartSize) => {
    if (active === null) return null;
    const { cw, pos, shift } = layout(size);
    const c = data[active];
    const p = pos(c);
    return {
      x: shift + p.x + cw / 2,
      y: p.y,
      content: (
        <TooltipBody title={`${c.row} · ${c.col}`} rows={[{ label: valueLabel, value: formatValue(c.value) }]} />
      ),
    };
  };

  const footer = legend
    ? (size: ChartSize) => {
        const { gridW, shift } = layout(size);
        return (
          <div data-testid="heatmap-legend" className="flex justify-center" style={{ marginLeft: shift + left, width: gridW }}>
            <HeatLegend ramp={ct.sequential} max={max} formatValue={formatValue} valueLabel={valueLabel} />
          </div>
        );
      }
    : undefined;

  return (
    <div
      ref={outerRef}
      data-testid="calendar-heatmap"
      className={`w-full ${fitHeight === undefined ? "h-full" : ""} ${scroll ? "overflow-x-auto overscroll-x-contain" : ""}`}
    >
      {data.length === 0 ? (
        <ChartEmpty height={height} />
      ) : (
        <div className={fitHeight === undefined ? "h-full" : ""} style={scroll ? { width: minWidth } : undefined}>
          <ChartFrame
            label={label}
            height={fitHeight}
            tooltip={tooltip}
            description={axisDescription(titles)}
            footer={footer}
          >
            {(size) => {
              const { cw, rh, shift, pos, stride, colLabelY, titleArea, titleBottom } = layout(size);
              return (
                <g transform={shift ? `translate(${shift} 0)` : undefined}>
                  <AxisTitles {...titles} area={titleArea} height={titleBottom} ct={ct} />
                  {rowKeys.map((r, ri) => (
                    <g key={`r-${r}`} aria-hidden="true">
                      <text x={left - 6} y={ri * rh + rh / 2} dy="0.32em" textAnchor="end" fontSize={AXIS_FONT_SIZE} fill={ct.axis}>
                        {r}
                      </text>
                      {colKeys.map((c, ci) => (
                        <rect
                          key={c}
                          x={left + ci * cw + cellGap / 2}
                          y={ri * rh + cellGap / 2}
                          width={cw - cellGap}
                          height={rh - cellGap}
                          rx={2}
                          fill={ct.track}
                          fillOpacity={0.5}
                        />
                      ))}
                    </g>
                  ))}
                  {colKeys.map((c, ci) =>
                    ci % stride === 0 ? (
                      <text
                        key={`c-${c}`}
                        x={left + ci * cw + cw / 2}
                        y={colLabelY}
                        textAnchor="middle"
                        fontSize={AXIS_FONT_SIZE}
                        fill={ct.axis}
                        aria-hidden="true"
                      >
                        {c}
                      </text>
                    ) : null,
                  )}
                  {data.map((cell, i) => {
                    const p = pos(cell);
                    if (p.x < left || p.y < 0) return null;
                    return (
                      <rect
                        key={i}
                        {...getMarkProps(i)}
                        x={p.x + cellGap / 2}
                        y={p.y + cellGap / 2}
                        width={cw - cellGap}
                        height={rh - cellGap}
                        rx={2}
                        fill={rampColor(ct.sequential, cell.value, max)}
                        stroke={active === i ? ct.axis : "none"}
                      />
                    );
                  })}
                </g>
              );
            }}
          </ChartFrame>
        </div>
      )}
    </div>
  );
}
