"use client";

import { useMemo } from "react";
import { TreemapChart, type TreemapDatum } from "@/components/charts";
import type { TopicItem } from "@/lib/insights-api";
import { useInsightsPalette } from "./colors";

export interface TopicsTreemapProps {
  items: readonly TopicItem[];
  /** Highlighted (filtered) intent code. */
  selected?: string;
  onSelect?: (item: TopicItem) => void;
  height?: number;
}

interface TopicTile extends TreemapDatum {
  intent: string;
}

/** Topics under this share of questions get a tile too small to label; they are listed under the chart. */
export const SMALL_TOPIC_SHARE = 0.05;
const SMALL_ROW_SPACE = 40;
const MAX_SMALL_CHIPS = 5;

/** The smallest topics (by questions), largest first, so every topic has a visible name (VA-27). */
export function smallTopics(items: readonly TopicItem[]): TopicItem[] {
  const total = items.reduce((s, t) => s + Math.max(0, t.questions), 0);
  if (total <= 0) return [];
  return items
    .filter((t) => t.questions > 0 && t.questions / total < SMALL_TOPIC_SHARE)
    .sort((a, b) => b.questions - a.questions);
}

/**
 * Question topics: tile size = substantive questions, shade = handled % on
 * the red-to-teal scale (the HTML's `treemap()`). Recharts Treemap is
 * code-split through `TreemapChart` (`next/dynamic`).
 */
export function TopicsTreemap({ items, selected, onSelect, height = 240 }: TopicsTreemapProps) {
  const palette = useInsightsPalette();
  const tiles = useMemo<TopicTile[]>(
    () =>
      items
        .filter((t) => t.questions > 0)
        .sort((a, b) => b.questions - a.questions)
        .map((t) => {
          const color = palette.handled(t.handled_pct);
          return {
            intent: t.intent,
            name: t.label,
            value: t.questions,
            color,
            labelColor: palette.textOn(color),
            detail: `${t.questions} · ${t.handled_pct.toFixed(0)}% handled`,
            selected: selected === t.intent,
          };
        }),
    [items, palette, selected],
  );
  const byIntent = useMemo(() => new Map(items.map((t) => [t.intent, t])), [items]);
  const small = useMemo(() => smallTopics(items), [items]);

  return (
    <div className="flex h-full w-full flex-col">
      <div className="min-h-0 flex-1">
        <TreemapChart
          data={tiles}
          // Room for the smaller-topics row inside the same card body.
          height={small.length > 0 ? height - SMALL_ROW_SPACE : height}
          label="Treemap of question topics by volume, shaded by handled rate."
          onSelect={
            onSelect
              ? (tile) => {
                  const topic = byIntent.get((tile as TopicTile).intent);
                  if (topic) onSelect(topic);
                }
              : undefined
          }
        />
      </div>
      {small.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-fg-muted" data-testid="treemap-small-topics">
          <span className="font-medium">Smaller topics:</span>
          {small.slice(0, MAX_SMALL_CHIPS).map((t) =>
            onSelect ? (
              <button
                key={t.intent}
                type="button"
                onClick={() => onSelect(t)}
                aria-pressed={selected === t.intent}
                className="rounded-full border border-line px-2 py-0.5 hover:border-brand-300 hover:text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                {t.label} · {t.questions}
              </button>
            ) : (
              <span key={t.intent} className="rounded-full border border-line px-2 py-0.5">
                {t.label} · {t.questions}
              </span>
            ),
          )}
          {small.length > MAX_SMALL_CHIPS ? <span>+{small.length - MAX_SMALL_CHIPS} more in the table view</span> : null}
        </div>
      )}
      <div className="mt-2 flex items-center gap-2 text-[11px] text-fg-muted" aria-hidden="true">
        <span>0% handled</span>
        <span
          className="h-2 w-24 rounded-full"
          style={{ backgroundImage: `linear-gradient(to right, ${palette.handled(0)}, ${palette.handled(50)}, ${palette.handled(100)})` }}
        />
        <span>100% handled</span>
      </div>
    </div>
  );
}
