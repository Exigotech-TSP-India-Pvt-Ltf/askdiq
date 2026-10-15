"use client";

/**
 * User Management's view of the signed-in user. Consolidated in the preview
 * build: permission checks delegate to PR-18's auth context (`usePermission`
 * semantics, fails closed), while `me` keeps the page's `GET /auth/me` shape.
 */
import { useCallback } from "react";
import { useAuth, usePermission } from "@/lib/auth-context";
import { hasPermission } from "@/lib/permissions";
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
  const { permissions, status } = useAuth();
  const can = useCallback(
    (permission: Permission) => status === "active" && hasPermission(permissions, permission),
    [permissions, status],
  );
  return { me: data, isLoading, error, can };
}

export function useCan(permission: Permission): boolean {
  return usePermission(permission);
}
