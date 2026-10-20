"use client";

/**
 * Persists the app-wide query cache to `sessionStorage`, so a reload, a new
 * page load in the same tab or Back / Forward renders every visited view from
 * the last data at once (then revalidates as usual: entries keep their
 * `updatedAt`, so anything older than its stale time refetches in the
 * background).
 *
 * - Scoped to the signed-in user's access (`owner` = `cacheScope(me)`: user
 *   id, role, status and a hash of the permission list): data cached under
 *   another user, or the same user with other access, is never restored.
 *   Sign-out removes it (`clearPersistedQueryCache`); an access change seen
 *   while signed in drops it with the memory cache (`discardCachedData`).
 * - A 401 / 403 evicts that key from memory (QueryCache) and at once from
 *   storage.
 * - sessionStorage only (one tab, gone when it closes), never localStorage.
 * - Bounded: entries above `MAX_ENTRY_CHARS` are skipped and the newest
 *   entries are kept up to `MAX_TOTAL_CHARS`. Writes are debounced and run
 *   when the browser is idle; every storage call is guarded (private mode,
 *   quota, disabled storage).
 */
import type { AuthMe } from "@/lib/auth-api";
import { isAccessDenied, queryCache, runWhenIdle, type QueryCache } from "@/lib/query-cache";

export const PERSIST_STORAGE_KEY = "askdiq.query-cache.v1";
export const MAX_ENTRY_CHARS = 300_000;
export const MAX_TOTAL_CHARS = 3_000_000;
export const PERSIST_DEBOUNCE_MS = 1_000;
/** Older entries are not worth restoring (they would only flash old numbers). */
export const MAX_RESTORE_AGE_MS = 6 * 60 * 60_000;
/** Keys never persisted: identity, settings forms and chat state. */
const DENY_PREFIXES = ["auth/", "settings/", "sessions", "chat/"];

interface Persisted {
  v: 1;
  owner: string;
  entries: Array<[string, unknown, number]>;
}

/** 32-bit FNV-1a, hex: a short stable fingerprint (not a secret). */
function fnv1a(text: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

/**
 * Persistence scope of the signed-in user: id, role, status and a stable hash
 * of the sorted permission list. Any change of access gives a new scope, so
 * cached data is only ever restored for exactly the access it was read with.
 * "" when signed out.
 */
export function cacheScope(me: Pick<AuthMe, "user" | "role" | "status" | "permissions"> | null): string {
  if (!me?.user.id) return "";
  const permissions = Array.from(new Set(me.permissions ?? [])).sort().join(",");
  return [me.user.id, me.role ?? "-", me.status, fnv1a(permissions)].join("|");
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

function persistableKey(key: string): boolean {
  return !DENY_PREFIXES.some((prefix) => key.startsWith(prefix));
}

/** Seeds `cache` from storage for `owner`; returns the number of entries restored. */
export function restoreQueryCache(owner: string, cache: QueryCache = queryCache, now: number = Date.now()): number {
  const store = storage();
  if (!store || !owner) return 0;
  let parsed: Persisted | null = null;
  try {
    const raw = store.getItem(PERSIST_STORAGE_KEY);
    parsed = raw ? (JSON.parse(raw) as Persisted) : null;
  } catch {
    parsed = null;
  }
  if (!parsed || parsed.v !== 1 || !Array.isArray(parsed.entries)) return 0;
  if (parsed.owner !== owner) {
    clearPersistedQueryCache();
    return 0;
  }
  let restored = 0;
  for (const entry of parsed.entries) {
    if (!Array.isArray(entry) || entry.length !== 3) continue;
    const [key, data, updatedAt] = entry;
    if (typeof key !== "string" || typeof updatedAt !== "number" || data === undefined) continue;
    if (!persistableKey(key) || now - updatedAt > MAX_RESTORE_AGE_MS) continue;
    cache.hydrate(key, data, updatedAt);
    restored += 1;
  }
  return restored;
}

/**
 * JSON of each data object already serialised (data objects are replaced, not
 * mutated, on refetch): a write only stringifies what changed, so persisting
 * never becomes a long main-thread task.
 */
const encoded = new WeakMap<object, string>();

function encodeData(data: unknown): string | null {
  const memo = typeof data === "object" && data !== null;
  if (memo) {
    const hit = encoded.get(data as object);
    if (hit !== undefined) return hit;
  }
  let json: string | undefined;
  try {
    json = JSON.stringify(data);
  } catch {
    return null;
  }
  if (json === undefined) return null;
  if (memo) encoded.set(data as object, json);
  return json;
}

/** Serialises the newest entries within the size bounds. */
export function serializeQueryCache(owner: string, cache: QueryCache = queryCache): string {
  const rows = cache
    .persistable()
    .filter(([key]) => persistableKey(key))
    .sort((a, b) => b[2] - a[2]);
  const parts: string[] = [];
  let total = 0;
  for (const [key, data, updatedAt] of rows) {
    const json = encodeData(data);
    if (json === null || json.length > MAX_ENTRY_CHARS) continue;
    const part = `[${JSON.stringify(key)},${json},${updatedAt}]`;
    if (total + part.length > MAX_TOTAL_CHARS) continue;
    parts.push(part);
    total += part.length;
  }
  return `{"v":1,"owner":${JSON.stringify(owner)},"entries":[${parts.join(",")}]}`;
}

function write(owner: string, cache: QueryCache): void {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(PERSIST_STORAGE_KEY, serializeQueryCache(owner, cache));
  } catch {
    // Quota or disabled storage: persistence is a convenience only.
  }
}

/**
 * Keeps storage in step with `cache` for `owner` (debounced, at idle, and on
 * `pagehide`). Returns the stop function.
 */
export function startQueryPersistence(owner: string, cache: QueryCache = queryCache): () => void {
  if (!owner || !storage()) return () => undefined;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let cancelIdle: (() => void) | null = null;
  const flush = () => write(owner, cache);
  const schedule = (key: string) => {
    // A refused key leaves storage now, not after the debounce.
    if (isAccessDenied(cache.getState(key).error)) {
      flush();
      return;
    }
    if (timer !== null) return;
    timer = setTimeout(() => {
      timer = null;
      cancelIdle = runWhenIdle(flush);
    }, PERSIST_DEBOUNCE_MS);
  };
  const stopListening = cache.onAnyChange(schedule);
  window.addEventListener("pagehide", flush);
  const stop = () => {
    active.delete(stop);
    stopListening();
    window.removeEventListener("pagehide", flush);
    if (timer !== null) clearTimeout(timer);
    timer = null;
    cancelIdle?.();
    cancelIdle = null;
  };
  active.add(stop);
  return stop;
}

/** Stop functions of the running persistences (all stopped on an access change). */
const active = new Set<() => void>();

/**
 * The signed-in user's access changed (role, status or permissions): stops
 * persisting under the old scope, removes the stored copy and drops every
 * cached entry (mounted views refetch under the new access; refused keys are
 * evicted). The console runtime then persists and preloads under the new scope.
 */
export function discardCachedData(cache: QueryCache = queryCache): void {
  for (const stop of Array.from(active)) stop();
  clearPersistedQueryCache();
  cache.reset();
}

export function clearPersistedQueryCache(): void {
  try {
    storage()?.removeItem(PERSIST_STORAGE_KEY);
  } catch {
    // ignore
  }
}
