/**
 * Typed client for User Management (newplan §7.8; planchanges4 F1.4).
 *
 * Contract: `feat/auth-user-management-api` —
 * `backend/app/api/routes/user_management.py`, `schemas/user_management.py`,
 * `routes/auth.py` (`GET /auth/me`) and `routes/governance.py`
 * (`GET /governance/audit-log`, auth events need `audit:auth:read`).
 *
 * Kept out of `lib/api.ts` (shared hotspot); it uses the same `/api/proxy`
 * route and `ApiError`, so error handling (409 toasts, retries) is shared.
 */
import { ApiError } from "./api";
import { isRole, isUserStatus, type Permission, type Role, type UserStatus } from "./permissions";

const API_BASE_URL = "/api/proxy";
const CLIENT_ID_STORAGE_KEY = "deployiq.client_id";
/** CSRF marker the backend requires on POST / PATCH (docs/deployment/docker-and-auth-contract.md, step 6). */
const CSRF_HEADER = { "X-Requested-With": "askdiq" } as const;

// ---------------------------------------------------------------------------
// Enums (mirror app/auth/permissions.py)
// ---------------------------------------------------------------------------

export { ROLES, USER_STATUSES, PERMISSIONS, ROLE_PERMISSIONS, isRole, isUserStatus } from "./permissions";
export type { Role, UserStatus, Permission } from "./permissions";

export const ACCESS_REQUEST_STATUSES = ["pending", "approved", "rejected"] as const;
export type AccessRequestStatus = (typeof ACCESS_REQUEST_STATUSES)[number];
export type AccessRequestView = AccessRequestStatus | "all";
export const ACCESS_REQUEST_VIEWS: readonly AccessRequestView[] = ["pending", "approved", "rejected", "all"];

export { ROLE_LABELS as ROLE_LABEL } from "./permissions";

export const STATUS_LABEL: Readonly<Record<UserStatus, string>> = {
  pending: "Pending",
  active: "Active",
  rejected: "Rejected",
  suspended: "Suspended",
};

export const PERMISSION_LABEL: Readonly<Record<Permission, string>> = {
  "dashboard:read": "View dashboards",
  "documents:write": "Upload and delete documents",
  "feedback:triage": "Triage feedback",
  "feedback:export": "Export feedback",
  "eval:run": "Run evaluations",
  "testip:write": "Manage test IPs",
  "usage:export": "Export usage",
  "users:manage": "Manage users and access",
  "audit:auth:read": "Read the auth audit trail",
  "branding:write": "Edit branding",
  "taxonomy:write": "Edit the taxonomy",
  "models:write": "Change models and pricing",
};

/** `app/auth/audit.py` `AuthEvent`. */
export const AUTH_EVENTS = [
  "login_success",
  "login_denied",
  "access_requested",
  "access_approved",
  "access_rejected",
  "role_changed",
  "user_suspended",
  "user_reactivated",
  "session_revoked",
  "logout",
  "user_invited",
] as const;
export type AuthEvent = (typeof AUTH_EVENTS)[number];

export const AUTH_EVENT_LABEL: Readonly<Record<AuthEvent, string>> = {
  login_success: "Signed in",
  login_denied: "Sign-in denied",
  access_requested: "Access requested",
  access_approved: "Access approved",
  access_rejected: "Access rejected",
  role_changed: "Role changed",
  user_suspended: "Suspended",
  user_reactivated: "Reactivated",
  session_revoked: "Sessions revoked",
  logout: "Signed out",
  user_invited: "Invited",
};

export function isAuthEvent(value: unknown): value is AuthEvent {
  return typeof value === "string" && (AUTH_EVENTS as readonly string[]).includes(value);
}
export function isAccessRequestView(value: unknown): value is AccessRequestView {
  return typeof value === "string" && (ACCESS_REQUEST_VIEWS as readonly string[]).includes(value);
}

// ---------------------------------------------------------------------------
// Limits (mirror the route / schema bounds)
// ---------------------------------------------------------------------------

export const PAGE_MAX = 200;
export const SEARCH_MAX_LENGTH = 100;
export const NOTE_MAX_LENGTH = 1000;
export const BULK_APPROVE_MAX = 100;
export const NAME_MAX_LENGTH = 256;
export const EMAIL_MAX_LENGTH = 320;
export const STATS_DAYS_MAX = 90;
export const AUDIT_LIMIT_MAX = 500;

export const USER_SORT_KEYS = ["created_at", "last_login_at", "email", "name"] as const;
export type UserSortKey = (typeof USER_SORT_KEYS)[number];
export type SortOrder = "asc" | "desc";

/** `SAFE_TEXT_PATTERN`: printable text only (no NUL / control characters). */
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/;
export function isSafeText(value: string): boolean {
  return !CONTROL_CHARS.test(value);
}

/** Pragmatic email check (the API validates with `EmailStr`). */
export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// ---------------------------------------------------------------------------
// Response models
// ---------------------------------------------------------------------------

export interface UserAccount {
  id: string;
  email: string;
  name: string;
  role: Role | null;
  status: UserStatus;
  last_login_at: string | null;
  created_at: string | null;
  approved_by_email: string | null;
  approved_at: string | null;
  /** Send back on PATCH (optimistic concurrency). */
  version: number;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}
export type UserAccountPage = Page<UserAccount>;

export interface AuthSession {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  ip_address: string | null;
  user_agent: string | null;
}

export interface UserAccountDetail extends UserAccount {
  active_sessions: AuthSession[];
}

export interface AccessRequest {
  id: string;
  user_id: string;
  user_email: string;
  user_name: string;
  user_status: UserStatus;
  requested_role: Role | null;
  justification: string | null;
  status: AccessRequestStatus;
  created_at: string;
  decided_at: string | null;
  decided_by_email: string | null;
  decision_note: string | null;
  /** Seconds since the request was made (pending requests only). */
  waiting_seconds: number | null;
}
export type AccessRequestPage = Page<AccessRequest>;

export interface BulkApproveResult {
  approved: number;
  ids: string[];
}

export interface RevokeSessionsResult {
  revoked: number;
}

export interface UserKpis {
  total: number;
  active: number;
  pending: number;
  rejected: number;
  suspended: number;
  open_requests: number;
  logins_24h: number;
}

export type FunnelStageId = "signed_in" | "requested" | "approved" | "active";

export interface UserStats {
  days: number;
  kpis: UserKpis;
  logins_per_day: { date: string; count: number }[];
  funnel: { stage: FunnelStageId; count: number }[];
  /** Active users per role. */
  role_mix: { role: Role; count: number }[];
  time_to_approve: { bucket: string; count: number }[];
}

export interface Me {
  user: { id: string; email: string; name: string };
  role: Role | null;
  status: UserStatus;
  permissions: Permission[];
  version: number;
  access_contact_email: string | null;
}

export interface AuditLogEntry {
  id: string;
  request_id: string;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
  created_at: string;
  ip_address: string | null;
}

/** Bucket order of `APPROVAL_BUCKETS` (the API omits empty buckets). */
export const APPROVAL_BUCKETS = ["<1h", "1-4h", "4-24h", "1-3d", ">3d"] as const;

// ---------------------------------------------------------------------------
// Query params
// ---------------------------------------------------------------------------

export interface UserListParams {
  search?: string;
  role?: Role;
  status?: UserStatus;
  sort?: UserSortKey;
  order?: SortOrder;
  limit: number;
  offset: number;
}

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, Math.trunc(n)));

/** Query string for `GET /admin/users-accounts`, bounded and allow-listed. */
export function usersQuery(params: UserListParams): string {
  const qs = new URLSearchParams();
  const search = params.search?.trim().slice(0, SEARCH_MAX_LENGTH);
  if (search && isSafeText(search)) qs.set("search", search);
  if (params.role && isRole(params.role)) qs.set("role", params.role);
  if (params.status && isUserStatus(params.status)) qs.set("status", params.status);
  if (params.sort && (USER_SORT_KEYS as readonly string[]).includes(params.sort)) qs.set("sort", params.sort);
  if (params.order === "asc" || params.order === "desc") qs.set("order", params.order);
  qs.set("limit", String(clamp(params.limit, 1, PAGE_MAX)));
  qs.set("offset", String(clamp(params.offset, 0, 10_000)));
  return qs.toString();
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

function clientIdHeader(): Record<string, string> {
  if (typeof window === "undefined") return {};
  try {
    const id = window.localStorage.getItem(CLIENT_ID_STORAGE_KEY);
    return id ? { "X-Client-Id": id } : {};
  } catch {
    return {};
  }
}

/** `detail` of a problem+json body (string, or the first 422 validation message). */
function problemDetail(body: unknown): string | null {
  if (!body || typeof body !== "object" || !("detail" in body)) return null;
  const { detail } = body as { detail: unknown };
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first: unknown = detail[0];
    if (first && typeof first === "object" && "msg" in first && typeof first.msg === "string") return first.msg;
  }
  return null;
}

async function send<T>(
  path: string,
  { method = "GET", body, signal }: { method?: "GET" | "POST" | "PATCH"; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    signal,
    headers: {
      "Content-Type": "application/json",
      ...clientIdHeader(),
      ...(method === "GET" ? {} : CSRF_HEADER),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = problemDetail(await res.json()) ?? detail;
    } catch {
      // response had no JSON body
    }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const seg = (id: string) => encodeURIComponent(id);

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export const userManagementApi = {
  me: (signal?: AbortSignal) => send<Me>("/auth/me", { signal }),

  stats: (days: number, signal?: AbortSignal) =>
    send<UserStats>(`/admin/users-accounts/stats?days=${clamp(days, 1, STATS_DAYS_MAX)}`, { signal }),

  listUsers: (params: UserListParams, signal?: AbortSignal) =>
    send<UserAccountPage>(`/admin/users-accounts?${usersQuery(params)}`, { signal }),

  getUser: (id: string, signal?: AbortSignal) => send<UserAccountDetail>(`/admin/users-accounts/${seg(id)}`, { signal }),

  updateUser: (id: string, body: { version: number; role?: Role; status?: "active" | "suspended" }) =>
    send<UserAccount>(`/admin/users-accounts/${seg(id)}`, { method: "PATCH", body }),

  revokeSessions: (id: string) =>
    send<RevokeSessionsResult>(`/admin/users-accounts/${seg(id)}/revoke-sessions`, { method: "POST" }),

  invite: (body: { email: string; role: Role; name?: string }) =>
    send<UserAccount>("/admin/users-accounts/invite", { method: "POST", body }),

  listRequests: (view: AccessRequestView, limit: number, offset: number, signal?: AbortSignal) =>
    send<AccessRequestPage>(
      `/admin/access-requests?status=${view}&limit=${clamp(limit, 1, PAGE_MAX)}&offset=${clamp(offset, 0, 10_000)}`,
      { signal },
    ),

  approve: (id: string, role: Role) =>
    send<AccessRequest>(`/admin/access-requests/${seg(id)}/approve`, { method: "POST", body: { role } }),

  reject: (id: string, note: string) =>
    send<AccessRequest>(`/admin/access-requests/${seg(id)}/reject`, { method: "POST", body: { note } }),

  bulkApprove: (ids: readonly string[], role: Role) =>
    send<BulkApproveResult>("/admin/access-requests/bulk-approve", {
      method: "POST",
      body: { ids: ids.slice(0, BULK_APPROVE_MAX), role },
    }),

  /**
   * Newest auth events (`category=auth` filters on the server, so query and
   * ingest rows cannot crowd them out of `limit`); the client-side filter
   * stays as a guard for older servers that ignore `category`.
   */
  authAudit: async (limit: number, signal?: AbortSignal): Promise<AuditLogEntry[]> => {
    const rows = await send<AuditLogEntry[]>(
      `/governance/audit-log?category=auth&limit=${clamp(limit, 1, AUDIT_LIMIT_MAX)}`,
      { signal },
    );
    return rows.filter((row) => isAuthEvent(row.action));
  },
};

// ---------------------------------------------------------------------------
// Cache keys (lib/query-cache.ts); every key starts with "um/"
// ---------------------------------------------------------------------------

export const UM_PREFIX = "um/";
export const umKeys = {
  me: "auth/me",
  stats: (days: number) => `um/stats?days=${days}`,
  users: (params: UserListParams) => `um/users?${usersQuery(params)}`,
  usersPrefix: "um/users",
  user: (id: string) => `um/user/${id}`,
  userPrefix: "um/user/",
  requests: (view: AccessRequestView, limit: number, offset: number) =>
    `um/requests?status=${view}&limit=${limit}&offset=${offset}`,
  requestsPrefix: "um/requests",
  audit: (limit: number) => `um/audit?limit=${limit}`,
  auditPrefix: "um/audit",
  statsPrefix: "um/stats",
} as const;
