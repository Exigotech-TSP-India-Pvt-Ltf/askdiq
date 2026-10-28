"use client";

import { useMemo, useState } from "react";
import type { TopicItem } from "@/lib/insights-api";
import { useInsightsPalette } from "./colors";

export interface TopicsBreakdownProps {
  items: readonly TopicItem[];
  /** Highlighted (filtered) intent code. */
  selected?: string;
  onSelect?: (item: TopicItem) => void;
}

/** Rows shown before "Show all N". */
export const TOPIC_PREVIEW_COUNT = 10;

/** Topics with questions, largest first (ties by name, so the order is stable). */
export function rankTopics(items: readonly TopicItem[]): TopicItem[] {
  return items.filter((t) => t.questions > 0).sort((a, b) => b.questions - a.questions || a.label.localeCompare(b.label));
}

export const topicMeta = (t: TopicItem): string => `${t.questions} · ${t.handled_pct.toFixed(0)}% handled`;

const ROW =
  "grid w-full grid-cols-[minmax(5.5rem,11rem)_minmax(2.5rem,1fr)_auto] items-center gap-x-3 rounded-lg px-2 py-1 text-left transition-colors duration-150 motion-reduce:transition-none";
const ROW_INTERACTIVE = "hover:bg-surface-muted focus:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-500";
const LINK =
  "rounded-md px-1.5 py-0.5 text-[11px] font-semibold text-brand-700 hover:underline focus:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-500 dark:text-brand-300";

/**
 * Question topics as a ranked list: full topic name (wraps, never cut), a bar
 * sized by volume and filled on the handled % scale (red 0 % to teal 100 %),
 * and "N · X% handled" as text. Top 10 first, "Show all N" expands in place.
 */
export function TopicsBreakdown({ items, selected, onSelect }: TopicsBreakdownProps) {
  const palette = useInsightsPalette();
  const ranked = useMemo(() => rankTopics(items), [items]);
  const selectedHidden = ranked.findIndex((t) => t.intent === selected) >= TOPIC_PREVIEW_COUNT;
  const [expanded, setExpanded] = useState(selectedHidden);
  const showAll = expanded || selectedHidden;
  const visible = showAll ? ranked : ranked.slice(0, TOPIC_PREVIEW_COUNT);
  const max = Math.max(1, ...ranked.map((t) => t.questions));
  const canExpand = ranked.length > TOPIC_PREVIEW_COUNT;

  return (
    <div className="flex h-full w-full flex-col">
      <ul
        className="flex flex-1 flex-col justify-between gap-0.5"
        aria-label="Question topics by volume, coloured by handled rate"
        data-testid="topics-breakdown"
      >
        {visible.map((t) => {
          const color = palette.handled(t.handled_pct);
          const isSelected = selected === t.intent;
          const dimmed = selected !== undefined && selected !== "" && !isSelected;
          const content = (
            <>
              <span className={`wrap-break-word text-xs leading-4 ${isSelected ? "font-semibold text-fg" : "font-medium text-fg"}`}>{t.label}</span>
              <span className="block h-2 w-full overflow-hidden rounded-full" style={{ backgroundColor: palette.ct.track }} aria-hidden="true">
                <span
                  className="block h-full rounded-full"
                  data-testid="topic-bar"
                  data-color={color}
                  style={{ width: `${Math.max(2, (100 * t.questions) / max)}%`, backgroundColor: color }}
                />
              </span>
              <span className="whitespace-nowrap text-right text-[11px] tabular-nums text-fg-muted">
                <b className="font-semibold text-fg">{t.questions}</b> · {t.handled_pct.toFixed(0)}% handled
              </span>
            </>
          );
          const label = `${t.label}: ${t.questions} questions, ${t.handled_pct.toFixed(0)}% handled`;
          return (
            <li key={t.intent} className={dimmed ? "opacity-60" : undefined} data-intent={t.intent}>
              {onSelect ? (
                <button
                  type="button"
                  aria-label={label}
                  aria-pressed={isSelected}
                  onClick={() => onSelect(t)}
                  className={`${ROW} ${ROW_INTERACTIVE} ${isSelected ? "bg-brand-50 ring-1 ring-brand-300 dark:bg-brand-500/10 dark:ring-brand-700/60" : ""}`}
                >
                  {content}
                </button>
              ) : (
                <div aria-label={label} role="group" className={ROW}>
                  {content}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-fg-muted">
        <div className="flex items-center gap-2" aria-hidden="true">
          <span>0% handled</span>
          <span
            className="h-2 w-24 rounded-full"
            style={{ backgroundImage: `linear-gradient(to right, ${palette.handled(0)}, ${palette.handled(50)}, ${palette.handled(100)})` }}
          />
          <span>100% handled</span>
        </div>
        {canExpand && !selectedHidden ? (
          <button type="button" className={LINK} aria-expanded={showAll} onClick={() => setExpanded((v) => !v)}>
            {showAll ? `Show top ${TOPIC_PREVIEW_COUNT}` : `Show all ${ranked.length}`}
          </button>
        ) : null}
      </div>
    </div>
  );
}
