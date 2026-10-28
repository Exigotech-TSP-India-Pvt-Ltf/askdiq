"use client";

import React, { useEffect, useRef, useState } from "react";
import { DateRangePicker } from "@/components/ui/DateRangePicker";

export interface PageToolbarProps {
  /** Left side: the page's `TabGroup` (empty on pages without tabs). */
  tabs?: React.ReactNode;
  /** Contextual page actions (icon + text), right-aligned before the filters. */
  actions?: React.ReactNode;
  /** Filter chips (`FilterChipBar`), between the actions and the date filter. */
  filters?: React.ReactNode;
  /** Shows the global date-range filter in the far corner (reads `RangeProvider`). */
  dateRange?: boolean;
  /** Anchor for the page tour on the tabs wrapper. */
  tabsTour?: string;
  /** Pointer / focus intent on the tabs (route prefetch). */
  onTabsIntent?: (event: React.PointerEvent | React.FocusEvent) => void;
  /** Keep tabs and actions stacked until the viewport has room for both. */
  singleRowAtWide?: boolean;
  className?: string;
}

/** Fade width for a tab row that scrolls sideways. */
const FADE = "1.5rem";

/**
 * Which edges of a horizontally scrolling element have more content past
 * them (for the fade masks). Re-measured on scroll and resize.
 */
function useScrollEdges(ref: React.RefObject<HTMLElement | null>): { start: boolean; end: boolean } {
  const [edges, setEdges] = useState({ start: false, end: false });
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const measure = () => {
      const max = el.scrollWidth - el.clientWidth;
      const next = { start: el.scrollLeft > 1, end: max - el.scrollLeft > 1 };
      setEdges((prev) => (prev.start === next.start && prev.end === next.end ? prev : next));
    };
    measure();
    el.addEventListener("scroll", measure, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(el);
    if (el.firstElementChild) observer?.observe(el.firstElementChild);
    return () => {
      el.removeEventListener("scroll", measure);
      observer?.disconnect();
    };
  }, [ref]);
  return edges;
}

function fadeMask({ start, end }: { start: boolean; end: boolean }): React.CSSProperties | undefined {
  if (!start && !end) return undefined;
  const mask = `linear-gradient(to right, ${start ? "transparent" : "black"}, black ${start ? FADE : "0"}, black calc(100% - ${
    end ? FADE : "0px"
  }), ${end ? "transparent" : "black"})`;
  return { maskImage: mask, WebkitMaskImage: mask };
}

/**
 * The tab row (OR-2): tabs on the left; every page action, the filter chips
 * and the date filter right-aligned on the same row, in that order, with the
 * date filter last in the corner. From `lg` up the row never wraps: the tabs
 * give way (one line, scrolling sideways with fade edges) so the right
 * cluster stays on the tab row. Below `lg` the right cluster wraps under the
 * tabs as one group, right-aligned. The date filter reads the range context.
 */
export function PageToolbar({
  tabs,
  actions,
  filters,
  dateRange = false,
  tabsTour,
  onTabsIntent,
  singleRowAtWide = false,
  className = "",
}: PageToolbarProps) {
  const tabsRef = useRef<HTMLDivElement>(null);
  const edges = useScrollEdges(tabsRef);
  const hasRight = Boolean(actions || filters || dateRange);
  const layout = singleRowAtWide
    ? {
        row: "min-[1440px]:flex-nowrap",
        tabs: "min-[1440px]:min-w-[12rem] min-[1440px]:shrink-[4] min-[1440px]:overflow-x-auto min-[1440px]:p-0.5 min-[1440px]:[scrollbar-width:none] min-[1440px]:[&::-webkit-scrollbar]:hidden min-[1440px]:[&_[role=tablist]]:flex-nowrap",
        actions: "min-[1440px]:max-w-[70%] min-[1440px]:shrink-0",
      }
    : {
        row: "lg:flex-nowrap",
        tabs: "lg:min-w-[12rem] lg:shrink-[4] lg:overflow-x-auto lg:p-0.5 lg:[scrollbar-width:none] lg:[&::-webkit-scrollbar]:hidden lg:[&_[role=tablist]]:flex-nowrap",
        actions: "lg:max-w-[70%] lg:shrink-0",
      };
  if (!tabs && !hasRight) return null;
  return (
    <div data-page-toolbar className={`flex flex-wrap items-center gap-x-3 gap-y-2 ${layout.row} ${className}`}>
      {tabs ? (
        <div
          ref={tabsRef}
          data-tour={tabsTour}
          data-toolbar-tabs
          data-scroll-start={edges.start || undefined}
          data-scroll-end={edges.end || undefined}
          // At the selected breakpoint, tabs shrink first and scroll; padding
          // keeps focus rings inside the scroll box.
          className={`min-w-0 max-w-full ${singleRowAtWide ? "w-full min-[1440px]:w-auto" : ""} ${layout.tabs}`}
          style={fadeMask(edges)}
          onPointerOver={onTabsIntent}
          onFocus={onTabsIntent}
        >
          {tabs}
        </div>
      ) : null}
      {hasRight ? (
        <div
          data-toolbar-actions
          // At the selected breakpoint, actions keep their size while tabs give
          // way; only a very long chip set wraps inside the capped cluster.
          className={`ml-auto flex min-w-0 max-w-full flex-wrap items-center justify-end gap-2 ${singleRowAtWide ? "w-full min-[1440px]:w-auto" : ""} ${layout.actions}`}
        >
          {actions}
          {filters}
          {dateRange ? (
            <div data-tour="shell-date-range" className="flex">
              <DateRangePicker />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
