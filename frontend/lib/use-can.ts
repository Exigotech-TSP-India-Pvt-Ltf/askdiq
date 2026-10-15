"use client";

/**
 * Tiny permission check for the Settings page and its nav item (planchanges4
 * F7). PR-18 replaces this with `usePermission()` / `<RequirePermission>`
 * backed by `GET /auth/me`; consolidate there.
 *
 * Mirrors `backend/app/auth/permissions.py` (ROLE_PERMISSIONS). The API is the
 * authority: hiding a control here never replaces the server-side guard.
 *
 * Until real sessions land the app uses a shared guest identity with no role,
 * which today sees every admin page; it keeps that behaviour (full access) so
 * nothing disappears before PR-18. A user that carries `permissions` (from
 * `/auth/me`) or a `role` is checked against them.
 */
import { useAuth } from "@/lib/auth-context";

export const PERMISSIONS = [
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
] as const;
export type Permission = (typeof PERMISSIONS)[number];
export type Role = "superadmin" | "admin" | "operator";

const OPERATOR: readonly Permission[] = ["dashboard:read"];
const ADMIN: readonly Permission[] = [
  ...OPERATOR,
  "documents:write",
  "feedback:triage",
  "feedback:export",
  "eval:run",
  "testip:write",
  "usage:export",
];
const SUPERADMIN: readonly Permission[] = [...ADMIN, "users:manage", "audit:auth:read", "branding:write", "taxonomy:write"];

export const ROLE_PERMISSIONS: Record<Role, ReadonlySet<Permission>> = {
  operator: new Set(OPERATOR),
  admin: new Set(ADMIN),
  superadmin: new Set(SUPERADMIN),
};

interface MaybeAuthorizedUser {
  role?: string | null;
  permissions?: readonly string[] | null;
}

/** Pure check used by `useCan` (exported for tests and non-hook callers). */
export function userCan(account: object | null | undefined, permission: Permission): boolean {
  if (!account) return false;
  const user = account as MaybeAuthorizedUser;
  if (Array.isArray(user.permissions)) return user.permissions.includes(permission);
  if (user.role === undefined) return true; // pre-PR-18 guest identity (see module doc)
  if (user.role === null) return false;
  return ROLE_PERMISSIONS[user.role as Role]?.has(permission) ?? false;
}

export function useCan(permission: Permission | undefined): boolean {
  const { user } = useAuth();
  if (!permission) return true;
  return userCan(user, permission);
}
