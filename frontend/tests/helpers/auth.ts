import type { AuthMe } from "@/lib/auth-api";
import { ROLE_PERMISSIONS as CANONICAL_ROLE_PERMISSIONS, type Permission, type Role, type UserStatus } from "@/lib/permissions";

/** Fixture copy of the canonical role table (mutable arrays for spreading). */
export const ROLE_PERMISSIONS: Record<Role, Permission[]> = {
  operator: [...CANONICAL_ROLE_PERMISSIONS.operator],
  admin: [...CANONICAL_ROLE_PERMISSIONS.admin],
  superadmin: [...CANONICAL_ROLE_PERMISSIONS.superadmin],
};

export function makeMe(
  role: Role | null,
  status: UserStatus = role ? "active" : "pending",
  overrides: Partial<AuthMe> = {},
): AuthMe {
  return {
    user: { id: "u-1", email: "jane@contoso.example", name: "Jane Doe" },
    role,
    status,
    permissions: role && status === "active" ? [...ROLE_PERMISSIONS[role]] : [],
    version: 1,
    access_contact_email: "it-admin@contoso.example",
    ...overrides,
  };
}

/** A `useAuth()` value for `vi.mock("@/lib/auth-context")` factories. */
export function authValue(me: AuthMe | null, extra: Record<string, unknown> = {}) {
  return {
    token: me ? "session" : null,
    user: me ? { id: me.user.id, full_name: me.user.name, email: me.user.email, created_at: "" } : null,
    isLoading: false,
    logout: async () => undefined,
    me,
    role: me?.role ?? null,
    status: me?.status ?? null,
    permissions: me?.permissions ?? [],
    accessContactEmail: me?.access_contact_email ?? null,
    error: undefined,
    refresh: async () => me,
    ...extra,
  };
}
