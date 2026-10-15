"use client";

/**
 * URL state for the User Management page: tab, Users filters (drill-down from
 * the KPI cards and charts), the open drawer, the request view and the audit
 * filter all live in the query string, so links and back / forward restore
 * the same view.
 */
import { useCallback, useMemo } from "react";
import { useDeepLink, type DeepLinkUpdate } from "@/lib/use-deep-link";
import {
  USER_SORT_KEYS,
  isAccessRequestView,
  isAuthEvent,
  isRole,
  isUserStatus,
  type AccessRequestView,
  type AuthEvent,
  type Role,
  type SortOrder,
  type UserSortKey,
  type UserStatus,
} from "@/lib/user-management-api";

export const UM_TABS = ["users", "requests", "roles", "audit"] as const;
export type UmTab = (typeof UM_TABS)[number];

export interface UmUrlState {
  tab: UmTab;
  q: string;
  role: Role | null;
  status: UserStatus | null;
  sort: UserSortKey;
  order: SortOrder;
  page: number;
  user: string | null;
  view: AccessRequestView;
  action: AuthEvent | null;
}

export const DEFAULT_SORT: UserSortKey = "created_at";

/** Builds an `/admin/user-management` href (KPI card links, drill-downs). */
export function umHref(update: DeepLinkUpdate): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(update)) {
    if (value !== null && value !== undefined && value !== "") qs.set(key, String(value));
  }
  const s = qs.toString();
  return `/admin/user-management${s ? `?${s}` : ""}`;
}

export function parseUmState(get: (key: string) => string | null): UmUrlState {
  const tab = get("tab");
  const role = get("role");
  const status = get("status");
  const sort = get("sort");
  const page = Number(get("page"));
  const view = get("view");
  const action = get("action");
  return {
    tab: (UM_TABS as readonly string[]).includes(tab ?? "") ? (tab as UmTab) : "users",
    q: (get("q") ?? "").slice(0, 100),
    role: isRole(role) ? role : null,
    status: isUserStatus(status) ? status : null,
    sort: (USER_SORT_KEYS as readonly string[]).includes(sort ?? "") ? (sort as UserSortKey) : DEFAULT_SORT,
    order: get("order") === "asc" ? "asc" : "desc",
    page: Number.isInteger(page) && page >= 1 ? page : 1,
    user: get("user"),
    view: isAccessRequestView(view) ? view : "pending",
    action: isAuthEvent(action) ? action : null,
  };
}

export function useUmUrl() {
  const { get, set } = useDeepLink();
  const state = useMemo(() => parseUmState(get), [get]);
  const update = useCallback((patch: DeepLinkUpdate) => set(patch), [set]);
  return { state, update };
}
