"use client";

import { useMemo } from "react";
import { useAuth } from "@/lib/auth-context";
import { fetchPendingAccessCount } from "@/lib/auth-api";
import { hasPermission } from "@/lib/permissions";
import { useApiQuery } from "@/lib/query-cache";
import { useVisibilityPolling } from "@/lib/use-visibility-polling";
import type { NotificationItem } from "@/components/shell/NotificationBell";

export const PENDING_ACCESS_QUERY_KEY = "admin/access-requests/pending-count";
/** Badge refresh (paused when the tab is hidden); focus also revalidates. */
export const PENDING_ACCESS_POLL_MS = 60_000;
const STALE_MS = 30_000;
export const USER_MANAGEMENT_REQUESTS_HREF = "/admin/user-management?tab=requests";

/**
 * Open access requests for superadmins (`users:manage`); 0 and no request for
 * everyone else. Sidebar badge and notification bell share one cached fetch.
 */
export function usePendingAccessCount(): number {
  const { permissions } = useAuth();
  const allowed = hasPermission(permissions, "users:manage");
  const query = useApiQuery<number>(allowed ? PENDING_ACCESS_QUERY_KEY : null, (signal) => fetchPendingAccessCount(signal), {
    staleTime: STALE_MS,
  });
  useVisibilityPolling(() => void query.refetch(), PENDING_ACCESS_POLL_MS, allowed);
  return allowed ? query.data ?? 0 : 0;
}

/**
 * Header bell feed. Access requests come from `GET /admin/access-requests`.
 * TODO(F3/F4): CRITICAL incidents and feedback spikes once their SSE feeds
 * (governance stream, feedback summary) are in this branch's base.
 */
export function useShellNotifications(): NotificationItem[] {
  const pending = usePendingAccessCount();
  return useMemo<NotificationItem[]>(
    () =>
      pending > 0
        ? [
            {
              id: "pending-access-requests",
              title: `${pending} access request${pending === 1 ? "" : "s"} waiting`,
              description: "Review them in User Management.",
              href: USER_MANAGEMENT_REQUESTS_HREF,
              tone: "warn",
            },
          ]
        : [],
    [pending],
  );
}
