"use client";

import { useMemo } from "react";
import { useAuth } from "@/lib/auth-context";
import { fetchActiveAlerts, type ActiveAlert, type AlertSeverity } from "@/lib/alerts-api";
import { fetchPendingAccessCount } from "@/lib/auth-api";
import { useLiveConnected } from "@/lib/live-updates";
import { hasPermission } from "@/lib/permissions";
import { useApiQuery } from "@/lib/query-cache";
import { formatRelativeTime } from "@/lib/time";
import { useVisibilityPolling } from "@/lib/use-visibility-polling";
import type { NotificationItem } from "@/components/shell/NotificationBell";

export const PENDING_ACCESS_QUERY_KEY = "admin/access-requests/pending-count";
export const ACTIVE_ALERTS_QUERY_KEY = "admin/alerts/active";
/** Badge refresh (paused when the tab is hidden); focus also revalidates. */
export const PENDING_ACCESS_POLL_MS = 60_000;
/** Fallback alerts refresh while the live stream is disconnected. */
export const ACTIVE_ALERTS_POLL_MS = 30_000;
const STALE_MS = 30_000;
export const USER_MANAGEMENT_REQUESTS_HREF = "/admin/user-management?tab=requests";
/** Access-request alerts are already summarised by the pending-requests item. */
const ACCESS_REQUEST_PATH = "/admin/user-management";

const ALERT_TONE: Record<AlertSeverity, NotificationItem["tone"]> = {
  critical: "danger",
  warning: "warn",
  info: "info",
};

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

/** Open and acknowledged alerts (`dashboard:read`); empty for everyone else. */
export function useActiveAlerts(): ActiveAlert[] {
  const { permissions } = useAuth();
  const allowed = hasPermission(permissions, "dashboard:read");
  const query = useApiQuery<ActiveAlert[]>(allowed ? ACTIVE_ALERTS_QUERY_KEY : null, (signal) => fetchActiveAlerts(signal), {
    staleTime: ACTIVE_ALERTS_POLL_MS / 2,
  });
  // Pushed by the console live stream ("alerts" scope); polling is only the
  // fallback while the stream is down.
  const live = useLiveConnected();
  useVisibilityPolling(() => void query.refetch(), ACTIVE_ALERTS_POLL_MS, allowed && !live);
  return allowed ? query.data ?? [] : [];
}

export function alertNotification(alert: ActiveAlert, now: number = Date.now()): NotificationItem {
  const seen = alert.lastSeenAt ? new Date(alert.lastSeenAt) : null;
  return {
    id: `alert-${alert.id}`,
    title: alert.title,
    description: alert.summary || undefined,
    href: alert.consolePath,
    tone: ALERT_TONE[alert.severity],
    time: seen && !Number.isNaN(seen.getTime()) ? formatRelativeTime(seen, now) : undefined,
  };
}

/** Header bell feed: pending access requests, then active alerts (newest first). */
export function useShellNotifications(): NotificationItem[] {
  const pending = usePendingAccessCount();
  const alerts = useActiveAlerts();
  return useMemo<NotificationItem[]>(() => {
    const items: NotificationItem[] = [];
    if (pending > 0) {
      items.push({
        id: "pending-access-requests",
        title: `${pending} access request${pending === 1 ? "" : "s"} waiting`,
        description: "Review them in User Management.",
        href: USER_MANAGEMENT_REQUESTS_HREF,
        tone: "warn",
      });
    }
    for (const alert of alerts) {
      if (pending > 0 && alert.consolePath.startsWith(ACCESS_REQUEST_PATH)) continue;
      items.push(alertNotification(alert));
    }
    return items;
  }, [pending, alerts]);
}
