"use client";

/**
 * Tiny permission gate for feature pages until PR-18 lands `usePermission` /
 * `<RequirePermission>` backed by `GET /auth/me` (consolidate then).
 *
 * Permission names match `app/auth/permissions.py`. Without a provider every
 * permission is granted, which matches today's no-login guest build; tests and
 * PR-18 wrap the tree in `PermissionsProvider` with the signed-in user's list.
 */
import React, { createContext, useContext } from "react";

export type Permission =
  | "dashboard:read"
  | "documents:write"
  | "feedback:triage"
  | "feedback:export"
  | "eval:run"
  | "testip:write"
  | "usage:export"
  | "users:manage"
  | "audit:auth:read"
  | "branding:write"
  | "taxonomy:write";

/** Role table mirrored from the backend (newplan §7.3). */
export const ROLE_PERMISSIONS: Record<"operator" | "admin" | "superadmin", readonly Permission[]> = {
  operator: ["dashboard:read"],
  admin: ["dashboard:read", "documents:write", "feedback:triage", "feedback:export", "eval:run", "testip:write", "usage:export"],
  superadmin: [
    "dashboard:read",
    "documents:write",
    "feedback:triage",
    "feedback:export",
    "eval:run",
    "testip:write",
    "usage:export",
    "users:manage",
    "audit:auth:read",
    "branding:write",
    "taxonomy:write",
  ],
};

const PermissionsContext = createContext<readonly Permission[] | null>(null);

export function PermissionsProvider({
  permissions,
  children,
}: {
  permissions: readonly Permission[];
  children: React.ReactNode;
}) {
  return <PermissionsContext.Provider value={permissions}>{children}</PermissionsContext.Provider>;
}

/** True when the current user holds `permission`. */
export function useCan(permission: Permission): boolean {
  const granted = useContext(PermissionsContext);
  return granted === null ? true : granted.includes(permission);
}
