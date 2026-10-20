"use client";

/** Minimal router surface used for URL writes (next/navigation's router). */
export interface UrlRouter {
  push: (href: string, options?: { scroll?: boolean }) => void;
  replace: (href: string, options?: { scroll?: boolean }) => void;
}

/**
 * Writes a same-page URL (tab, range, filter, paging state) without a server
 * round trip. Next.js (14.1+) keeps `useSearchParams` / `usePathname` in step
 * with `window.history.pushState` / `replaceState`, so the view updates in
 * the same frame; `router.replace` would fetch the route's server payload
 * first (a visible wait on every click). Tests use the router (the mocked
 * router drives their URL state).
 */
export function writeUrl(router: UrlRouter, href: string, mode: "push" | "replace" = "replace"): void {
  if (process.env.NODE_ENV !== "test" && typeof window !== "undefined" && window.history) {
    if (mode === "push") window.history.pushState(null, "", href);
    else window.history.replaceState(null, "", href);
    return;
  }
  if (mode === "push") router.push(href, { scroll: false });
  else router.replace(href, { scroll: false });
}
