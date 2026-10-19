"use client";

import { useEffect, useState } from "react";
import { visibleNavItems } from "@/components/Sidebar";
import { useAudience } from "@/lib/audience-context";
import { useAuth } from "@/lib/auth-context";
import { useConsoleLive } from "@/lib/live-updates";
import { hasPermission } from "@/lib/permissions";
import { queryCache, runWhenIdle } from "@/lib/query-cache";
import { restoreQueryCache, startQueryPersistence } from "@/lib/query-persist";
import { preloadConsole, prefetchShellData } from "@/lib/route-prefetch";

/**
 * Code split out of the pages (heavy charts, drawers): loaded once idle, so
 * their first render shows content, not a skeleton.
 */
function preloadLazyModules(): void {
  void import("@/components/admin/evaluation/QualityCharts").catch(() => undefined);
  void import("@/components/admin/usage/QueryInspectorDrawer").catch(() => undefined);
}

/** Delay after sign-in / an audience switch before the background preload. */
export const PRELOAD_DELAY_MS = 1_200;

/**
 * Console-wide data runtime (renders nothing), mounted once in the app layout:
 *
 * 1. restores this tab's persisted query cache for the signed-in user before
 *    the first page renders, and keeps it persisted;
 * 2. opens the live stream (pushed refreshes instead of polling);
 * 3. once idle, preloads every destination the user can open, so the first
 *    click on any page renders from cache.
 */
export function ConsoleRuntime() {
  const { me, token, permissions } = useAuth();
  const { audience } = useAudience();
  const owner = me?.user.id ?? "";

  // Restored synchronously in the first render, before the page below reads
  // the cache (this component renders ahead of the page; nothing subscribes
  // yet, so seeding notifies no one). The layout mounts only once the user
  // is known.
  useState(() => (owner ? restoreQueryCache(owner) : 0));

  useEffect(() => (owner ? startQueryPersistence(owner, queryCache) : undefined), [owner]);

  const canRead = hasPermission(permissions, "dashboard:read");
  useConsoleLive(Boolean(token) && canRead);

  useEffect(() => {
    if (!token) return undefined;
    let cancelPreload: (() => void) | null = null;
    const timer = setTimeout(() => {
      cancelPreload = runWhenIdle(() => {
        prefetchShellData(token);
        preloadLazyModules();
        const hrefs = visibleNavItems(permissions).map((item) => item.href);
        cancelPreload = preloadConsole(hrefs, token, audience);
      });
    }, PRELOAD_DELAY_MS);
    return () => {
      clearTimeout(timer);
      cancelPreload?.();
    };
  }, [token, audience, permissions]);

  return null;
}
