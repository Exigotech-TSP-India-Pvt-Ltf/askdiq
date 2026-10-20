"use client";

import React from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, AlertCircle, Inbox } from "lucide-react";
import { m } from "motion/react";
import { fadeUp, useMotionVariants } from "@/lib/motion";
import { useRowIntentHandlers } from "@/lib/query-cache";
import type { TableDensity } from "@/lib/use-table-preferences";
import { secondaryButton, studioSurface, tableBody, tableHead, tableRow } from "@/components/ui/styles";

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
  /** Hover / focus (120 ms dwell) on a row: warm what a click on it opens. */
  onRowIntent?: (row: T) => void;
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
  onRowIntent,
  nested = true,
  density = "comfortable",
  animateRows = false,
  className = "",
}: DataTableProps<T>) {
  const rowVariants = useMotionVariants(fadeUp);
  // Hover / focus a row for 120 ms: warm what a click on it opens.
  const intentHandlers = useRowIntentHandlers(onRowIntent);
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
      return <ArrowDown className="w-3.5 h-3.5 text-accent-fg" />;
    }
    if (currentSort === `${baseKey}_asc`) {
      return <ArrowUp className="w-3.5 h-3.5 text-accent-fg" />;
    }
    return <ArrowUpDown className="w-3 h-3 text-fg-subtle/50 group-hover:text-fg-muted transition" />;
  };

  const getAriaSort = (col: ColumnDef<T>): "ascending" | "descending" | "none" => {
    if (!col.sortable || !currentSort) return "none";
    if (currentSort === `${col.key}_asc`) return "ascending";
    if (currentSort === `${col.key}_desc`) return "descending";
    return "none";
  };

  const containerClasses = nested
    ? `relative w-full bg-transparent ${className}`
    : `${studioSurface} w-full ${className}`;

  return (
    <div className={containerClasses}>
      {/* Subtle indeterminate progress bar when refreshing existing records */}
      {isLoading && data.length > 0 && (
        <div className="absolute top-0 left-0 right-0 z-20 h-0.5 w-full bg-surface-muted overflow-hidden">
          <div className="h-full bg-brand-gradient animate-pulse w-full" />
        </div>
      )}

      <div className="overflow-x-auto min-h-[280px]">
        <table className="w-full text-left text-xs border-collapse">
          {/* Header */}
          <thead className={`sticky top-0 z-10 font-medium ${tableHead}`}>
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
                      col.sortable ? "cursor-pointer group hover:bg-brand-50/70 hover:text-fg dark:hover:bg-white/5 transition" : ""
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
                      {Icon && <Icon className="w-3.5 h-3.5 text-fg-subtle group-hover:text-fg-muted" />}
                      <span className="group-hover:text-fg transition">{col.header}</span>
                      {getSortIcon(col)}
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>

          {/* Body */}
          <tbody
            className={`${tableBody} transition-opacity duration-200 ${
              isLoading && data.length > 0 ? "opacity-60 pointer-events-none" : ""
            }`}
          >
            {isLoading && data.length === 0 ? (
              // Loading skeleton only on initial cold mount when no rows exist
              Array.from({ length: 6 }).map((_, i) => (
                <tr key={`skeleton-${i}`} className="motion-safe:animate-pulse">
                  {columns.map((col) => (
                    <td key={`skeleton-cell-${col.key}`} className={CELL_PADDING[density]}>
                      <div className="h-3.5 w-3/4 rounded-md bg-surface-muted dark:bg-brand-500/[0.08]" />
                    </td>
                  ))}
                </tr>
              ))
            ) : error ? (
              // Error state
              <tr>
                <td colSpan={columns.length} className="px-6 py-12 text-center">
                  <div className="flex flex-col items-center justify-center gap-2 max-w-sm mx-auto">
                    <span className="flex h-10 w-10 items-center justify-center rounded-full border border-status-danger/25 bg-status-danger-bg text-status-danger">
                      <AlertCircle className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
                    </span>
                    <p className="text-xs font-semibold text-fg">Failed to load table records</p>
                    <p className="text-[11px] text-fg-muted text-center">{error}</p>
                    {onRetry && (
                      <button
                        type="button"
                        onClick={onRetry}
                        className={`${secondaryButton} mt-2`}
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
                    <span className="mb-1 flex h-10 w-10 items-center justify-center rounded-full border border-line-brand/60 bg-surface-studio text-accent-fg dark:border-brand-500/25">
                      <Inbox className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
                    </span>
                    <p className="text-xs font-semibold text-fg">{emptyMessage}</p>
                    <p className="text-[11px] text-fg-muted text-center">{emptySubMessage}</p>
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
                    // Keyboard: a clickable row is focusable (focus warms it) and Enter opens it.
                    tabIndex={isClickable ? 0 : undefined}
                    onKeyDown={
                      isClickable
                        ? (e: React.KeyboardEvent) => {
                            if (e.key === "Enter" && e.target === e.currentTarget) onRowClick?.(row);
                          }
                        : undefined
                    }
                    {...intentHandlers(row)}
                    className={`${tableRow} ${isClickable ? "cursor-pointer" : ""}`}
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
