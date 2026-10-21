"use client";

/**
 * Console live updates: one EventSource per tab on `GET /admin/live`. The
 * backend names what changed (`data`, `alerts` or a precomputed aggregate
 * kind); the matching query-cache prefixes are invalidated, so views on
 * screen refetch in place (their previous data stays visible) and cached
 * views elsewhere refetch when next shown. Nothing polls.
 *
 * While the tab is hidden the stream is closed; on return it reconnects and
 * refreshes everything once (whatever changed meanwhile).
 */
import { useEffect, useRef, useSyncExternalStore } from "react";
import { queryCache, type QueryCache } from "@/lib/query-cache";

export const LIVE_STREAM_URL = "/api/proxy/admin/live";
/** One invalidation per prefix at most this often (bursts of changes). */
export const LIVE_INVALIDATE_MIN_MS = 1_000;
/** Reconnect backoff bounds after an error. */
const RETRY_MIN_MS = 1_000;
const RETRY_MAX_MS = 30_000;

/** Query-key prefixes of every view backed by live data (scope `data`). */
export const LIVE_DATA_PREFIXES = [
  "admin/dashboard",
  "admin/governance",
  "admin/usage",
  "admin/feedback",
  "admin/insights",
  "metrics/evaluation",
  "visitors",
  "visitor",
  "conversations",
  "conversation",
  "documents",
  "um/",
  // The signed-in user's chat list (a chat from another tab or device).
  "chat/sessions",
] as const;

/** Scope (backend kind prefix) -> query-key prefixes it refreshes. */
const SCOPE_PREFIXES: ReadonlyArray<readonly [string, readonly string[]]> = [
  ["alerts", ["admin/alerts"]],
  // Sign-ins, access requests, decisions and user changes (app.auth.live);
  // `users.stats` once the precomputed User Management stats are stored.
  ["users", ["um/", "admin/access-requests"]],
  ["users.", ["um/stats"]],
  ["data", LIVE_DATA_PREFIXES],
  ["usage.audience_counts", ["admin/usage/audience-counts"]],
  ["usage.", ["admin/usage", "admin/dashboard"]],
  ["admin.dashboard", ["admin/dashboard"]],
  ["admin.conversations", ["conversations"]],
  ["conversations.", ["conversations", "conversation"]],
  ["visitors.", ["visitors", "visitor"]],
  ["documents.", ["documents"]],
  ["feedback.", ["admin/feedback"]],
  ["evaluation.", ["metrics/evaluation"]],
  ["governance.", ["admin/governance"]],
  ["insights.", ["admin/insights"]],
  // Azure Insights (app/azure_insights): every azure.* kind is re-read from
  // its snapshot after each background poll.
  ["azure.", ["admin/azure"]],
];

/**
 * Scopes that refresh only their own views (not their kind prefix's): a
 * full evaluation run's progress ticks every few seconds and must not
 * refetch the whole Evaluation page (its finish announces `evaluation.live`).
 */
const EXCLUSIVE_SCOPES: Readonly<Record<string, readonly string[]>> = {
  "evaluation.runs": ["metrics/evaluation/runs"],
};

/** Query-key prefixes to refresh for the scopes of one `changed` frame. */
export function prefixesForScopes(scopes: readonly string[]): string[] {
  const out = new Set<string>();
  for (const scope of scopes) {
    const exclusive = EXCLUSIVE_SCOPES[scope];
    if (exclusive) {
      exclusive.forEach((p) => out.add(p));
      continue;
    }
    for (const [match, prefixes] of SCOPE_PREFIXES) {
      if (scope === match || (match.endsWith(".") && scope.startsWith(match))) {
        prefixes.forEach((p) => out.add(p));
      }
    }
  }
  return Array.from(out);
}

/** Throttled invalidation: a prefix refreshed within the window waits for its end. */
export function createInvalidator(cache: QueryCache = queryCache, minMs = LIVE_INVALIDATE_MIN_MS) {
  const last = new Map<string, number>();
  const pending = new Map<string, ReturnType<typeof setTimeout>>();
  const run = (prefix: string) => {
    last.set(prefix, Date.now());
    pending.delete(prefix);
    cache.invalidate(prefix);
  };
  return {
    invalidate(prefixes: readonly string[]) {
      const now = Date.now();
      for (const prefix of prefixes) {
        if (pending.has(prefix)) continue;
        const wait = (last.get(prefix) ?? 0) + minMs - now;
        if (wait <= 0) run(prefix);
        else pending.set(prefix, setTimeout(() => run(prefix), wait));
      }
    },
    dispose() {
      pending.forEach((t) => clearTimeout(t));
      pending.clear();
    },
  };
}

// -- connection state (the alerts bell polls only while disconnected) --------

let connected = false;
const connectionListeners = new Set<() => void>();

function setConnected(value: boolean): void {
  if (connected === value) return;
  connected = value;
  connectionListeners.forEach((l) => l());
}

/** True while the live stream is open in this tab. */
export function useLiveConnected(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      connectionListeners.add(onChange);
      return () => connectionListeners.delete(onChange);
    },
    () => connected,
    () => false,
  );
}

/** Scope of user, role and access changes (app.auth.live): the viewer's own access may have changed. */
export const ACCESS_SCOPE = "users";
/** At most one access re-check per this interval (bursts of user changes). */
export const ACCESS_RECHECK_MIN_MS = 5_000;

/**
 * Opens the console live stream while `enabled` (dashboard readers).
 * `onAccessChange` (throttled) runs when a frame reports user / access changes
 * or the server closes the stream, so the caller can re-read `/auth/me`.
 */
export function useConsoleLive(enabled: boolean, onAccessChange?: () => void): void {
  const accessChanged = useRef(onAccessChange);
  accessChanged.current = onAccessChange;

  useEffect(() => {
    if (!enabled || typeof window === "undefined" || typeof EventSource === "undefined") return undefined;
    const invalidator = createInvalidator();
    let lastAccessCheck = 0;
    const recheckAccess = () => {
      const now = Date.now();
      if (now - lastAccessCheck < ACCESS_RECHECK_MIN_MS) return;
      lastAccessCheck = now;
      accessChanged.current?.();
    };
    let source: EventSource | null = null;
    let retryMs = RETRY_MIN_MS;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let wasHidden = false;

    const close = () => {
      source?.close();
      source = null;
      setConnected(false);
    };
    const open = () => {
      if (source || document.visibilityState === "hidden") return;
      const es = new EventSource(LIVE_STREAM_URL);
      source = es;
      es.addEventListener("ready", () => {
        retryMs = RETRY_MIN_MS;
        setConnected(true);
        if (wasHidden) {
          wasHidden = false;
          invalidator.invalidate([...LIVE_DATA_PREFIXES, "admin/alerts", "admin/access-requests"]);
        }
      });
      es.addEventListener("changed", (event) => {
        try {
          const { scopes } = JSON.parse((event as MessageEvent<string>).data) as { scopes?: string[] };
          if (!Array.isArray(scopes)) return;
          invalidator.invalidate(prefixesForScopes(scopes));
          if (scopes.includes(ACCESS_SCOPE)) recheckAccess();
        } catch {
          // malformed frame: ignore
        }
      });
      es.onerror = () => {
        // The browser retries on its own after a drop; a closed stream
        // (auth, server gone) is retried here with backoff.
        if (es.readyState === EventSource.CLOSED) {
          close();
          recheckAccess();
          retryTimer = setTimeout(open, retryMs);
          retryMs = Math.min(retryMs * 2, RETRY_MAX_MS);
        } else {
          setConnected(false);
        }
      };
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        wasHidden = true;
        close();
      } else {
        open();
      }
    };
    open();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      if (retryTimer !== null) clearTimeout(retryTimer);
      invalidator.dispose();
      close();
    };
  }, [enabled]);
}
