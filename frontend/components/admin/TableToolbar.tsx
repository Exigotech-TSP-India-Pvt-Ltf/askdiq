"use client";

import React, { useEffect, useRef, useState } from "react";
import { Search, SlidersHorizontal, X, RotateCcw, Check, Columns3, Rows3, Rows4 } from "lucide-react";
import type { TableDensity } from "@/lib/use-table-preferences";
import { usePopover } from "@/lib/use-popover";
import { m } from "motion/react";
import { popover, useMotionVariants } from "@/lib/motion";
import { focusRing, popoverPanel, primaryButton } from "@/components/ui/styles";
import { FilterChipBar } from "@/components/admin/filters/FilterChipBar";
import { isActive, type FilterDescriptor } from "@/components/admin/filters/types";

export type FilterOption = { label: string; value: string };

export interface FilterGroup {
  id: string;
  label: string;
  options: FilterOption[];
  value?: string;
  onChange: (val: string) => void;
}

export interface TableToolbarProps {
  search: string;
  onSearchChange: (value: string) => void;
  searchPlaceholder?: string;
  debounceMs?: number;
  // Multiple filter groups support
  filters?: FilterGroup[];
  // Backwards compatibility for single filter
  filterOptions?: FilterOption[];
  filterValue?: string;
  onFilterChange?: (value: string) => void;
  filterLabel?: string;
  countLabel?: string;
  /** Heading shown on the left of the search row (instead of a separate card header). */
  title?: React.ReactNode;
  onClearAll?: () => void;
  extraActions?: React.ReactNode;
  /** Column-visibility menu (pair with `useTablePreferences`). */
  columnOptions?: ColumnOption[];
  hiddenColumns?: string[];
  onToggleColumn?: (key: string) => void;
  /** Density toggle (comfortable / compact). */
  density?: TableDensity;
  onDensityChange?: (density: TableDensity) => void;
  /** Export action slot (e.g. a CSV export button for tables with a server export). */
  exportAction?: React.ReactNode;
}

export type ColumnOption = { key: string; label: string; hideable?: boolean };

const toolbarButton = `flex items-center gap-1.5 rounded-lg border border-line bg-surface-card px-2.5 py-1.5 text-xs font-medium text-fg shadow-sm transition-colors hover:bg-surface-muted hover:text-fg ${focusRing}`;

function ColumnMenu({
  options,
  hidden,
  onToggle,
}: {
  options: ColumnOption[];
  hidden: string[];
  onToggle: (key: string) => void;
}) {
  const { open, toggle, triggerRef, panelRef } = usePopover();
  const variants = useMotionVariants(popover);
  const visibleCount = options.filter((o) => !hidden.includes(o.key)).length;
  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={toggle}
        aria-haspopup="true"
        aria-expanded={open}
        className={toolbarButton}
      >
        <Columns3 className="h-3.5 w-3.5 text-brand-600 dark:text-brand-400" strokeWidth={1.75} aria-hidden="true" />
        <span>Columns</span>
      </button>
      {open && (
        <m.div
          ref={panelRef}
          role="group"
          aria-label="Visible columns"
          variants={variants}
          initial="hidden"
          animate="visible"
          className={`absolute right-0 z-50 mt-2 w-56 origin-top-right ${popoverPanel}`}
        >
          {options.map((opt) => {
            const checked = !hidden.includes(opt.key);
            // Never hide the last visible column, nor a column marked not hideable.
            const locked = opt.hideable === false || (checked && visibleCount <= 1);
            return (
              <label
                key={opt.key}
                className={`flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-fg-muted hover:bg-surface-muted ${locked ? "opacity-60" : "cursor-pointer"}`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={locked}
                  onChange={() => onToggle(opt.key)}
                  className="h-3.5 w-3.5 rounded border-line accent-brand-600"
                />
                {opt.label}
              </label>
            );
          })}
        </m.div>
      )}
    </div>
  );
}

export function TableToolbar({
  search,
  onSearchChange,
  searchPlaceholder = "Search across columns...",
  debounceMs = 0,
  filters = [],
  filterOptions,
  filterValue,
  onFilterChange,
  filterLabel,
  countLabel,
  title,
  onClearAll,
  extraActions,
  columnOptions,
  hiddenColumns = [],
  onToggleColumn,
  density,
  onDensityChange,
  exportAction,
}: TableToolbarProps) {
  const [isOpen, setIsOpen] = useState(false);
  const popoverVariants = useMotionVariants(popover);
  const [internalSearch, setInternalSearch] = useState(search);
  const containerRef = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setInternalSearch(search);
  }, [search]);

  useEffect(() => {
    if (!debounceMs || debounceMs <= 0) return;
    const timer = setTimeout(() => {
      if (internalSearch !== search) {
        onSearchChange(internalSearch);
      }
    }, debounceMs);
    return () => clearTimeout(timer);
  }, [internalSearch, debounceMs, onSearchChange, search]);

  // Close popover on click outside or Escape
  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node) &&
        popoverRef.current &&
        !popoverRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  // Normalize all filter groups into one list
  const allFilterGroups: FilterGroup[] = [...filters];
  if (filterOptions && filterOptions.length > 0 && onFilterChange) {
    allFilterGroups.push({
      id: "legacy",
      label: filterLabel || "Filter",
      options: filterOptions,
      value: filterValue || "all",
      onChange: onFilterChange,
    });
  }

  // Every group as a shared chip descriptor ("all" / "" = no filter).
  const chipDescriptors: FilterDescriptor[] = allFilterGroups.map((group) => ({
    key: group.id,
    label: group.label,
    value: group.value ?? "",
    kind: "single",
    options: group.options,
    clearedValue: "all",
    resetTo: "all",
  }));
  const activeCount = chipDescriptors.filter(isActive).length;
  const hasActiveControls = search.length > 0 || activeCount > 0;

  const handleResetAll = () => {
    onSearchChange("");
    allFilterGroups.forEach((g) => g.onChange("all"));
    onClearAll?.();
  };

  return (
    <div className="flex flex-col gap-2.5" ref={containerRef}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        {title ? (
          <div className="flex min-w-0 flex-col gap-0.5">
            <h2 className="truncate text-sm font-semibold text-fg dark:text-white">{title}</h2>
            {countLabel ? <span className="text-xs font-medium text-fg-subtle">{countLabel}</span> : null}
          </div>
        ) : null}
        <div role="status" aria-live="polite" className="sr-only">
          {countLabel || ""}
        </div>

        {/* One row: count, search, Filters, Columns, Compact, actions (wraps only when narrow). */}
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          {countLabel && !title ? (
            <span data-testid="table-count" className="whitespace-nowrap text-xs font-medium text-fg-subtle">
              {countLabel}
            </span>
          ) : null}
          {/* Integrated Search Input with Clear Button */}
          <div className="relative w-full max-w-xs sm:w-56 2xl:w-64">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-subtle" />
            <input
              type="text"
              value={debounceMs > 0 ? internalSearch : search}
              onChange={(e) => {
                const val = e.target.value;
                if (debounceMs > 0) {
                  setInternalSearch(val);
                } else {
                  onSearchChange(val);
                }
              }}
              placeholder={searchPlaceholder}
              className="w-full rounded-lg border border-line bg-surface-card py-1.5 pl-8 pr-7 text-xs text-fg placeholder:text-fg-subtle/80 shadow-sm transition focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
            {(debounceMs > 0 ? internalSearch : search) ? (
              <button
                type="button"
                onClick={() => {
                  if (debounceMs > 0) {
                    setInternalSearch("");
                  }
                  onSearchChange("");
                }}
                aria-label="Clear search"
                title="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-fg-subtle hover:text-fg focus:outline-none"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </div>

          {/* Single "Filters" Button with Active Badge */}
          {allFilterGroups.length > 0 && (
            <div className="relative">
              <button
                type="button"
                aria-haspopup="dialog"
                aria-expanded={isOpen}
                onClick={() => setIsOpen(!isOpen)}
                className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium shadow-sm transition-all duration-150 ${
                  activeCount > 0
                    ? "border-brand-500 bg-brand-50/70 dark:bg-brand-950/60 text-brand-700 dark:text-brand-300 font-semibold shadow-brand-glow/20"
                    : "border-line bg-surface-card text-fg hover:bg-surface-muted hover:text-fg"
                }`}
              >
                <SlidersHorizontal className="h-3.5 w-3.5 text-brand-600 dark:text-brand-400" />
                <span>Filters</span>
                {activeCount > 0 && (
                  <span className="rounded-full bg-brand-600 dark:bg-brand-500 px-1.5 py-0.2 text-[10px] font-semibold text-white leading-none">
                    {activeCount}
                  </span>
                )}
              </button>

              {/* Multi-Group Filter Popover Panel */}
              {isOpen && (
                <m.div
                  ref={popoverRef}
                  role="dialog"
                  aria-label="Filter Options"
                  variants={popoverVariants}
                  initial="hidden"
                  animate="visible"
                  className={`absolute right-0 z-50 mt-2 w-72 origin-top-right ${popoverPanel} p-3`}
                >
                  <div className="flex items-center justify-between border-b border-line/70 pb-2 mb-2">
                    <span className="text-xs font-semibold text-fg">Filter Options</span>
                    {activeCount > 0 && (
                      <button
                        type="button"
                        onClick={() => allFilterGroups.forEach((g) => g.onChange("all"))}
                        className="text-[11px] font-medium text-brand-600 dark:text-brand-400 hover:text-brand-700 hover:underline"
                      >
                        Reset all
                      </button>
                    )}
                  </div>

                  <div className="max-h-72 overflow-y-auto space-y-3 pr-1">
                    {allFilterGroups.map((group) => (
                      <div key={group.id} className="space-y-1.5">
                        <label className="block text-[11px] font-bold uppercase tracking-wider text-fg-subtle">
                          {group.label}
                        </label>
                        <div className="space-y-0.5">
                          {group.options.map((opt) => {
                            const isSelected =
                              group.value === opt.value || (!group.value && opt.value === "all");
                            return (
                              <button
                                key={opt.value}
                                type="button"
                                onClick={() => group.onChange(opt.value)}
                                className={`flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-xs transition ${
                                  isSelected
                                    ? "bg-brand-50 dark:bg-brand-950/60 text-brand-700 dark:text-brand-300 font-semibold"
                                    : "text-fg-muted hover:bg-surface-muted hover:text-fg"
                                }`}
                              >
                                <span>{opt.label}</span>
                                {isSelected && <Check className="h-3.5 w-3.5 text-brand-600 dark:text-brand-400 shrink-0" />}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="mt-3 flex items-center justify-end gap-2 border-t border-line/70 pt-2.5">
                    <button
                      type="button"
                      onClick={() => setIsOpen(false)}
                      className={primaryButton}
                    >
                      Done
                    </button>
                  </div>
                </m.div>
              )}
            </div>
          )}

          {/* Column visibility and density (remembered per table) */}
          {columnOptions && columnOptions.length > 0 && onToggleColumn ? (
            <ColumnMenu options={columnOptions} hidden={hiddenColumns} onToggle={onToggleColumn} />
          ) : null}
          {density && onDensityChange ? (
            <button
              type="button"
              aria-pressed={density === "compact"}
              onClick={() => onDensityChange(density === "compact" ? "comfortable" : "compact")}
              title={density === "compact" ? "Compact rows on: click for comfortable rows" : "Compact rows"}
              data-active={density === "compact" ? "true" : undefined}
              className={
                density === "compact"
                  ? `flex items-center gap-1.5 rounded-lg border border-brand-500 bg-brand-50/70 px-2.5 py-1.5 text-xs font-semibold text-brand-700 shadow-sm transition-colors dark:bg-brand-950/60 dark:text-brand-300 ${focusRing}`
                  : toolbarButton
              }
            >
              {density === "compact" ? (
                <Rows4 className="h-3.5 w-3.5 text-brand-600 dark:text-brand-400" strokeWidth={1.75} aria-hidden="true" />
              ) : (
                <Rows3 className="h-3.5 w-3.5 text-brand-600 dark:text-brand-400" strokeWidth={1.75} aria-hidden="true" />
              )}
              <span>Compact</span>
            </button>
          ) : null}

          {/* Export action slot */}
          {exportAction}

          {/* Optional Extra Action Buttons (e.g. Export CSV) */}
          {extraActions}

          {/* Global Reset Button */}
          {hasActiveControls && (
            <button
              type="button"
              onClick={handleResetAll}
              title="Reset all filters and search"
              className="flex items-center gap-1 rounded-lg border border-line bg-surface-muted px-2.5 py-1.5 text-xs text-fg-subtle hover:bg-surface-muted hover:text-fg transition"
            >
              <RotateCcw className="h-3 w-3" />
              <span>Reset</span>
            </button>
          )}
        </div>
      </div>

      {/* Active filters as the shared editable chips (OR-13): a chip opens its
          options in place; × resets that group. */}
      {activeCount > 0 ? (
        <FilterChipBar
          ariaLabel="Active table filters"
          className="justify-start pt-1"
          showFiltersButton={false}
          filters={chipDescriptors}
          onChange={(key, value) => allFilterGroups.find((g) => g.id === key)?.onChange(value)}
          onClearAll={activeCount > 1 ? () => allFilterGroups.forEach((g) => g.onChange("all")) : undefined}
        />
      ) : null}
    </div>
  );
}
