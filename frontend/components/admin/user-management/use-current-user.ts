"use client";

/**
 * Local permission shim for User Management.
 *
 * PR-18 (`feat/ui-auth-gate-login`) adds the real auth context with
 * `usePermission()` / `<RequirePermission>`; until it lands this reads
 * `GET /auth/me` directly through the query cache. Replace `useCan` with
 * `usePermission` when consolidating; the call sites keep the same shape.
 * Fails closed: no `/auth/me` data means no permission.
 */
import { useCallback } from "react";
import { useApiQuery } from "@/lib/query-cache";
import { umKeys, userManagementApi, type Me, type Permission } from "@/lib/user-management-api";

const ME_STALE_MS = 60_000;

export interface CurrentUser {
  me: Me | undefined;
  isLoading: boolean;
  error: unknown;
  can: (permission: Permission) => boolean;
}

export function useCurrentUser(): CurrentUser {
  const { data, isLoading, error } = useApiQuery(umKeys.me, (signal) => userManagementApi.me(signal), {
    staleTime: ME_STALE_MS,
  });
  const can = useCallback(
    (permission: Permission) => data?.status === "active" && data.permissions.includes(permission),
    [data],
  );
  return { me: data, isLoading, error, can };
}

export function useCan(permission: Permission): boolean {
  return useCurrentUser().can(permission);
}
