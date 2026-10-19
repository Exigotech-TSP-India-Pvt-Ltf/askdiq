/**
 * Roles, statuses and permissions, mirroring backend `app/auth/permissions.py`
 * (newplan §7.3). The UI only hides what the API would reject; the API stays
 * the boundary. Pure module (no React), safe in tests and on the server.
 */

export const ROLES = ["superadmin", "admin", "operator"] as const;
export type Role = (typeof ROLES)[number];

export const USER_STATUSES = ["pending", "active", "rejected", "suspended"] as const;
export type UserStatus = (typeof USER_STATUSES)[number];

export const PERMISSIONS = [
  "dashboard:read",
  "documents:write",
  "feedback:triage",
  "feedback:export",
  "eval:run",
  "testip:write",
  "usage:export",
  "alerts:manage",
  "users:manage",
  "audit:auth:read",
  "branding:write",
  "taxonomy:write",
  "models:write",
] as const;
export type Permission = (typeof PERMISSIONS)[number];

const OPERATOR_PERMISSIONS: readonly Permission[] = ["dashboard:read"];
const ADMIN_PERMISSIONS: readonly Permission[] = [
  ...OPERATOR_PERMISSIONS,
  "documents:write",
  "feedback:triage",
  "feedback:export",
  "eval:run",
  "testip:write",
  "usage:export",
  "alerts:manage",
];
const SUPERADMIN_PERMISSIONS: readonly Permission[] = [
  ...ADMIN_PERMISSIONS,
  "users:manage",
  "audit:auth:read",
  "branding:write",
  "taxonomy:write",
  "models:write",
];

/**
 * The single frontend copy of backend `ROLE_PERMISSIONS` (the API has no list
 * endpoint; `/auth/me` returns only the caller's set).
 */
export const ROLE_PERMISSIONS: Readonly<Record<Role, readonly Permission[]>> = {
  superadmin: SUPERADMIN_PERMISSIONS,
  admin: ADMIN_PERMISSIONS,
  operator: OPERATOR_PERMISSIONS,
};

export const ROLE_LABELS: Record<Role, string> = {
  superadmin: "Superadmin",
  admin: "Admin",
  operator: "Operator",
};

const ROLE_SET = new Set<string>(ROLES);
const STATUS_SET = new Set<string>(USER_STATUSES);
const PERMISSION_SET = new Set<string>(PERMISSIONS);

export const isRole = (value: unknown): value is Role => typeof value === "string" && ROLE_SET.has(value);
export const isUserStatus = (value: unknown): value is UserStatus =>
  typeof value === "string" && STATUS_SET.has(value);
export const isPermission = (value: unknown): value is Permission =>
  typeof value === "string" && PERMISSION_SET.has(value);

/** Deny by default: `undefined` / unknown permissions never grant anything. */
export function hasPermission(granted: readonly string[] | null | undefined, permission: Permission): boolean {
  return Array.isArray(granted) && granted.includes(permission);
}
