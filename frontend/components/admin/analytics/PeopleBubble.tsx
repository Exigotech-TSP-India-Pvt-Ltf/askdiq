"use client";

import { useCallback, useMemo, useRef } from "react";
import { ChartEmpty, ChartFrame, TooltipBody, useChartMarks } from "@/components/charts/primitives";
import type { PersonItem } from "@/lib/insights-api";
import { useInsightsPalette } from "./colors";

export interface PeopleBubbleProps {
  items: readonly PersonItem[];
  /** Highlighted (filtered) person_id. */
  selected?: string;
  onSelect?: (item: PersonItem) => void;
  height?: number;
}

export interface PackedCircle {
  x: number;
  y: number;
  r: number;
}

const PADDING = 0.12;
const SPIRAL_STEP = 0.05;
const MAX_STEPS = 200_000;
const LABEL_MIN_R = 13;
const FILL_OPACITY = 0.78;
const LEGEND = ["Served", "Partly served", "Not served"] as const;

/** Offset that keeps grid cell coordinates positive inside one numeric key. */
const CELL_KEY_OFFSET = 1 << 20;
const CELL_KEY_STRIDE = 2 * CELL_KEY_OFFSET;
const MIN_CELL_RADIUS = 0.5;

/**
 * Packs circles (radius = sqrt(value)) around the centre, largest first,
 * along an Archimedean spiral. Unit space; the caller scales to fit.
 *
 * Same layout as the naive search (every spiral point tested against every
 * placed circle) without its O(n² × steps) cost, which froze the page for
 * minutes with ~2,000 people:
 * - a uniform grid: each placed circle is registered in every cell a later,
 *   no-larger circle could collide from, so a candidate point only checks
 *   the circles of its own cell;
 * - circles of equal radius resume the spiral where the previous one landed:
 *   every earlier point was already blocked for that radius and the placed
 *   set only grows, so they stay blocked.
 */
export function packCircles(values: readonly number[]): PackedCircle[] {
  const order = values.map((v, i) => ({ i, r: Math.sqrt(Math.max(0, v)) })).sort((a, b) => b.r - a.r);
  const out: PackedCircle[] = new Array(values.length);
  if (order.length === 0) return out;
  const cell = 2 * Math.max(order[order.length - 1].r, MIN_CELL_RADIUS) + PADDING;
  const grid = new Map<number, PackedCircle[]>();
  const cellOf = (v: number) => Math.floor(v / cell) + CELL_KEY_OFFSET;
  const resume = new Map<number, { step: number; t: number }>();
  for (const { i, r } of order) {
    const from = resume.get(r);
    let step = from?.step ?? 0;
    let t = from?.t ?? 0;
    let spot: PackedCircle = { x: 0, y: 0, r };
    for (; step < MAX_STEPS; step++) {
      const x = t * Math.cos(t * 4);
      const y = t * Math.sin(t * 4);
      const near = grid.get(cellOf(x) * CELL_KEY_STRIDE + cellOf(y));
      if (!near || near.every((p) => Math.hypot(p.x - x, p.y - y) >= p.r + r + PADDING)) {
        spot = { x, y, r };
        break;
      }
      t += SPIRAL_STEP;
    }
    resume.set(r, { step, t });
    out[i] = spot;
    // Later circles are no larger than this one: they collide with it only
    // from within 2r + padding of its centre.
    const reach = 2 * r + PADDING;
    for (let cx = cellOf(spot.x - reach); cx <= cellOf(spot.x + reach); cx++) {
      for (let cy = cellOf(spot.y - reach); cy <= cellOf(spot.y + reach); cy++) {
        const key = cx * CELL_KEY_STRIDE + cy;
        const bucket = grid.get(key);
        if (bucket) bucket.push(spot);
        else grid.set(key, [spot]);
      }
    }
  }
  return out;
}

/** Scales the unit-space packing into a `width` × `height` box (4 px margin). */
export function fitCircles(packed: readonly PackedCircle[], width: number, height: number): PackedCircle[] {
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const c of packed) {
    minX = Math.min(minX, c.x - c.r);
    maxX = Math.max(maxX, c.x + c.r);
    minY = Math.min(minY, c.y - c.r);
    maxY = Math.max(maxY, c.y + c.r);
  }
  const k = Math.min((width - 4) / Math.max(1e-6, maxX - minX), (height - 4) / Math.max(1e-6, maxY - minY));
  const ox = width / 2 - ((minX + maxX) / 2) * k;
  const oy = height / 2 - ((minY + maxY) / 2) * k;
  return packed.map((c) => ({ x: ox + c.x * k, y: oy + c.y * k, r: c.r * k }));
}

/** Conversations per probable person, coloured by their most common outcome (the HTML's `bubble()`). */
export function PeopleBubble({ items, selected, onSelect, height = 220 }: PeopleBubbleProps) {
  const palette = useInsightsPalette();
  const { ct } = palette;
  const data = useMemo(() => items.filter((p) => p.conversations > 0), [items]);
  const packed = useMemo(() => packCircles(data.map((p) => p.conversations)), [data]);
  const describe = useCallback(
    (p: PersonItem) => `${p.person}: ${p.conversations} conversation(s), mostly ${p.dominant_outcome}.`,
    [],
  );
  const { active, getMarkProps, opacityFor } = useChartMarks(data, describe, onSelect, ct.dimOpacity);
  // Hover re-renders every mark: reuse the scaled layout for an unchanged box.
  const fitted = useRef<{ packed: readonly PackedCircle[]; width: number; height: number; out: PackedCircle[] } | null>(null);
  const fit = (width: number, h: number): PackedCircle[] => {
    const last = fitted.current;
    if (last && last.packed === packed && last.width === width && last.height === h) return last.out;
    const out = fitCircles(packed, width, h);
    fitted.current = { packed, width, height: h, out };
    return out;
  };

  if (data.length === 0) return <ChartEmpty message="No people in this selection." height={height} />;

  return (
    <ChartFrame
      label={`Bubble chart of ${data.length} people by number of conversations.`}
      height={height}
      tooltip={({ width, height: h }) => {
        if (active === null) return null;
        const c = fit(width, h)[active];
        const p = data[active];
        return {
          x: c.x,
          y: c.y - c.r,
          content: (
            <TooltipBody
              title={p.person}
              rows={[
                { label: "Conversations", value: String(p.conversations) },
                { label: "Mostly", value: p.dominant_outcome, color: palette.outcome(p.dominant_outcome) },
              ]}
            />
          ),
        };
      }}
      footer={
        <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1" aria-label="Legend">
          {LEGEND.map((o) => (
            <li key={o} className="inline-flex items-center text-[11px] text-fg-muted">
              <span className="mr-1.5 h-2.5 w-2.5 rounded-[3px]" style={{ backgroundColor: palette.outcome(o) }} aria-hidden="true" />
              Mostly {o}
            </li>
          ))}
        </ul>
      }
    >
      {({ width, height: h }) =>
        fit(width, h).map((c, i) => {
          const p = data[i];
          const fill = palette.outcome(p.dominant_outcome);
          const isSelected = selected === p.person_id;
          return (
            <g key={p.person_id} {...getMarkProps(i)} aria-pressed={onSelect ? isSelected : undefined} opacity={opacityFor(i)}>
              <circle
                cx={c.x}
                cy={c.y}
                r={c.r}
                fill={fill}
                fillOpacity={FILL_OPACITY}
                stroke={isSelected ? ct.tooltip.text : "none"}
                strokeWidth={isSelected ? 2 : 0}
              />
              {c.r > LABEL_MIN_R && (
                <text x={c.x} y={c.y + 4} textAnchor="middle" fontSize={Math.min(12, c.r * 0.5)} fontWeight={600} fill={palette.textOn(fill)}>
                  {p.conversations}
                </text>
              )}
            </g>
          );
        })
      }
    </ChartFrame>
  );
}
