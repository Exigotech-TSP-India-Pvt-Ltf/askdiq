"use client";

/**
 * Sign-in screen preload: while the login page is idle, loads the JavaScript
 * of the post-login landing routes (the console shell, the dashboard and
 * Chat), so the first screen after the SSO round-trip renders without
 * waiting on code. Code only: no API request is made before authentication
 * (route prefetches of signed-in pages are redirected to /login by the
 * middleware, so the chunks are loaded with dynamic `import()` instead).
 *
 * Skipped in reduced-data mode (`navigator.connection.saveData`) and on
 * 2G-class connections.
 */
import { useEffect } from "react";
import { runWhenIdle } from "@/lib/query-cache";

export type ChunkLoader = () => Promise<unknown>;

interface NetworkInformationLike {
  saveData?: boolean;
  effectiveType?: string;
}

const SLOW_CONNECTIONS = new Set(["slow-2g", "2g"]);

/** True when the user asked to save data or the connection is too slow to preload. */
export function codePreloadDisabled(nav: Navigator | undefined = globalThis.navigator): boolean {
  const connection = (nav as (Navigator & { connection?: NetworkInformationLike }) | undefined)?.connection;
  if (!connection) return false;
  return connection.saveData === true || SLOW_CONNECTIONS.has(connection.effectiveType ?? "");
}

/** Loads every chunk once idle (failures ignored). Returns the cancel function. */
export function preloadCode(loaders: readonly ChunkLoader[]): () => void {
  if (codePreloadDisabled()) return () => undefined;
  return runWhenIdle(() => {
    for (const load of loaders) void load().catch(() => undefined);
  });
}

/** Warms `loaders` once the page is idle (login screen). */
export function useCodePreload(loaders: readonly ChunkLoader[]): void {
  useEffect(() => preloadCode(loaders), [loaders]);
}
