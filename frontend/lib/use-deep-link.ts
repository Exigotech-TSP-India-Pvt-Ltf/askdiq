"use client";

import { useCallback, useMemo, useRef } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { writeUrl } from "@/lib/url-write";

export type DeepLinkUpdate = Record<string, string | number | null | undefined>;

export interface DeepLinkOptions {
  /** "replace" (default) keeps history clean; "push" adds a back-button step. */
  history?: "replace" | "push";
}

/** Applies `update` to `search` (null / undefined / "" removes a key). */
export function mergeSearch(search: string, update: DeepLinkUpdate): string {
  const params = new URLSearchParams(search);
  for (const [key, value] of Object.entries(update)) {
    if (value === null || value === undefined || value === "") params.delete(key);
    else params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/**
 * Reads and writes URL query params. State lives in the URL, so
 * back / forward and shared links restore the same view. Writes go through
 * `writeUrl` (History API: no server round trip, no scroll jump).
 */
export function useDeepLink() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const search = searchParams?.toString() ?? "";

  const get = useCallback((key: string): string | null => new URLSearchParams(search).get(key), [search]);

  // Several `set` calls in one tick (e.g. a toolbar "Reset all" clearing each
  // filter in turn) must compose: each merges onto the URL the previous call
  // wrote, not onto the render-time `search`, or only the last one sticks.
  // The pending URL is only reused until the next render, so back / forward
  // (which re-renders with an older URL) never resurrects an undone write.
  const pending = useRef<{ render: number; next: string } | null>(null);
  const renders = useRef(0);
  renders.current += 1;

  const set = useCallback(
    (update: DeepLinkUpdate, { history = "replace" }: DeepLinkOptions = {}) => {
      const base = pending.current?.render === renders.current ? pending.current.next : search;
      const merged = mergeSearch(base, update);
      pending.current = { render: renders.current, next: merged.replace(/^\?/, "") };
      const href = `${pathname ?? ""}${merged}`;
      writeUrl(router, href, history);
    },
    [pathname, router, search],
  );

  return useMemo(() => ({ get, set, search }), [get, set, search]);
}

/** One URL param as state: `const [turn, setTurn] = useDeepLinkParam("turn")`. */
export function useDeepLinkParam(
  key: string,
  options?: DeepLinkOptions,
): [string | null, (value: string | null) => void] {
  const { get, set } = useDeepLink();
  const setValue = useCallback((value: string | null) => set({ [key]: value }, options), [key, options, set]);
  return [get(key), setValue];
}
