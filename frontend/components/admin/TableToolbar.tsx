"use client";

import React, { useEffect, useRef, useState } from "react";
import { Filter, Search, X, RotateCcw } from "lucide-react";

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
  // Multiple filters support
  filters?: FilterGroup[];
  // Backwards compatibility for single filter
  filterOptions?: FilterOption[];
  filterValue?: string;
  onFilterChange?: (value: string) => void;
  filterLabel?: string;
  countLabel?: string;
  onClearAll?: () => void;
  extraActions?: React.ReactNode;
}

export function TableToolbar({
  search,
  onSearchChange,
  searchPlaceholder = "Search across columns...",
  filters = [],
  filterOptions,
  filterValue,
  onFilterChange,
  filterLabel,
  countLabel,
  onClearAll,
  extraActions,
}: TableToolbarProps) {
  const [activeDropdown, setActiveDropdown] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close dropdown on click outside or Escape
  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setActiveDropdown(null);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setActiveDropdown(null);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  // Normalize all filters into one array (combining multi + legacy)
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

  // Active filters for pill display
  const activePills = allFilterGroups.flatMap((group) => {
    if (!group.value || group.value === "all" || group.value === "") return [];
    const opt = group.options.find((o) => o.value === group.value);
    return [
      {
        groupId: group.id,
        groupLabel: group.label,
        value: group.value,
        label: opt ? opt.label : group.value,
        onClear: () => group.onChange("all"),
      },
    ];
  });

  const hasActiveControls = search.length > 0 || activePills.length > 0;

  return (
    <div className="mb-4 flex flex-col gap-2.5" ref={containerRef}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        {countLabel && (
          <span className="text-xs font-medium text-slate-500">{countLabel}</span>
        )}
        <div role="status" aria-live="polite" className="sr-only">
          {countLabel || ""}
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {/* Search box with Clear button */}
          <div className="relative w-full max-w-xs sm:w-64">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => onSearchChange(e.target.value)}
              placeholder={searchPlaceholder}
              className="w-full rounded-lg border border-slate-200 bg-white py-1.5 pl-8 pr-7 text-xs text-slate-700 placeholder:text-slate-400 shadow-sm transition focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
            {search && (
              <button
                onClick={() => onSearchChange("")}
                aria-label="Clear search"
                title="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 focus:outline-none"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          {/* Filter dropdowns */}
          {allFilterGroups.map((group) => {
            const isOpen = activeDropdown === group.id;
            const isFiltering = group.value && group.value !== "all" && group.value !== "";
            const currentLabel =
              group.options.find((o) => o.value === group.value)?.label || group.label;

            return (
              <div key={group.id} className="relative">
                <button
                  type="button"
                  aria-haspopup="true"
                  aria-expanded={isOpen}
                  onClick={() => setActiveDropdown(isOpen ? null : group.id)}
                  className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium shadow-sm transition ${
                    isFiltering
                      ? "border-brand-500 bg-brand-50 text-brand-700"
                      : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  <Filter className="h-3 w-3" />
                  <span>
                    {group.label}: {currentLabel}
                  </span>
                </button>

                {isOpen && (
                  <div className="absolute right-0 z-30 mt-1 min-w-[160px] rounded-lg border border-slate-200 bg-white p-1 shadow-lg">
                    {group.options.map((opt) => (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => {
                          group.onChange(opt.value);
                          setActiveDropdown(null);
                        }}
                        className={`block w-full rounded-md px-2.5 py-1.5 text-left text-xs transition ${
                          group.value === opt.value
                            ? "bg-brand-500 text-white font-medium"
                            : "text-slate-600 hover:bg-slate-100"
                        }`}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}

          {/* Optional Extra Action Buttons (e.g. Export CSV) */}
          {extraActions}

          {/* Global reset button */}
          {hasActiveControls && (
            <button
              type="button"
              onClick={() => {
                onSearchChange("");
                allFilterGroups.forEach((g) => g.onChange("all"));
                onClearAll?.();
              }}
              title="Reset all filters and search"
              className="flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-xs text-slate-500 hover:bg-slate-100 hover:text-slate-700 transition"
            >
              <RotateCcw className="h-3 w-3" />
              <span>Reset</span>
            </button>
          )}
        </div>
      </div>

      {/* Active filter badge pills */}
      {activePills.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 pt-1">
          <span className="text-[11px] font-medium text-slate-400">Active filters:</span>
          {activePills.map((pill) => (
            <span
              key={`${pill.groupId}-${pill.value}`}
              className="inline-flex items-center gap-1 rounded-md bg-brand-50 border border-brand-200/80 px-2 py-0.5 text-[11px] font-medium text-brand-700"
            >
              <span>
                {pill.groupLabel}: {pill.label}
              </span>
              <button
                type="button"
                onClick={pill.onClear}
                className="hover:text-brand-900 focus:outline-none"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
