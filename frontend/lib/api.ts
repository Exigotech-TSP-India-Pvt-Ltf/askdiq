import type {
  DocumentSummary,
  LatencyReport,
  QueryResponse,
  AuditLogEntry,
  ChatSessionSummary,
  ChatSessionDetail,
} from "./types";

// Requests go through our own /api/proxy route so the real backend API key
// stays server-side and is never shipped in the browser bundle.
const API_BASE_URL = "/api/proxy";

const CLIENT_ID_STORAGE_KEY = "deployiq.client_id";

// No accounts exist, so this is what scopes each browser to its own chat
// sessions instead of every visitor sharing one identity server-side.
// Persisted in localStorage so it survives reloads but not a fresh browser.
function getClientId(): string {
  if (typeof window === "undefined") return "server";
  let id = window.localStorage.getItem(CLIENT_ID_STORAGE_KEY);
  if (!id) {
    id = crypto.randomUUID();
    window.localStorage.setItem(CLIENT_ID_STORAGE_KEY, id);
  }
  return id;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  options: RequestInit & { token?: string | null } = {}
): Promise<T> {
  const { token, headers, ...rest } = options;

  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
    headers: {
      "Content-Type": "application/json",
      "X-Client-Id": getClientId(),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // response had no JSON body
    }
    throw new ApiError(detail, res.status);
  }

  // /health-style endpoints can return 204s in the future; guard for that.
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  query: (
    token: string,
    body: {
      query: string;
      filters?: Record<string, string>;
      session_id?: string | null;
    }
  ) =>
    request<QueryResponse>("/query", {
      method: "POST",
      token,
      body: JSON.stringify(body),
    }),

  listSessions: (token: string) =>
    request<ChatSessionSummary[]>("/sessions", { token }),

  createSession: (token: string, title?: string) =>
    request<ChatSessionSummary>("/sessions", {
      method: "POST",
      token,
      body: JSON.stringify({ title: title ?? null }),
    }),

  getSession: (token: string, sessionId: string) =>
    request<ChatSessionDetail>(`/sessions/${sessionId}`, { token }),

  renameSession: (token: string, sessionId: string, title: string) =>
    request<ChatSessionSummary>(`/sessions/${sessionId}`, {
      method: "PATCH",
      token,
      body: JSON.stringify({ title }),
    }),

  deleteSession: (token: string, sessionId: string) =>
    request<void>(`/sessions/${sessionId}`, { method: "DELETE", token }),

  deleteExchange: (token: string, sessionId: string, messageId: string) =>
    request<void>(`/sessions/${sessionId}/messages/${messageId}`, {
      method: "DELETE",
      token,
    }),

  listDocuments: (token: string) =>
    request<DocumentSummary[]>("/documents", { token }),

  latencyReport: (token: string) =>
    request<LatencyReport>("/metrics/latency", { token }),

  ingest: (
    token: string,
    body: {
      source_name: string;
      source_type: string;
      content: string;
      chunking_strategy?: string;
      metadata?: Record<string, unknown>;
    }
  ) =>
    request("/ingest", {
      method: "POST",
      token,
      body: JSON.stringify({ chunking_strategy: "auto", metadata: {}, ...body }),
    }),

  auditLog: (token: string, params: { limit?: number; action?: string } = {}) => {
    const search = new URLSearchParams();
    if (params.limit) search.set("limit", String(params.limit));
    if (params.action) search.set("action", params.action);
    const qs = search.toString();
    return request<AuditLogEntry[]>(`/governance/audit-log${qs ? `?${qs}` : ""}`, {
      token,
    });
  },
};
