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

  return (
    <div className="flex h-full w-full flex-col">
      <div className="min-h-0 flex-1">
        <TreemapChart
          data={tiles}
          height={height}
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
