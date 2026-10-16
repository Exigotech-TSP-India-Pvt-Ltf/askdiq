"use client";

import React from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, AlertCircle, Inbox } from "lucide-react";
import { m } from "motion/react";
import { fadeUp, useMotionVariants } from "@/lib/motion";
import type { TableDensity } from "@/lib/use-table-preferences";

const CELL_PADDING: Record<TableDensity, string> = {
  comfortable: "px-4 py-3",
  compact: "px-3 py-1.5",
};

export interface ColumnDef<T> {
  key: string;
  header: string;
  icon?: React.ComponentType<{ className?: string }>;
  accessor?: (row: T) => any;
  sortable?: boolean;
  align?: "left" | "center" | "right";
  width?: string;
  render?: (row: T) => React.ReactNode;
}

export interface DataTableProps<T> {
  columns: ColumnDef<T>[];
  data: T[];
  keyField?: keyof T | ((row: T) => string);
  isLoading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  emptyMessage?: string;
  emptySubMessage?: string;
  currentSort?: string;
  onSortChange?: (sortKey: string) => void;
  onRowClick?: (row: T) => void;
  nested?: boolean;
  /** Row density from `useTablePreferences` / the TableToolbar toggle. */
  density?: TableDensity;
  /** Rows fade up as they mount (page changes, new data); off by default. */
  animateRows?: boolean;
  className?: string;
}

export function DataTable<T extends Record<string, any>>({
  columns,
  data,
  keyField = "id",
  isLoading = false,
  error = null,
  onRetry,
  emptyMessage = "No matching records found",
  emptySubMessage = "Try adjusting your search query, filters, or audience scope.",
  currentSort,
  onSortChange,
  onRowClick,
  nested = true,
  density = "comfortable",
  animateRows = false,
  className = "",
}: DataTableProps<T>) {
  const rowVariants = useMotionVariants(fadeUp);
  const Row = animateRows ? m.tr : "tr";
  const rowMotion = animateRows ? { variants: rowVariants, initial: "hidden", animate: "visible" } : {};
  const getRowKey = (row: T, idx: number): string => {
    if (typeof keyField === "function") return keyField(row);
    if (keyField in row && row[keyField] !== undefined) return String(row[keyField]);
    return String(idx);
  };

  const handleHeaderClick = (col: ColumnDef<T>) => {
    if (!col.sortable || !onSortChange) return;
    const baseKey = col.key;
    if (currentSort === `${baseKey}_desc`) {
      onSortChange(`${baseKey}_asc`);
    } else {
      onSortChange(`${baseKey}_desc`);
    }
  };

  const getSortIcon = (col: ColumnDef<T>) => {
    if (!col.sortable) return null;
    const baseKey = col.key;
    if (currentSort === `${baseKey}_desc`) {
      return <ArrowDown className="w-3.5 h-3.5 text-brand-600 dark:text-brand-400" />;
    }
    if (currentSort === `${baseKey}_asc`) {
      return <ArrowUp className="w-3.5 h-3.5 text-brand-600 dark:text-brand-400" />;
    }
    return <ArrowUpDown className="w-3 h-3 text-slate-300 dark:text-slate-600 group-hover:text-slate-500 dark:group-hover:text-slate-300 transition" />;
  };

  const getAriaSort = (col: ColumnDef<T>): "ascending" | "descending" | "none" => {
    if (!col.sortable || !currentSort) return "none";
    if (currentSort === `${col.key}_asc`) return "ascending";
    if (currentSort === `${col.key}_desc`) return "descending";
    return "none";
  };

  const containerClasses = nested
    ? `relative w-full bg-transparent ${className}`
    : `relative w-full overflow-hidden rounded-xl border border-slate-200/90 dark:border-white/10 bg-white dark:bg-dark-surface shadow-sm ${className}`;

  return (
    <div className={containerClasses}>
      {/* Subtle indeterminate progress bar when refreshing existing records */}
      {isLoading && data.length > 0 && (
        <div className="absolute top-0 left-0 right-0 z-20 h-0.5 w-full bg-slate-100 dark:bg-white/10 overflow-hidden">
          <div className="h-full bg-brand-gradient animate-pulse w-full" />
        </div>
      )}

      <div className="overflow-x-auto min-h-[280px]">
        <table className="w-full text-left text-xs border-collapse">
          {/* Header */}
          <thead className="bg-slate-50/80 dark:bg-dark-surface/90 border-b border-slate-200/90 dark:border-white/10 text-slate-500 dark:text-slate-400 font-medium">
            <tr>
              {columns.map((col) => {
                const Icon = col.icon;
                const alignClass =
                  col.align === "right"
                    ? "text-right"
                    : col.align === "center"
                    ? "text-center"
                    : "text-left";

                return (
                  <th
                    key={col.key}
                    scope="col"
                    aria-sort={getAriaSort(col)}
                    style={col.width ? { width: col.width } : undefined}
                    onClick={() => handleHeaderClick(col)}
                    className={`${CELL_PADDING[density]} font-semibold select-none ${alignClass} ${
                      col.sortable ? "cursor-pointer group hover:bg-slate-100/70 dark:hover:bg-white/5 transition" : ""
                    }`}
                  >
                    <div
                      className={`inline-flex items-center gap-1.5 ${
                        col.align === "right"
                          ? "justify-end"
                          : col.align === "center"
                          ? "justify-center"
                          : "justify-start"
                      }`}
                    >
                      {Icon && <Icon className="w-3.5 h-3.5 text-slate-400 dark:text-slate-500 group-hover:text-slate-600 dark:group-hover:text-slate-300" />}
                      <span className="group-hover:text-slate-800 dark:group-hover:text-slate-200 transition">{col.header}</span>
                      {getSortIcon(col)}
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>

          {/* Body */}
          <tbody
            className={`divide-y divide-slate-100 dark:divide-white/5 text-slate-700 dark:text-slate-300 transition-opacity duration-200 ${
              isLoading && data.length > 0 ? "opacity-60 pointer-events-none" : ""
            }`}
          >
            {isLoading && data.length === 0 ? (
              // Loading skeleton only on initial cold mount when no rows exist
              Array.from({ length: 6 }).map((_, i) => (
                <tr key={`skeleton-${i}`} className="animate-pulse">
                  {columns.map((col) => (
                    <td key={`skeleton-cell-${col.key}`} className={CELL_PADDING[density]}>
                      <div className="h-3.5 bg-slate-200/60 dark:bg-white/10 rounded-md w-3/4" />
                    </td>
                  ))}
                </tr>
              ))
            ) : error ? (
              // Error state
              <tr>
                <td colSpan={columns.length} className="px-6 py-12 text-center">
                  <div className="flex flex-col items-center justify-center gap-2 max-w-sm mx-auto">
                    <AlertCircle className="w-7 h-7 text-red-500" />
                    <p className="text-xs font-semibold text-slate-800 dark:text-slate-100">Failed to load table records</p>
                    <p className="text-[11px] text-slate-500 dark:text-slate-400 text-center">{error}</p>
                    {onRetry && (
                      <button
                        onClick={onRetry}
                        className="mt-2 px-3 py-1 bg-slate-100 hover:bg-slate-200 dark:bg-white/10 dark:hover:bg-white/15 text-slate-700 dark:text-slate-200 text-xs font-medium rounded-lg transition"
                      >
                        Try Again
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ) : data.length === 0 ? (
              // Empty state
              <tr>
                <td colSpan={columns.length} className="px-6 py-12 text-center">
                  <div className="flex flex-col items-center justify-center gap-1.5 max-w-sm mx-auto">
                    <Inbox className="w-8 h-8 text-slate-300 dark:text-slate-600 mb-1" />
                    <p className="text-xs font-semibold text-slate-800 dark:text-slate-100">{emptyMessage}</p>
                    <p className="text-[11px] text-slate-500 dark:text-slate-400 text-center">{emptySubMessage}</p>
                  </div>
                </td>
              </tr>
            ) : (
              // Data rows
              data.map((row, idx) => {
                const rowKey = getRowKey(row, idx);
                const isClickable = Boolean(onRowClick);

                return (
                  <Row
                    {...rowMotion}
                    key={rowKey}
                    onClick={() => isClickable && onRowClick?.(row)}
                    className={`transition-colors duration-150 ${
                      isClickable
                        ? "cursor-pointer hover:bg-brand-50/40 dark:hover:bg-brand-950/40"
                        : "hover:bg-slate-50/70 dark:hover:bg-white/5"
                    }`}
                  >
                    {columns.map((col) => {
                      const alignClass =
                        col.align === "right"
                          ? "text-right"
                          : col.align === "center"
                          ? "text-center"
                          : "text-left";

                      let content: React.ReactNode;
                      if (col.render) {
                        content = col.render(row);
                      } else if (col.accessor) {
                        content = col.accessor(row);
                      } else {
                        content = row[col.key];
                      }

                      return (
                        <td key={col.key} className={`${CELL_PADDING[density]} text-xs ${alignClass}`} data-density={density}>
                          {content}
                        </td>
                      );
                    })}
                  </Row>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
