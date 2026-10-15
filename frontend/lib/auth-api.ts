/**
 * Typed client for the `/auth/*` routes and the pending access-request count
 * (backend `app/api/routes/auth.py`, `user_management.py`; DEPLOYMENT.md
 * "frontend contract"). Calls go through `/api/proxy`, which forwards the
 * httpOnly session cookie as `X-Session`: client code never sees it.
 */
import { ApiError } from "@/lib/api";
import {
  isPermission,
  isRole,
  isUserStatus,
  type Permission,
  type Role,
  type UserStatus,
} from "@/lib/permissions";

const PROXY = "/api/proxy";
const HTTP_UNAUTHORIZED = 401;
const HTTP_NO_CONTENT = 204;
export const JUSTIFICATION_MAX_LENGTH = 500;

export interface AuthIdentity {
  id: string;
  email: string;
  name: string;
}

/** `GET /auth/me` (MeRead). */
export interface AuthMe {
  user: AuthIdentity;
  role: Role | null;
  status: UserStatus;
  permissions: Permission[];
  version: number;
  access_contact_email: string | null;
}

export type AccessRequestStatus = "pending" | "approved" | "rejected";

/** `AccessRequestRead`. */
export interface AccessRequest {
  id: string;
  status: AccessRequestStatus;
  requested_role: Role | null;
  justification: string | null;
  created_at: string;
  decided_at: string | null;
  decision_note: string | null;
}

/** `GET /auth/access-request` (AccessRequestStatusRead). */
export interface AccessRequestState {
  user_status: UserStatus;
  role: Role | null;
  request: AccessRequest | null;
  access_contact_email: string | null;
}

export interface AccessRequestCreate {
  requested_role?: Role | null;
  justification?: string | null;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const hasBody = init.body !== undefined;
  const res = await fetch(`${PROXY}${path}`, {
    credentials: "same-origin",
    cache: "no-store",
    ...init,
    headers: {
      Accept: "application/json",
      ...(hasBody ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  if (!res.ok) {
    let detail = res.statusText || "Request failed";
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // no JSON body
    }
    throw new ApiError(detail, res.status);
  }
  if (res.status === HTTP_NO_CONTENT) return undefined as T;
  return (await res.json()) as T;
}

/** Validates the `/auth/me` payload; anything unexpected is rejected rather than trusted. */
export function parseMe(raw: unknown): AuthMe | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const user = r.user as Record<string, unknown> | undefined;
  if (!user || typeof user.id !== "string" || typeof user.email !== "string") return null;
  if (!isUserStatus(r.status)) return null;
  return {
    user: { id: user.id, email: user.email, name: typeof user.name === "string" ? user.name : user.email },
    role: isRole(r.role) ? r.role : null,
    status: r.status,
    permissions: Array.isArray(r.permissions) ? r.permissions.filter(isPermission) : [],
    version: typeof r.version === "number" ? r.version : 0,
    access_contact_email: typeof r.access_contact_email === "string" ? r.access_contact_email : null,
  };
}

/** The signed-in user, or `null` when there is no valid session (401). */
export async function fetchMe(signal?: AbortSignal): Promise<AuthMe | null> {
  try {
    return parseMe(await call<unknown>("/auth/me", { signal }));
  } catch (error) {
    if (error instanceof ApiError && error.status === HTTP_UNAUTHORIZED) return null;
    throw error;
  }
}

/** `POST /auth/logout` (204). The proxy deletes the cookie. */
export function logoutRequest(): Promise<void> {
  return call<void>("/auth/logout", { method: "POST" });
}

export function fetchAccessRequestState(signal?: AbortSignal): Promise<AccessRequestState> {
  return call<AccessRequestState>("/auth/access-request", { signal });
}

export function createAccessRequest(body: AccessRequestCreate): Promise<AccessRequest> {
  return call<AccessRequest>("/auth/access-request", { method: "POST", body: JSON.stringify(body) });
}

/** Open access requests (superadmin, `users:manage`): `total` of the pending queue. */
export async function fetchPendingAccessCount(signal?: AbortSignal): Promise<number> {
  const page = await call<{ total?: unknown }>("/admin/access-requests?status=pending&limit=1", { signal });
  return typeof page.total === "number" ? page.total : 0;
}
