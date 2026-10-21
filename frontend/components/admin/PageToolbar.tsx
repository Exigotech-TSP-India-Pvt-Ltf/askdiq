import React from "react";
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
  className?: string;
}

/**
 * The tab row (OR-2): tabs on the left; every page action, the filter chips
 * and the date filter right-aligned on the same row, in that order, with the
 * date filter last in the corner. Below `lg` (or whenever the row is full)
 * the right cluster wraps under the tabs and stays right-aligned. No context
 * or state of its own: the date filter reads the existing range context.
 */
export function PageToolbar({
  tabs,
  actions,
  filters,
  dateRange = false,
  tabsTour,
  onTabsIntent,
  className = "",
}: PageToolbarProps) {
  const hasRight = Boolean(actions || filters || dateRange);
  if (!tabs && !hasRight) return null;
  return (
    <div data-page-toolbar className={`flex flex-wrap items-center gap-x-3 gap-y-2 ${className}`}>
      {tabs ? (
        <div
          data-tour={tabsTour}
          data-toolbar-tabs
          className="min-w-0 max-w-full"
          onPointerOver={onTabsIntent}
          onFocus={onTabsIntent}
        >
          {tabs}
        </div>
      ) : null}
      {hasRight ? (
        <div data-toolbar-actions className="ml-auto flex min-w-0 max-w-full flex-wrap items-center justify-end gap-2">
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
