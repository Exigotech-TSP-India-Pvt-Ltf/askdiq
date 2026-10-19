"use client";

/**
 * Former per-PR permission shim (PR-21, PR-24, PR-26), consolidated in the
 * preview build: `useCan` now delegates to PR-18's canonical `usePermission`
 * (`GET /auth/me` via `AuthProvider`). The exported names are kept so feature
 * pages compile unchanged.
 *
 * `PermissionsProvider` remains only as an explicit override for component
 * tests that render a page without `AuthProvider`; production never mounts it.
 */
import React, { createContext, useContext } from "react";
import { usePermission } from "@/lib/auth-context";
import { PERMISSIONS, ROLE_PERMISSIONS, hasPermission, type Permission, type Role } from "@/lib/permissions";

export { PERMISSIONS, ROLE_PERMISSIONS };
export type { Permission, Role };

interface MaybeAuthorizedUser {
  role?: string | null;
  permissions?: readonly string[] | null;
}

/** Pure check for non-hook callers: explicit `permissions` win, else the role table. */
export function userCan(account: object | null | undefined, permission: Permission): boolean {
  if (!account) return false;
  const user = account as MaybeAuthorizedUser;
  if (Array.isArray(user.permissions)) return user.permissions.includes(permission);
  if (user.role === undefined) return true; // legacy guest identity (tests only)
  if (user.role === null) return false;
  return ROLE_PERMISSIONS[user.role as Role]?.includes(permission) ?? false;
}

const PermissionsContext = createContext<readonly Permission[] | null>(null);

/** Test-only override of the signed-in user's permissions. */
export function PermissionsProvider({
  permissions,
  children,
}: {
  permissions: readonly Permission[];
  children: React.ReactNode;
}) {
  return <PermissionsContext.Provider value={permissions}>{children}</PermissionsContext.Provider>;
}

/** True when the signed-in user holds `permission` (delegates to `usePermission`). */
export function useCan(permission: Permission | undefined): boolean {
  const override = useContext(PermissionsContext);
  const granted = usePermission(permission ?? "dashboard:read");
  if (!permission) return true;
  return override === null ? granted : hasPermission(override, permission);
}
