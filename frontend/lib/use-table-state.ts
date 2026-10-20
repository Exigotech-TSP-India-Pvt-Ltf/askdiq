"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { writeUrl } from "@/lib/url-write";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useAudience } from "./audience-context";

export const ALLOWED_PAGE_SIZES = [10, 25, 50, 100] as const;
export type PageSize = (typeof ALLOWED_PAGE_SIZES)[number];

interface UseTableStateOptions<TFilter extends Record<string, any> = Record<string, any>> {
  tableKey?: string;
  defaultPageSize?: PageSize;
  defaultSort?: string;
  allowedSorts?: string[];
  defaultFilters?: TFilter;
  syncWithUrl?: boolean;
}

export function useTableState<TFilter extends Record<string, any> = Record<string, any>>({
  tableKey = "tbl",
  defaultPageSize = 10,
  defaultSort = "",
  allowedSorts = [],
  defaultFilters = {} as TFilter,
  syncWithUrl = true,
}: UseTableStateOptions<TFilter> = {}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { audience } = useAudience();

  // Helper to read URL query params with tableKey prefix
  const paramKey = useCallback((key: string) => `${tableKey}_${key}`, [tableKey]);

  const initialPageSize = useMemo(() => {
    if (!syncWithUrl) return defaultPageSize;
    const raw = Number(searchParams.get(paramKey("limit")));
    return (ALLOWED_PAGE_SIZES as readonly number[]).includes(raw)
      ? (raw as PageSize)
      : defaultPageSize;
  }, [searchParams, syncWithUrl, defaultPageSize, paramKey]);

  const initialPage = useMemo(() => {
    if (!syncWithUrl) return 1;
    const raw = Number(searchParams.get(paramKey("page")));
    return Number.isFinite(raw) && raw >= 1 ? raw : 1;
  }, [searchParams, syncWithUrl, paramKey]);

  const initialSort = useMemo(() => {
    if (!syncWithUrl) return defaultSort;
    const raw = searchParams.get(paramKey("sort"));
    if (raw && (allowedSorts.length === 0 || allowedSorts.includes(raw))) {
      return raw;
    }
    return defaultSort;
  }, [searchParams, syncWithUrl, defaultSort, allowedSorts, paramKey]);

  const initialSearch = useMemo(() => {
    if (!syncWithUrl) return "";
    return searchParams.get(paramKey("q")) || "";
  }, [searchParams, syncWithUrl, paramKey]);

  const [page, setPageState] = useState<number>(initialPage);
  const [pageSize, setPageSizeState] = useState<PageSize>(initialPageSize);
  const [sort, setSortState] = useState<string>(initialSort);
  const [searchInput, setSearchInput] = useState<string>(initialSearch);
  const [debouncedSearch, setDebouncedSearch] = useState<string>(initialSearch);
  const [filters, setFiltersState] = useState<TFilter>(defaultFilters);

  // Debounce search input by 300ms
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(searchInput.trim());
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  // Reset page to 1 whenever search, filters, or audience changes
  const prevAudienceRef = useRef(audience);
  const prevSearchRef = useRef(debouncedSearch);
  const prevFiltersRef = useRef(filters);

  useEffect(() => {
    const audienceChanged = prevAudienceRef.current !== audience;
    const searchChanged = prevSearchRef.current !== debouncedSearch;
    const filtersChanged = JSON.stringify(prevFiltersRef.current) !== JSON.stringify(filters);

    if (audienceChanged || searchChanged || filtersChanged) {
      setPageState(1);
      prevAudienceRef.current = audience;
      prevSearchRef.current = debouncedSearch;
      prevFiltersRef.current = filters;
    }
  }, [audience, debouncedSearch, filters]);

  // Sync state back to URL if enabled
  useEffect(() => {
    if (!syncWithUrl) return;

    const current = new URLSearchParams(Array.from(searchParams.entries()));
    if (page > 1) current.set(paramKey("page"), String(page));
    else current.delete(paramKey("page"));

    if (pageSize !== 10) current.set(paramKey("limit"), String(pageSize));
    else current.delete(paramKey("limit"));

    if (sort && sort !== defaultSort) current.set(paramKey("sort"), sort);
    else current.delete(paramKey("sort"));

    if (debouncedSearch) current.set(paramKey("q"), debouncedSearch);
    else current.delete(paramKey("q"));

    const queryString = current.toString();
    if (queryString === searchParams.toString()) return;
    // Page, size, sort and search are shareable and survive back / forward.
    writeUrl(router, `${pathname}${queryString ? `?${queryString}` : ""}`);
  }, [page, pageSize, sort, debouncedSearch, syncWithUrl, defaultSort, paramKey, pathname, router, searchParams]);

  // Setters with validation
  const setPage = useCallback((p: number | ((prev: number) => number)) => {
    setPageState((prev) => {
      const next = typeof p === "function" ? p(prev) : p;
      return Math.max(1, next);
    });
  }, []);

  const setPageSize = useCallback((size: PageSize) => {
    if ((ALLOWED_PAGE_SIZES as readonly number[]).includes(size)) {
      setPageSizeState(size);
      setPageState(1);
    }
  }, []);

  const setSort = useCallback(
    (newSort: string) => {
      if (allowedSorts.length === 0 || allowedSorts.includes(newSort)) {
        setSortState(newSort);
        setPageState(1);
      }
    },
    [allowedSorts]
  );

  const setFilter = useCallback((key: keyof TFilter, value: any) => {
    setFiltersState((prev) => ({
      ...prev,
      [key]: value,
    }));
    setPageState(1);
  }, []);

  const clearFilters = useCallback(() => {
    setFiltersState(defaultFilters);
    setSearchInput("");
    setDebouncedSearch("");
    setPageState(1);
  }, [defaultFilters]);

  // Abort controller manager for fetch lifecycles
  const abortControllerRef = useRef<AbortController | null>(null);
  const getSignal = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    abortControllerRef.current = new AbortController();
    return abortControllerRef.current.signal;
  }, []);

  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
    };
  }, []);

  const offset = (page - 1) * pageSize;

  return {
    page,
    pageSize,
    offset,
    sort,
    search: debouncedSearch,
    searchInput,
    filters,
    audience,
    setPage,
    setPageSize,
    setSort,
    setSearchInput,
    setFilter,
    clearFilters,
    getSignal,
  };
}
