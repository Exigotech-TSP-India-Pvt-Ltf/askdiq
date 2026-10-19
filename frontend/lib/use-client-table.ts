"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { PageSize } from "./use-table-state";

export interface ClientTableOptions<T> {
  /** Text a row is searched by (lower-cased by the hook). */
  searchText: (row: T) => string;
  /** Extra predicate (e.g. an outcome filter); return true to keep the row. */
  filter?: (row: T) => boolean;
  defaultPageSize?: PageSize;
}

/**
 * Search + filter + pagination for a list that is already in memory (detail
 * pages: a visitor's sessions and queries, a conversation's turns). Mirrors
 * the server-paged `useTableState` controls so these tables use the same
 * `TableToolbar` and `Pagination`.
 */
export function useClientTable<T>(rows: readonly T[], { searchText, filter, defaultPageSize = 10 }: ClientTableOptions<T>) {
  const [search, setSearchState] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSizeState] = useState<PageSize>(defaultPageSize);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return rows.filter((row) => (!filter || filter(row)) && (!needle || searchText(row).toLowerCase().includes(needle)));
  }, [rows, search, filter, searchText]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  // Keep the page in range when the list shrinks (new filter, new range).
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  const paged = useMemo(() => {
    const start = (Math.min(page, pageCount) - 1) * pageSize;
    return filtered.slice(start, start + pageSize);
  }, [filtered, page, pageCount, pageSize]);

  const setSearch = useCallback((value: string) => {
    setSearchState(value);
    setPage(1);
  }, []);
  const setPageSize = useCallback((size: PageSize) => {
    setPageSizeState(size);
    setPage(1);
  }, []);

  return {
    search,
    setSearch,
    page: Math.min(page, pageCount),
    setPage,
    pageSize,
    setPageSize,
    pageCount,
    filtered,
    paged,
    total: filtered.length,
  };
}
