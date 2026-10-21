"use client";

import { useCallback, useMemo } from "react";
import { AXIS_FONT_SIZE, ChartEmpty, ChartFrame, TooltipBody, useChartMarks } from "@/components/charts/primitives";
import { CONVERSATION_OUTCOMES, type FlowLink, type InsightsFlow } from "@/lib/insights-api";
import { useInsightsPalette } from "./colors";

export interface FlowSankeyProps {
  flow: InsightsFlow;
  /** Click on a band: toggles the user type and outcome filters (the HTML's `flow()`). */
  onSelect?: (link: FlowLink) => void;
  height?: number;
}

const MARGIN = { t: 16, b: 16 };
const GAP = 8;
const ROW = 34;
const MIN_HEIGHT = 190;
// Outcome colour at 0.45 so the bands stay apart on the dark card too (VA-30).
const LINK_OPACITY = 0.45;
const LINK_OPACITY_ACTIVE = 0.7;
/** Minimum vertical distance between two node labels (one text line). */
const LABEL_GAP = 14;

/**
 * Label y positions for nodes stacked top to bottom: each starts at its
 * node's centre and is pushed apart so no two are closer than `gap`, staying
 * inside [top, bottom]. Thin neighbouring nodes ("Mixed (11)", "Off-scope
 * request (3)") no longer print on top of each other (VA-30).
 */
export function spreadLabels(centers: readonly number[], gap: number, top: number, bottom: number): number[] {
  const out = [...centers];
  for (let i = 0; i < out.length; i += 1) out[i] = Math.max(out[i], i === 0 ? top : out[i - 1] + gap);
  for (let i = out.length - 1; i >= 0; i -= 1) out[i] = Math.min(out[i], i === out.length - 1 ? bottom : out[i + 1] - gap);
  return out;
}

interface Span {
  y0: number;
  y1: number;
}

/** User type to outcome flow ("Greeting only" excluded), drawn like the reference HTML. */
export function FlowSankey({ flow, onSelect, height }: FlowSankeyProps) {
  const palette = useInsightsPalette();
  const { ct } = palette;
  const outcomeOrder = useMemo(
    () =>
      CONVERSATION_OUTCOMES.filter((o) => o !== "Greeting only" && flow.outcomes.some((n) => n.name === o && n.conversations > 0)),
    [flow.outcomes],
  );
  const types = useMemo(() => flow.user_types.filter((t) => t.conversations > 0), [flow.user_types]);
  // Links in drawing order: by user type, then outcome (stacking order on both sides).
  const links = useMemo(
    () =>
      types.flatMap((t) =>
        outcomeOrder.flatMap((o) => flow.links.filter((l) => l.user_type === t.name && l.outcome === o && l.conversations > 0)),
      ),
    [flow.links, outcomeOrder, types],
  );
  const describe = useCallback((l: FlowLink) => `${l.user_type} to ${l.outcome}: ${l.conversations} conversations.`, []);
  const { active, getMarkProps } = useChartMarks(links, describe, onSelect, ct.dimOpacity);

  if (flow.total === 0 || links.length === 0) return <ChartEmpty message="No conversations in this selection." height={height} />;

  const chartHeight = height ?? Math.max(MIN_HEIGHT, types.length * ROW + 40);
  const layout = (width: number, h: number) => {
    const lx = 10;
    const rx = width - 10;
    const usable = h - MARGIN.t - MARGIN.b;
    const scale = (n: number) => (n / flow.total) * (usable - GAP * (Math.max(types.length, outcomeOrder.length) - 1));
    const left = new Map<string, Span>();
    let y = MARGIN.t;
    for (const t of types) {
      const hgt = scale(t.conversations);
      left.set(t.name, { y0: y, y1: y + hgt });
      y += hgt + GAP;
    }
    const right = new Map<string, Span>();
    y = MARGIN.t;
    for (const o of outcomeOrder) {
      const n = flow.outcomes.find((x) => x.name === o)?.conversations ?? 0;
      const hgt = scale(n);
      right.set(o, { y0: y, y1: y + hgt });
      y += hgt + GAP;
    }
    const leftCursor = new Map([...left].map(([k, v]) => [k, v.y0]));
    const rightCursor = new Map([...right].map(([k, v]) => [k, v.y0]));
    const bands = links.map((l) => {
      const hgt = scale(l.conversations);
      const a0 = leftCursor.get(l.user_type) ?? 0;
      const b0 = rightCursor.get(l.outcome) ?? 0;
      leftCursor.set(l.user_type, a0 + hgt);
      rightCursor.set(l.outcome, b0 + hgt);
      const c1 = width * 0.42;
      const c2 = width * 0.58;
      const d =
        `M${lx},${a0}C${c1},${a0} ${c2},${b0} ${rx},${b0}` +
        `L${rx},${b0 + hgt}C${c2},${b0 + hgt} ${c1},${a0 + hgt} ${lx},${a0 + hgt}Z`;
      return { d, mid: { x: width / 2, y: (a0 + b0) / 2 + hgt / 2 } };
    });
    const labelY = (spans: Span[]) =>
      spreadLabels(
        spans.map((s) => (s.y0 + s.y1) / 2),
        LABEL_GAP,
        MARGIN.t,
        h - MARGIN.b,
      );
    const leftLabels = labelY(types.flatMap((t) => (left.get(t.name) ? [left.get(t.name) as Span] : [])));
    const rightLabels = labelY(outcomeOrder.flatMap((o) => (right.get(o) ? [right.get(o) as Span] : [])));
    return { lx, rx, left, right, bands, leftLabels, rightLabels };
  };

  return (
    <ChartFrame
      label={`Flow from user type to outcome for ${flow.total} conversations.`}
      height={chartHeight}
      tooltip={({ width, height: h }) => {
        if (active === null) return null;
        const l = links[active];
        const { bands } = layout(width, h);
        return {
          x: bands[active].mid.x,
          y: bands[active].mid.y,
          content: <TooltipBody title={l.user_type} rows={[{ label: l.outcome, value: String(l.conversations), color: palette.outcome(l.outcome) }]} />,
        };
      }}
    >
      {({ width, height: h }) => {
        const { lx, rx, left, right, bands, leftLabels, rightLabels } = layout(width, h);
        return (
          <>
            {links.map((l, i) => (
              <path
                key={`${l.user_type}-${l.outcome}`}
                {...getMarkProps(i)}
                d={bands[i].d}
                fill={palette.outcome(l.outcome)}
                fillOpacity={active === i ? LINK_OPACITY_ACTIVE : LINK_OPACITY}
              />
            ))}
            {types.map((t, i) => {
              const s = left.get(t.name);
              if (!s) return null;
              return (
                <g key={t.name} aria-hidden="true">
                  <rect x={lx} y={s.y0} width={4} height={Math.max(2, s.y1 - s.y0)} fill={ct.axis} />
                  <text x={lx + 9} y={(leftLabels[i] ?? (s.y0 + s.y1) / 2) + 3} fontSize={AXIS_FONT_SIZE} fill={ct.tooltip.text}>
                    {`${t.name} (${t.conversations})`}
                  </text>
                </g>
              );
            })}
            {outcomeOrder.map((o, i) => {
              const s = right.get(o);
              if (!s) return null;
              return (
                <g key={o} aria-hidden="true">
                  <rect x={rx - 4} y={s.y0} width={4} height={Math.max(2, s.y1 - s.y0)} fill={palette.outcome(o)} />
                  <text x={rx - 9} y={(rightLabels[i] ?? (s.y0 + s.y1) / 2) + 3} textAnchor="end" fontSize={AXIS_FONT_SIZE} fill={ct.tooltip.text}>
                    {o}
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
