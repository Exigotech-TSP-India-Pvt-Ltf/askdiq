"use client";

/**
 * Small request cache for the admin console.
 *
 * - One in-flight promise per key: identical concurrent requests share a fetch.
 * - Stale-while-revalidate: cached data is returned immediately and refreshed
 *   in the background once it is older than `staleTime`.
 * - `AbortController`: the request is aborted when its last observer unmounts
 *   or switches to another key.
 * - Revalidates observed, stale keys on window focus / tab visible.
 * - Backs off after a failure (1 s, 2 s, 4 s ... 60 s): mounts, focus and
 *   stale checks do not re-request a failing key until then; explicit
 *   refetches (Try again, polling) still go through.
 * - `invalidate(prefix)` marks matching keys stale and refetches observed ones.
 * - Prefetch on intent (120 ms dwell) and idle prefetch of the next page.
 * - `useApiMutation`: optimistic update, rollback on failure, undo, and a
 *   typed `onConflict` hook (409) that the toast layer plugs into.
 *
 * No dependency: plain React (`useSyncExternalStore`) and the Fetch API.
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useToast } from "@/components/ui/ToastProvider";

export type QueryKeyPart = string | number | boolean | null | undefined;
/** `"admin/dashboard"` or `["admin/dashboard", { from, to }]`-style tuples of primitives. */
export type QueryKey = string | readonly QueryKeyPart[];
export type Fetcher<T> = (signal: AbortSignal) => Promise<T>;

export const DEFAULT_STALE_TIME_MS = 30_000;
/** Hover / focus dwell before an intent prefetch fires. */
export const INTENT_DELAY_MS = 120;
/** First wait after a failed request before stale checks refetch the key. */
export const ERROR_BACKOFF_BASE_MS = 1_000;
export const ERROR_BACKOFF_MAX_MS = 60_000;

/**
 * Stable string form of a key. Tuple keys join with "/" so that
 * `invalidate("admin/")` matches `["admin", "dashboard"]` as well as
 * `"admin/dashboard?from=..."`.
 */
export function serializeKey(key: QueryKey): string {
  if (typeof key === "string") return key;
  return key.map((part) => (part === null || part === undefined ? "" : String(part))).join("/");
}

export interface QueryState<T> {
  readonly data: T | undefined;
  readonly error: unknown;
  /** Epoch ms of the last successful fetch or manual set; 0 = never / invalidated. */
  readonly updatedAt: number;
  readonly isFetching: boolean;
}

const EMPTY_STATE: QueryState<never> = Object.freeze({
  data: undefined,
  error: undefined,
  updatedAt: 0,
  isFetching: false,
});

interface Observer {
  fetcher: () => Fetcher<unknown>;
  staleTime: number;
  revalidateOnFocus: boolean;
}

interface Entry {
  state: QueryState<unknown>;
  promise?: Promise<unknown>;
  controller?: AbortController;
  observers: Set<Observer>;
  listeners: Set<() => void>;
  /** In-flight prefetches holding the request open without an observer. */
  pins: number;
  /** Consecutive failed requests, and when stale checks may try again (epoch ms). */
  failures: number;
  retryAt: number;
}

function isAbortError(error: unknown): boolean {
  return (
    (typeof DOMException !== "undefined" && error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

const HTTP_UNAUTHORIZED = 401;
const HTTP_FORBIDDEN = 403;

/** A 401 / 403: the server refuses this user the resource. */
export function isAccessDenied(error: unknown): boolean {
  const status = errorStatus(error);
  return status === HTTP_UNAUTHORIZED || status === HTTP_FORBIDDEN;
}

const getServerSnapshot =(): QueryState<never> => EMPTY_STATE;

export class QueryCache {
  private readonly entries = new Map<string, Entry>();
  private readonly changeListeners = new Set<(key: string) => void>();
  private focusListenerInstalled = false;
  private gen = 0;

  /**
   * Bumped by `reset` / `clear`: hooks drop data they kept from before
   * (`keepPreviousData`), so nothing cached under the old access renders.
   */
  get generation(): number {
    return this.gen;
  }

  private entry(key: string): Entry {
    let e = this.entries.get(key);
    if (!e) {
      e = { state: EMPTY_STATE, observers: new Set(), listeners: new Set(), pins: 0, failures: 0, retryAt: 0 };
      this.entries.set(key, e);
    }
    return e;
  }

  private setState(key: string, patch: Partial<QueryState<unknown>>): void {
    const e = this.entry(key);
    e.state = { ...e.state, ...patch };
    for (const listener of Array.from(e.listeners)) listener();
    for (const listener of Array.from(this.changeListeners)) listener(key);
  }

  /** Called with the key after any state change (persistence). */
  onAnyChange(listener: (key: string) => void): () => void {
    this.changeListeners.add(listener);
    return () => this.changeListeners.delete(listener);
  }

  /**
   * Seeds `key` with data fetched earlier (persisted cache) unless it already
   * has data. `updatedAt` is kept, so stale data shows at once and
   * revalidates as usual.
   */
  hydrate(key: string, data: unknown, updatedAt: number): void {
    const e = this.entry(key);
    if (e.state.data !== undefined) return;
    e.state = { ...e.state, data, updatedAt };
    for (const listener of Array.from(e.listeners)) listener();
  }

  /** `[key, data, updatedAt]` of every entry holding data without an error. */
  persistable(): Array<[string, unknown, number]> {
    const out: Array<[string, unknown, number]> = [];
    this.entries.forEach((e, k) => {
      if (e.state.data !== undefined && !e.state.error && e.state.updatedAt > 0) {
        out.push([k, e.state.data, e.state.updatedAt]);
      }
    });
    return out;
  }

  getState<T>(key: QueryKey): QueryState<T> {
    return (this.entries.get(serializeKey(key))?.state ?? EMPTY_STATE) as QueryState<T>;
  }

  getData<T>(key: QueryKey): T | undefined {
    return this.getState<T>(key).data;
  }

  /** Writes data directly (optimistic updates, rollback, server push). */
  setData<T>(key: QueryKey, updater: T | undefined | ((current: T | undefined) => T | undefined)): void {
    const k = serializeKey(key);
    const current = this.entry(k).state.data as T | undefined;
    const next =
      typeof updater === "function" ? (updater as (c: T | undefined) => T | undefined)(current) : updater;
    this.setState(k, { data: next, error: undefined, updatedAt: Date.now() });
  }

  isStale(key: QueryKey, staleTime: number): boolean {
    const e = this.entries.get(serializeKey(key));
    if (e && e.retryAt > Date.now()) return false; // backing off after a failure
    const { updatedAt } = this.getState(key);
    return updatedAt === 0 || Date.now() - updatedAt >= staleTime;
  }

  subscribe(key: QueryKey, listener: () => void): () => void {
    const e = this.entry(serializeKey(key));
    e.listeners.add(listener);
    return () => e.listeners.delete(listener);
  }

  /**
   * Starts (or joins) the request for `key`. Concurrent calls share one
   * promise; `force` aborts the in-flight request and starts a new one.
   */
  fetch<T>(key: QueryKey, fetcher: Fetcher<T>, { force = false }: { force?: boolean } = {}): Promise<T> {
    const k = serializeKey(key);
    const e = this.entry(k);
    if (e.promise && !force) return e.promise as Promise<T>;
    e.controller?.abort();

    const controller = new AbortController();
    const promise = fetcher(controller.signal).then(
      (data) => {
        if (e.controller === controller) {
          e.promise = undefined;
          e.controller = undefined;
          e.failures = 0;
          e.retryAt = 0;
          this.setState(k, { data, error: undefined, updatedAt: Date.now(), isFetching: false });
        }
        return data;
      },
      (error: unknown) => {
        if (e.controller === controller) {
          e.promise = undefined;
          e.controller = undefined;
          if (controller.signal.aborted || isAbortError(error)) {
            this.setState(k, { isFetching: false });
          } else {
            e.failures += 1;
            e.retryAt =
              Date.now() + Math.min(ERROR_BACKOFF_BASE_MS * 2 ** (e.failures - 1), ERROR_BACKOFF_MAX_MS);
            // Refused (signed out, or access withdrawn): the cached data must
            // not keep rendering, so drop it with the error.
            const refused = isAccessDenied(error);
            this.setState(
              k,
              refused ? { data: undefined, updatedAt: 0, error, isFetching: false } : { error, isFetching: false },
            );
          }
        }
        throw error;
      },
    );
    // Callers that ignore the promise (observers) must not see unhandled rejections.
    promise.catch(() => undefined);
    e.controller = controller;
    e.promise = promise;
    this.setState(k, { isFetching: true });
    return promise;
  }

  /** Registers a mounted hook. Returns the unregister function. */
  observe(key: QueryKey, observer: Observer): () => void {
    const k = serializeKey(key);
    const e = this.entry(k);
    e.observers.add(observer);
    this.installFocusListener();
    return () => {
      e.observers.delete(observer);
      // Deferred so React StrictMode's unmount/remount (and a sibling taking
      // over the key in the same commit) re-joins instead of aborting.
      queueMicrotask(() => this.abortIfUnused(k));
    };
  }

  private abortIfUnused(k: string): void {
    const e = this.entries.get(k);
    if (!e?.controller || e.observers.size > 0 || e.pins > 0) return;
    const controller = e.controller;
    e.controller = undefined;
    e.promise = undefined;
    controller.abort();
    this.setState(k, { isFetching: false });
  }

  /** Warms `key` unless it is fresh or already loading. Never aborted by unmounts. */
  prefetch<T>(key: QueryKey, fetcher: Fetcher<T>, staleTime = DEFAULT_STALE_TIME_MS): Promise<T | undefined> {
    const k = serializeKey(key);
    const e = this.entry(k);
    if (e.promise) return e.promise as Promise<T>;
    if (!this.isStale(key, staleTime)) return Promise.resolve(e.state.data as T);
    e.pins += 1;
    return this.fetch(key, fetcher)
      .catch(() => undefined)
      .finally(() => {
        e.pins -= 1;
      });
  }

  /** Revalidates an observed key with its observer's fetcher. */
  private revalidate(k: string, e: Entry): void {
    const [observer] = Array.from(e.observers);
    if (!observer) return;
    this.fetch(k, observer.fetcher(), { force: true }).catch(() => undefined);
  }

  /**
   * Marks every key starting with `prefix` stale and refetches the observed
   * ones. Returns the number of keys invalidated.
   */
  invalidate(prefix: string): number {
    let count = 0;
    this.entries.forEach((e, k) => {
      if (!k.startsWith(prefix)) return;
      count += 1;
      this.setState(k, { updatedAt: 0 });
      this.revalidate(k, e);
    });
    return count;
  }

  /** Refetches observed keys that are stale (window focus / tab visible). */
  revalidateStale(): void {
    this.entries.forEach((e, k) => {
      const focusObservers = Array.from(e.observers).filter((o) => o.revalidateOnFocus);
      if (focusObservers.length === 0 || e.promise) return;
      const staleTime = Math.min(...focusObservers.map((o) => o.staleTime));
      if (this.isStale(k, staleTime)) this.revalidate(k, e);
    });
  }

  private installFocusListener(): void {
    if (this.focusListenerInstalled || typeof window === "undefined") return;
    this.focusListenerInstalled = true;
    const onFocus = () => this.revalidateStale();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") onFocus();
    });
  }

  /** Drops every entry and aborts in-flight requests (tests, sign-out). */
  clear(): void {
    this.gen += 1;
    this.entries.forEach((e) => e.controller?.abort());
    this.entries.clear();
  }

  /**
   * Drops all data, errors and in-flight requests while keeping mounted
   * subscribers attached (unlike `clear`), notifies them, then refetches the
   * observed keys under the current access (a key now refused is evicted).
   * Used when the signed-in user's role or permissions change.
   */
  reset(): void {
    this.gen += 1;
    this.entries.forEach((e, k) => {
      const controller = e.controller;
      e.controller = undefined;
      e.promise = undefined;
      controller?.abort();
      e.failures = 0;
      e.retryAt = 0;
      this.setState(k, { data: undefined, error: undefined, updatedAt: 0, isFetching: false });
    });
    this.entries.forEach((e, k) => {
      if (e.observers.size === 0 && e.listeners.size === 0) this.entries.delete(k);
      else this.revalidate(k, e);
    });
  }
}

/** App-wide cache instance. */
export const queryCache = new QueryCache();

export const invalidate = (prefix: string): number => queryCache.invalidate(prefix);

export function prefetch<T>(
  key: QueryKey,
  fetcher: Fetcher<T>,
  staleTime = DEFAULT_STALE_TIME_MS,
): Promise<T | undefined> {
  return queryCache.prefetch(key, fetcher, staleTime);
}

// ---------------------------------------------------------------------------
// useApiQuery
// ---------------------------------------------------------------------------

export interface UseApiQueryOptions {
  /** How long data counts as fresh (ms). Default 30 s. */
  staleTime?: number;
  /** `false` skips fetching (e.g. while the auth token is loading). */
  enabled?: boolean;
  revalidateOnFocus?: boolean;
  cache?: QueryCache;
  /**
   * While a new key loads, keep returning the last data this hook showed
   * (`isPlaceholderData`) instead of `undefined`: switching a range, tab or
   * filter swaps the charts in place rather than flashing skeletons.
   */
  keepPreviousData?: boolean;
}

export interface UseApiQueryResult<T> {
  data: T | undefined;
  /** `data` belongs to the previous key (`keepPreviousData`) while this one loads. */
  isPlaceholderData: boolean;
  error: unknown;
  /** No data yet and a request is running. */
  isLoading: boolean;
  /** Any request is running (including background revalidation). */
  isValidating: boolean;
  updatedAt: number;
  refetch: () => Promise<T | undefined>;
  /** Local write to this key (optimistic UI). */
  setData: (updater: T | undefined | ((current: T | undefined) => T | undefined)) => void;
}

/**
 * Cached GET. `key = null` disables the query. The fetcher receives an
 * `AbortSignal`; pass it to `fetch` so key changes and unmounts cancel work.
 */
export function useApiQuery<T>(
  key: QueryKey | null,
  fetcher: Fetcher<T>,
  {
    staleTime = DEFAULT_STALE_TIME_MS,
    enabled = true,
    revalidateOnFocus = true,
    cache = queryCache,
    keepPreviousData = false,
  }: UseApiQueryOptions = {},
): UseApiQueryResult<T> {
  const k = key === null ? null : serializeKey(key);
  const active = enabled && k !== null;
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const subscribe = useCallback(
    (onChange: () => void) => (k === null ? () => undefined : cache.subscribe(k, onChange)),
    [cache, k],
  );
  const getSnapshot = useCallback(
    () => (k === null ? (EMPTY_STATE as QueryState<T>) : cache.getState<T>(k)),
    [cache, k],
  );
  // The server never has cached data: hydration renders the server snapshot,
  // then React re-renders at once with the (persisted) client cache.
  const state = useSyncExternalStore<QueryState<T>>(subscribe, getSnapshot, getServerSnapshot);

  useEffect(() => {
    if (!active || k === null) return undefined;
    const unobserve = cache.observe(k, {
      fetcher: () => fetcherRef.current as Fetcher<unknown>,
      staleTime,
      revalidateOnFocus,
    });
    if (cache.isStale(k, staleTime)) {
      cache.fetch(k, (signal) => fetcherRef.current(signal)).catch(() => undefined);
    }
    return unobserve;
  }, [active, cache, k, staleTime, revalidateOnFocus]);

  const refetch = useCallback(async (): Promise<T | undefined> => {
    if (k === null) return undefined;
    try {
      return await cache.fetch(k, (signal) => fetcherRef.current(signal), { force: true });
    } catch {
      return undefined;
    }
  }, [cache, k]);

  const setData = useCallback<UseApiQueryResult<T>["setData"]>(
    (updater) => {
      if (k !== null) cache.setData<T>(k, updater);
    },
    [cache, k],
  );

  // Last data this hook rendered (any key), for `keepPreviousData`.
  const shown = useRef<T | undefined>(undefined);
  const shownGeneration = useRef(cache.generation);
  if (shownGeneration.current !== cache.generation) {
    shown.current = undefined;
    shownGeneration.current = cache.generation;
  }
  if (state.data !== undefined) shown.current = state.data;
  const placeholder =
    keepPreviousData && active && state.data === undefined && !state.error ? shown.current : undefined;
  // `null` is data (an optional endpoint that is absent), not "missing".
  const data = state.data !== undefined ? state.data : placeholder;

  return {
    data,
    isPlaceholderData: placeholder !== undefined,
    error: state.error,
    isLoading: active && data === undefined && (state.isFetching || state.updatedAt === 0) && !state.error,
    isValidating: state.isFetching,
    updatedAt: state.updatedAt,
    refetch,
    setData,
  };
}

// ---------------------------------------------------------------------------
// Prefetch
// ---------------------------------------------------------------------------

export interface IntentHandlers {
  onMouseEnter: () => void;
  onMouseLeave: () => void;
  onFocus: () => void;
  onBlur: () => void;
}

/**
 * Spread the returned handlers on a KPI card, tab or row. After a 120 ms
 * dwell (hover or focus) the target page's first query is warmed; leaving
 * earlier cancels it.
 */
export function usePrefetchOnIntent<T>(
  key: QueryKey | null,
  fetcher: Fetcher<T>,
  {
    delay = INTENT_DELAY_MS,
    staleTime = DEFAULT_STALE_TIME_MS,
    cache = queryCache,
  }: { delay?: number; staleTime?: number; cache?: QueryCache } = {},
): IntentHandlers {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const k = key === null ? null : serializeKey(key);

  const cancel = useCallback(() => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const start = useCallback(() => {
    if (k === null || timer.current !== null) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      cache.prefetch(k, (signal) => fetcherRef.current(signal), staleTime).catch(() => undefined);
    }, delay);
  }, [cache, delay, k, staleTime]);

  useEffect(() => cancel, [cancel, k]);

  return useMemo(
    () => ({ onMouseEnter: start, onMouseLeave: cancel, onFocus: start, onBlur: cancel }),
    [start, cancel],
  );
}

type IdleCallback = (cb: () => void) => number;
type CancelIdle = (handle: number) => void;

/** Runs `cb` when the browser is idle (`requestIdleCallback`, `setTimeout` fallback). */
export function runWhenIdle(cb: () => void): () => void {
  const w = typeof window === "undefined" ? undefined : (window as Window & {
    requestIdleCallback?: IdleCallback;
    cancelIdleCallback?: CancelIdle;
  });
  if (w?.requestIdleCallback && w.cancelIdleCallback) {
    const handle = w.requestIdleCallback(cb);
    const cancelIdle = w.cancelIdleCallback;
    return () => cancelIdle(handle);
  }
  const handle = setTimeout(cb, 1);
  return () => clearTimeout(handle);
}

/** Warms the next pagination page while idle. Returns a cancel function. */
export function prefetchNextPage<T>(
  key: QueryKey,
  fetcher: Fetcher<T>,
  { staleTime = DEFAULT_STALE_TIME_MS, cache = queryCache }: { staleTime?: number; cache?: QueryCache } = {},
): () => void {
  return runWhenIdle(() => {
    cache.prefetch(key, fetcher, staleTime).catch(() => undefined);
  });
}

/** Hook form of `prefetchNextPage`; pass `null` when there is no next page. */
export function usePrefetchNextPage<T>(
  key: QueryKey | null,
  fetcher: Fetcher<T>,
  options: { staleTime?: number; cache?: QueryCache } = {},
): void {
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const k = key === null ? null : serializeKey(key);
  const { staleTime, cache } = options;
  useEffect(() => {
    if (k === null) return undefined;
    return prefetchNextPage(k, (signal) => fetcherRef.current(signal), { staleTime, cache });
  }, [k, staleTime, cache]);
}

// ---------------------------------------------------------------------------
// useApiMutation
// ---------------------------------------------------------------------------

export const HTTP_CONFLICT = 409;

/** Status of an `ApiError` (lib/api.ts) or any error carrying a numeric `status`. */
export function errorStatus(error: unknown): number | undefined {
  if (typeof error === "object" && error !== null && "status" in error) {
    const { status } = error as { status: unknown };
    if (typeof status === "number") return status;
  }
  return undefined;
}

export interface OptimisticUpdate<TVars, TData> {
  /** Cached query to update before the request settles. */
  key: QueryKey;
  update: (current: TData | undefined, vars: TVars) => TData;
}

export interface MutationConflict<TVars> {
  error: unknown;
  vars: TVars;
  /** Keys refetched because of the conflict. */
  refetched: readonly string[];
}

export interface UseApiMutationOptions<TVars, TResult, TData = unknown> {
  mutationFn: (vars: TVars) => Promise<TResult>;
  optimistic?: OptimisticUpdate<TVars, TData>;
  /** Custom rollback; the default restores the cached value from before `optimistic`. */
  rollback?: (previous: TData | undefined, vars: TVars, error: unknown) => void;
  /** Key prefixes to invalidate after success (and after a conflict). */
  invalidate?: readonly string[];
  onSuccess?: (result: TResult, vars: TVars) => void;
  onError?: (error: unknown, vars: TVars) => void;
  /**
   * 409 Conflict: the optimistic change is rolled back, the affected keys are
   * refetched, then this fires. Defaults to the "changed by someone else"
   * toast (`CONFLICT_TOAST`); pass a handler to replace it.
   */
  onConflict?: (conflict: MutationConflict<TVars>) => void;
  cache?: QueryCache;
}

export interface UseApiMutationResult<TVars, TResult> {
  /** Resolves to the result, or `undefined` on failure (see `error`). */
  mutate: (vars: TVars) => Promise<TResult | undefined>;
  /** Like `mutate` but rethrows the error. */
  mutateAsync: (vars: TVars) => Promise<TResult>;
  isPending: boolean;
  error: unknown;
  /** Restores the cache to before the last successful optimistic mutation (toast "Undo"). */
  undo: () => boolean;
  canUndo: boolean;
  reset: () => void;
}

/** Default 409 toast shown by `useApiMutation` when no `onConflict` is given. */
export const CONFLICT_TOAST = {
  title: "Changed by someone else",
  description: "This record was updated elsewhere. The latest version has been loaded; review it and try again.",
  tone: "info",
} as const;

interface UndoSnapshot<TData> {
  key: string;
  previous: TData | undefined;
}

export function useApiMutation<TVars, TResult, TData = unknown>(
  options: UseApiMutationOptions<TVars, TResult, TData>,
): UseApiMutationResult<TVars, TResult> {
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const cache = options.cache ?? queryCache;
  const toast = useToast();
  const [isPending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [undoSnapshot, setUndoSnapshot] = useState<UndoSnapshot<TData> | null>(null);

  const mutateAsync = useCallback(
    async (vars: TVars): Promise<TResult> => {
      const opts = optionsRef.current;
      let snapshot: UndoSnapshot<TData> | null = null;
      if (opts.optimistic) {
        const k = serializeKey(opts.optimistic.key);
        const update = opts.optimistic.update;
        snapshot = { key: k, previous: cache.getData<TData>(k) };
        cache.setData<TData>(k, (current) => update(current, vars));
      }
      setPending(true);
      setError(undefined);
      try {
        const result = await opts.mutationFn(vars);
        setUndoSnapshot(snapshot);
        for (const prefix of opts.invalidate ?? []) cache.invalidate(prefix);
        opts.onSuccess?.(result, vars);
        return result;
      } catch (err) {
        if (snapshot) {
          if (opts.rollback) opts.rollback(snapshot.previous, vars, err);
          else cache.setData<TData>(snapshot.key, snapshot.previous);
        }
        setError(err);
        if (errorStatus(err) === HTTP_CONFLICT) {
          const refetched = [...(snapshot ? [snapshot.key] : []), ...(opts.invalidate ?? [])];
          for (const prefix of refetched) cache.invalidate(prefix);
          if (opts.onConflict) opts.onConflict({ error: err, vars, refetched });
          else toast.show(CONFLICT_TOAST);
        }
        opts.onError?.(err, vars);
        throw err;
      } finally {
        setPending(false);
      }
    },
    [cache, toast],
  );

  const mutate = useCallback(
    async (vars: TVars): Promise<TResult | undefined> => {
      try {
        return await mutateAsync(vars);
      } catch {
        return undefined;
      }
    },
    [mutateAsync],
  );

  const undo = useCallback((): boolean => {
    if (!undoSnapshot) return false;
    cache.setData<TData>(undoSnapshot.key, undoSnapshot.previous);
    setUndoSnapshot(null);
    return true;
  }, [cache, undoSnapshot]);

  const reset = useCallback(() => {
    setError(undefined);
    setUndoSnapshot(null);
  }, []);

  return { mutate, mutateAsync, isPending, error, undo, canUndo: undoSnapshot !== null, reset };
}
