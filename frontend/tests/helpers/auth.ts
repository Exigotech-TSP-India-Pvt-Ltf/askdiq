import type { AuthMe } from "@/lib/auth-api";
import type { Permission, Role, UserStatus } from "@/lib/permissions";

/** Mirrors backend ROLE_PERMISSIONS (app/auth/permissions.py) for fixtures only. */
const OPERATOR: Permission[] = ["dashboard:read"];
const ADMIN: Permission[] = [
  ...OPERATOR,
  "documents:write",
  "feedback:triage",
  "feedback:export",
  "eval:run",
  "testip:write",
  "usage:export",
];
const SUPERADMIN: Permission[] = [...ADMIN, "users:manage", "audit:auth:read", "branding:write", "taxonomy:write"];

export const ROLE_PERMISSIONS: Record<Role, Permission[]> = {
  operator: OPERATOR,
  admin: ADMIN,
  superadmin: SUPERADMIN,
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
