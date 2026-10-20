"use client";

/**
 * User Management's view of the signed-in user: the session already held by
 * the auth context (preloaded with the first HTML, refreshed by
 * `AuthProvider`), so opening the page costs no `/auth/me` round trip and
 * its data requests never wait behind one. Permission checks fail closed.
 */
import { useCallback } from "react";
import { useAuth, usePermission } from "@/lib/auth-context";
import { hasPermission } from "@/lib/permissions";
import type { Me, Permission } from "@/lib/user-management-api";

export interface CurrentUser {
  me: Me | undefined;
  isLoading: boolean;
  error: unknown;
  can: (permission: Permission) => boolean;
}

export function useCurrentUser(): CurrentUser {
  const { me, isLoading, error, permissions, status } = useAuth();
  const can = useCallback(
    (permission: Permission) => status === "active" && hasPermission(permissions, permission),
    [permissions, status],
  );
  return { me: me ?? undefined, isLoading, error, can };
}

export function useCan(permission: Permission): boolean {
  return usePermission(permission);
}
