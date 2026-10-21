"use client";

import { useCallback } from "react";
import { AXIS_FONT_SIZE, ChartEmpty, ChartFrame, TooltipBody, useChartMarks } from "@/components/charts/primitives";
import type { DropoffItem } from "@/lib/insights-api";
import { useInsightsPalette } from "./colors";

export interface DropoffChartProps {
  items: readonly DropoffItem[];
  /** Highlighted (filtered) issue code. */
  selected?: string;
  onSelect?: (item: DropoffItem) => void;
}

const ROW = 30;
const MARGIN = { t: 6, b: 6, r: 30 };
/** Room under the rows for the value-axis title. */
const TITLE_SPACE = 18;
const MAX_LABEL_WIDTH = 170;
const LABEL_SHARE = 0.4;
const DOT_R = 6.5;

/** Where conversations dropped: issue counts over Partly / Not served conversations (lollipop). */
export function DropoffChart({ items, selected, onSelect }: DropoffChartProps) {
  const { ct } = useInsightsPalette();
  const rows = items.filter((i) => i.conversations > 0);
  const describe = useCallback((d: DropoffItem) => `${d.label}: ${d.conversations} conversations.`, []);
  const { active, getMarkProps, opacityFor } = useChartMarks(rows, describe, onSelect, ct.dimOpacity);

  if (rows.length === 0) return <ChartEmpty message="No dropped conversations in this selection." />;

  const height = rows.length * ROW + MARGIN.t + MARGIN.b + TITLE_SPACE;
  const max = Math.max(1, ...rows.map((r) => r.conversations));
  const geometry = (width: number) => {
    const left = Math.min(MAX_LABEL_WIDTH, Math.round(width * LABEL_SHARE));
    const x = (v: number) => left + (v / max) * Math.max(0, width - left - MARGIN.r);
    return { left, x };
  };
  const color = ct.status.danger;

  return (
    <ChartFrame
      label="Lollipop chart of drop reasons."
      height={height}
      tooltip={({ width }) => {
        if (active === null) return null;
        const d = rows[active];
        const { x } = geometry(width);
        return {
          x: x(d.conversations),
          y: MARGIN.t + active * ROW + ROW / 2 - DOT_R,
          content: <TooltipBody title={d.label} rows={[{ label: "Conversations", value: String(d.conversations), color }]} />,
        };
      }}
    >
      {({ width }) => {
        const { left, x } = geometry(width);
        const title = (
          <text
            key="x-title"
            x={left + Math.max(0, width - left - MARGIN.r) / 2}
            y={height - 4}
            textAnchor="middle"
            fontSize={AXIS_FONT_SIZE}
            fontWeight={500}
            fill={ct.axis}
            aria-hidden="true"
          >
            Conversations with this issue
          </text>
        );
        return [title, ...rows.map((d, i) => {
          const y = MARGIN.t + i * ROW + ROW / 2;
          const isSelected = selected === d.issue;
          return (
            <g key={d.issue}>
              <text x={left - 10} y={y + 4} textAnchor="end" fontSize={AXIS_FONT_SIZE} fill={ct.tooltip.text}>
                {d.label}
              </text>
              <g {...getMarkProps(i)} aria-pressed={onSelect ? isSelected : undefined} opacity={opacityFor(i)}>
                <rect x={left} y={y - ROW / 2} width={Math.max(0, width - left)} height={ROW} fill="transparent" />
                <line x1={left} x2={x(d.conversations)} y1={y} y2={y} stroke={color} strokeWidth={2.4} strokeLinecap="round" />
                <circle cx={x(d.conversations)} cy={y} r={isSelected ? DOT_R + 2 : DOT_R} fill={color} />
                <text x={x(d.conversations) + 11} y={y + 4} fontSize={AXIS_FONT_SIZE} fill={ct.axis}>
                  {d.conversations}
                </text>
              </g>
            </g>
          );
        })];
      }}
    </ChartFrame>
  );
}

/** Headline numbers under the lollipop: dropped conversations and the top issue's share. */
export function dropoffSummary(
  items: readonly DropoffItem[],
  total?: number | null,
): { dropped: number; top: DropoffItem | null; topShare: number | null; issues: number } {
  const rows = items.filter((i) => i.conversations > 0);
  const sum = rows.reduce((s, r) => s + r.conversations, 0);
  const dropped = typeof total === "number" && total > 0 ? total : sum;
  const top = rows.reduce<DropoffItem | null>((best, r) => (!best || r.conversations > best.conversations ? r : best), null);
  return { dropped, top, topShare: top && dropped > 0 ? top.conversations / dropped : null, issues: rows.length };
}

export function DropoffInsight({ items, total }: { items: readonly DropoffItem[]; total?: number | null }) {
  const { dropped, top, topShare, issues } = dropoffSummary(items, total);
  if (!top) return null;
  return (
    <dl className="mt-4 grid grid-cols-1 gap-2 border-t border-line pt-3 text-xs sm:grid-cols-3" data-testid="dropoff-insight">
      <div className="min-w-0">
        <dt className="text-fg-subtle">Dropped conversations</dt>
        <dd className="font-semibold tabular-nums text-fg">{dropped.toLocaleString()}</dd>
      </div>
      <div className="min-w-0">
        <dt className="text-fg-subtle">Distinct issues</dt>
        <dd className="font-semibold tabular-nums text-fg">{issues.toLocaleString()}</dd>
      </div>
      <div className="min-w-0">
        <dt className="text-fg-subtle">Top issue</dt>
        <dd className="truncate font-semibold text-fg" title={top.label}>
          {top.label}
          {topShare !== null ? <span className="font-normal tabular-nums text-fg-muted"> · {Math.round(topShare * 100)}%</span> : null}
        </dd>
      </div>
    </dl>
  );
}
