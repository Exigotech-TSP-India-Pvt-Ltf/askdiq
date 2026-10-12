"use client";

import { useEffect, useRef, useState } from "react";
import { Filter, Search } from "lucide-react";

export type FilterOption = { label: string; value: string };

/** Search box (docked right) + a click-to-open filter dropdown, used above
 * every admin data table so search/filter controls look and behave the
 * same everywhere. */
export function TableToolbar({
  search,
  onSearchChange,
  searchPlaceholder,
  filterOptions,
  filterValue,
  onFilterChange,
  countLabel,
}: {
  search: string;
  onSearchChange: (value: string) => void;
  searchPlaceholder: string;
  filterOptions?: FilterOption[];
  filterValue?: string;
  onFilterChange?: (value: string) => void;
  countLabel?: string;
}) {
  const [filterOpen, setFilterOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setFilterOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const activeFilterLabel =
    filterOptions?.find((f) => f.value === filterValue)?.label ?? "All";

  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      {countLabel && <span className="text-xs text-slate-400">{countLabel}</span>}

      <div className="ml-auto flex items-center gap-2">
        <div className="relative w-full max-w-xs sm:w-64">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder={searchPlaceholder}
            className="w-full rounded-lg border border-purple-100 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 placeholder:text-slate-400 focus:border-purple-300 focus:outline-none focus:ring-1 focus:ring-purple-300"
          />
        </div>

        {filterOptions && filterOptions.length > 0 && (
          <div className="relative" ref={ref}>
            <button
              onClick={() => setFilterOpen((v) => !v)}
              className={`flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-medium transition ${
                filterOpen || (filterValue && filterValue !== "all")
                  ? "border-purple-600 bg-purple-600 text-white shadow-sm"
                  : "border-purple-100 bg-white text-slate-600 hover:bg-slate-50"
              }`}
            >
              <Filter className="h-3.5 w-3.5" />
              Filter: {activeFilterLabel}
            </button>

            {filterOpen && (
              <div className="absolute right-0 z-20 mt-1 w-48 rounded-lg border border-purple-100 bg-white p-1 shadow-lg">
                {filterOptions.map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => {
                      onFilterChange?.(opt.value);
                      setFilterOpen(false);
                    }}
                    className={`block w-full rounded-md px-3 py-1.5 text-left text-sm transition ${
                      filterValue === opt.value
                        ? "bg-purple-600 text-white"
                        : "text-slate-600 hover:bg-slate-50"
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
