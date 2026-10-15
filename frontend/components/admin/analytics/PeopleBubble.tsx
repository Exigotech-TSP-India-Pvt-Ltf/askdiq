"use client";

import { useCallback, useMemo } from "react";
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

/**
 * Packs circles (radius = sqrt(value)) around the centre, largest first,
 * along an Archimedean spiral. Unit space; the caller scales to fit.
 */
export function packCircles(values: readonly number[]): PackedCircle[] {
  const order = values.map((v, i) => ({ i, r: Math.sqrt(Math.max(0, v)) })).sort((a, b) => b.r - a.r);
  const placed: (PackedCircle & { i: number })[] = [];
  for (const { i, r } of order) {
    let t = 0;
    let spot: PackedCircle = { x: 0, y: 0, r };
    for (let step = 0; step < MAX_STEPS; step++) {
      const x = t * Math.cos(t * 4);
      const y = t * Math.sin(t * 4);
      const free = placed.every((p) => Math.hypot(p.x - x, p.y - y) >= p.r + r + PADDING);
      if (free) {
        spot = { x, y, r };
        break;
      }
      t += SPIRAL_STEP;
    }
    placed.push({ ...spot, i });
  }
  const out: PackedCircle[] = new Array(values.length);
  for (const p of placed) out[p.i] = { x: p.x, y: p.y, r: p.r };
  return out;
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

  if (data.length === 0) return <ChartEmpty message="No people in this selection." height={height} />;

  const fit = (width: number, h: number) => {
    const minX = Math.min(...packed.map((c) => c.x - c.r));
    const maxX = Math.max(...packed.map((c) => c.x + c.r));
    const minY = Math.min(...packed.map((c) => c.y - c.r));
    const maxY = Math.max(...packed.map((c) => c.y + c.r));
    const k = Math.min((width - 4) / Math.max(1e-6, maxX - minX), (h - 4) / Math.max(1e-6, maxY - minY));
    const ox = width / 2 - ((minX + maxX) / 2) * k;
    const oy = h / 2 - ((minY + maxY) / 2) * k;
    return packed.map((c) => ({ x: ox + c.x * k, y: oy + c.y * k, r: c.r * k }));
  };

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
