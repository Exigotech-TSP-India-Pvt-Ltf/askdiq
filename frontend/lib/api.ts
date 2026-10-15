import type {
  DocumentSummary,
  LatencyReport,
  QueryResponse,
  AuditLogEntry,
  ChatSessionSummary,
  ChatSessionDetail,
  IngestResult,
  Audience,
  PageEnvelope,
  AudienceCounts,
  UsageSummary,
  UsageUserRow,
  UsageSessionRow,
  UsageQueryRow,
  QueryInspectorDetail,
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
  const { token, headers, body, ...rest } = options;
  const isFormData = typeof FormData !== "undefined" && body instanceof FormData;

  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
    body,
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      "X-Client-Id": getClientId(),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const respBody = await res.json();
      detail = respBody.detail ?? detail;
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

  documentChunks: (token: string, id: string) =>
    request<
      {
        id: string;
        chunk_index: number;
        content: string;
        strategy: string;
        section_heading: string | null;
        char_count: number;
      }[]
    >(`/documents/${id}/chunks`, { token }),

  deleteDocument: (token: string, id: string) =>
    request<void>(`/documents/${id}`, { method: "DELETE", token }),

  latencyReport: (token: string) =>
    request<LatencyReport>("/metrics/latency", { token }),

  adminGet: <T = any>(token: string, path: string) =>
    request<T>(`/admin${path}`, { token }),

  adminPost: <T = any>(token: string, path: string, body?: any) =>
    request<T>(`/admin${path}`, {
      method: "POST",
      token,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),

  adminDelete: <T = any>(token: string, path: string) =>
    request<T>(`/admin${path}`, {
      method: "DELETE",
      token,
    }),

  evaluationReport: (token: string, audience?: Audience, signal?: AbortSignal) => {
    const qs = audience && audience !== "overview" ? `?audience=${audience}` : "";
    return request<Record<string, any>>(`/metrics/evaluation${qs}`, { token, signal });
  },

  evaluationReportLive: (token: string, audience?: Audience, compare?: boolean, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    if (audience) params.set("audience", audience);
    if (compare) params.set("compare", "true");
    const qs = params.toString() ? `?${params.toString()}` : "";
    return request<Record<string, any>>(`/metrics/evaluation/live${qs}`, { token, signal });
  },

  evaluationRecordDetail: (token: string, recordId: string, signal?: AbortSignal) => {
    return request<Record<string, any>>(`/metrics/evaluation/record/${recordId}`, { token, signal });
  },


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
    request<{
      document_id: string;
      chunk_count: number;
      strategy_used: string;
      status: string;
      chunks_added: number;
      chunks_kept: number;
      chunks_removed: number;
      quality?: Record<string, any> | null;
    }>("/ingest", {
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

  submitFeedback: (
    token: string,
    sessionId: string,
    messageId: string,
    rating: number,
    comment?: string
  ) =>
    request<{
      id: string;
      message_id: string;
      actor: string;
      rating: number;
      comment: string | null;
      created_at: string;
      updated_at: string;
    }>(`/sessions/${sessionId}/messages/${messageId}/feedback`, {
      method: "POST",
      token,
      body: JSON.stringify({ rating, comment: comment ?? null }),
    }),

  ingestFile: (
    token: string,
    file: File,
    options?: { dryRun?: boolean; force?: boolean }
  ) => {
    const formData = new FormData();
    formData.append("file", file);
    const qs = new URLSearchParams();
    if (options?.dryRun) qs.set("dry_run", "true");
    if (options?.force) qs.set("force", "true");
    const queryStr = qs.toString();
    return request<IngestResult>(`/ingest/file${queryStr ? `?${queryStr}` : ""}`, {
      method: "POST",
      token,
      body: formData,
    });
  },

  getAudienceCounts: (token: string, signal?: AbortSignal) =>
    request<AudienceCounts>("/admin/usage/audience-counts", { token, signal }),

  getUsageSummary: (
    token: string,
    params: { audience?: Audience; from_date?: string; to_date?: string } = {},
    signal?: AbortSignal
  ) => {
    const qs = new URLSearchParams();
    if (params.audience) qs.set("audience", params.audience);
    if (params.from_date) qs.set("from_date", params.from_date);
    if (params.to_date) qs.set("to_date", params.to_date);
    const queryStr = qs.toString();
    return request<UsageSummary>(`/admin/usage/summary${queryStr ? `?${queryStr}` : ""}`, {
      token,
      signal,
    });
  },

  getUsageUsers: (
    token: string,
    params: {
      audience?: Audience;
      q?: string;
      type?: string;
      sort?: string;
      limit?: number;
      offset?: number;
    } = {},
    signal?: AbortSignal
  ) => {
    const qs = new URLSearchParams();
    if (params.audience) qs.set("audience", params.audience);
    if (params.q) qs.set("q", params.q);
    if (params.type) qs.set("type", params.type);
    if (params.sort) qs.set("sort", params.sort);
    if (params.limit !== undefined) qs.set("limit", String(params.limit));
    if (params.offset !== undefined) qs.set("offset", String(params.offset));
    const queryStr = qs.toString();
    return request<PageEnvelope<UsageUserRow>>(
      `/admin/usage/users${queryStr ? `?${queryStr}` : ""}`,
      { token, signal }
    );
  },

  getUsageSessions: (
    token: string,
    params: {
      audience?: Audience;
      q?: string;
      ip?: string;
      sort?: string;
      limit?: number;
      offset?: number;
    } = {},
    signal?: AbortSignal
  ) => {
    const qs = new URLSearchParams();
    if (params.audience) qs.set("audience", params.audience);
    if (params.q) qs.set("q", params.q);
    if (params.ip) qs.set("ip", params.ip);
    if (params.sort) qs.set("sort", params.sort);
    if (params.limit !== undefined) qs.set("limit", String(params.limit));
    if (params.offset !== undefined) qs.set("offset", String(params.offset));
    const queryStr = qs.toString();
    return request<PageEnvelope<UsageSessionRow>>(
      `/admin/usage/sessions${queryStr ? `?${queryStr}` : ""}`,
      { token, signal }
    );
  },

  getUsageQueries: (
    token: string,
    params: {
      audience?: Audience;
      q?: string;
      ip?: string;
      session_id?: string;
      outcome?: string;
      grounded?: boolean;
      sort?: string;
      limit?: number;
      offset?: number;
    } = {},
    signal?: AbortSignal
  ) => {
    const qs = new URLSearchParams();
    if (params.audience) qs.set("audience", params.audience);
    if (params.q) qs.set("q", params.q);
    if (params.ip) qs.set("ip", params.ip);
    if (params.session_id) qs.set("session_id", params.session_id);
    if (params.outcome) qs.set("outcome", params.outcome);
    if (params.grounded !== undefined) qs.set("grounded", String(params.grounded));
    if (params.sort) qs.set("sort", params.sort);
    if (params.limit !== undefined) qs.set("limit", String(params.limit));
    if (params.offset !== undefined) qs.set("offset", String(params.offset));
    const queryStr = qs.toString();
    return request<PageEnvelope<UsageQueryRow>>(
      `/admin/usage/queries${queryStr ? `?${queryStr}` : ""}`,
      { token, signal }
    );
  },

  getBranding: () =>
    request<{
      brand_name: string;
      brand_assistant_name: string;
      brand_support_email: string;
    }>("/branding"),

  getQueryDetail: (token: string, requestId: string, signal?: AbortSignal) =>
    request<any>(`/admin/usage/queries/${requestId}`, { token, signal }),

  getTestIps: (token: string) =>
    request<{
      default_ips: string[];
      custom_ips: Array<{
        id: string;
        ip_address: string;
        description: string | null;
        is_active: boolean;
        created_at: string | null;
      }>;
      effective_ips: string[];
      classification_note?: string;
    }>("/admin/usage/test-ips", { token }),

  addTestIp: (token: string, ipAddress: string, description?: string) =>
    request<{
      id: string;
      ip_address: string;
      description: string | null;
      is_active: boolean;
      created_at: string | null;
    }>("/admin/usage/test-ips", {
      method: "POST",
      token,
      body: JSON.stringify({ ip_address: ipAddress, description: description ?? null }),
    }),

  deleteTestIp: (token: string, ipAddress: string) =>
    request<{ deleted: boolean; ip_address: string }>(
      `/admin/usage/test-ips/${encodeURIComponent(ipAddress)}`,
      { method: "DELETE", token }
    ),

  getUsersList: (
    token: string,
    params: {
      audience?: Audience;
      q?: string;
      status?: string;
      type?: string;
      sort?: string;
      limit?: number;
      offset?: number;
    } = {},
    signal?: AbortSignal
  ) => {
    const qs = new URLSearchParams();
    if (params.audience) qs.set("audience", params.audience);
    if (params.q) qs.set("q", params.q);
    if (params.status && params.status !== "all") qs.set("status", params.status);
    if (params.type && params.type !== "all") qs.set("type", params.type);
    if (params.sort) qs.set("sort", params.sort);
    if (params.limit !== undefined) qs.set("limit", String(params.limit));
    if (params.offset !== undefined) qs.set("offset", String(params.offset));
    const queryStr = qs.toString();
    return request<PageEnvelope<any>>(`/admin/users${queryStr ? `?${queryStr}` : ""}`, {
      token,
      signal,
    });
  },

  getConversationsList: (
    token: string,
    params: {
      audience?: Audience;
      q?: string;
      ip?: string;
      sort?: string;
      limit?: number;
      offset?: number;
    } = {},
    signal?: AbortSignal
  ) => {
    const qs = new URLSearchParams();
    if (params.audience) qs.set("audience", params.audience);
    if (params.q) qs.set("q", params.q);
    if (params.ip) qs.set("ip", params.ip);
    if (params.sort) qs.set("sort", params.sort);
    if (params.limit !== undefined) qs.set("limit", String(params.limit));
    if (params.offset !== undefined) qs.set("offset", String(params.offset));
    const queryStr = qs.toString();
    return request<PageEnvelope<any>>(`/admin/conversations${queryStr ? `?${queryStr}` : ""}`, {
      token,
      signal,
    });
  },

  getFeedbackList: (
    token: string,
    params: {
      audience?: Audience;
      q?: string;
      rating?: number;
      sort?: string;
      limit?: number;
      offset?: number;
    } = {},
    signal?: AbortSignal
  ) => {
    const qs = new URLSearchParams();
    if (params.audience) qs.set("audience", params.audience);
    if (params.q) qs.set("q", params.q);
    if (params.rating !== undefined) qs.set("rating", String(params.rating));
    if (params.sort) qs.set("sort", params.sort);
    if (params.limit !== undefined) qs.set("limit", String(params.limit));
    if (params.offset !== undefined) qs.set("offset", String(params.offset));
    const queryStr = qs.toString();
    return request<PageEnvelope<any>>(`/admin/feedback${queryStr ? `?${queryStr}` : ""}`, {
      token,
      signal,
    });
  },

  getFeedbackAnalytics: (
    token: string,
    audience?: Audience,
    signal?: AbortSignal
  ) => {
    const qs = audience ? `?audience=${audience}` : "";
    return request<{
      total_feedback: number;
      positive_count: number;
      negative_count: number;
      neutral_count: number;
      positive_ratio: number;
      negative_ratio: number;
      feedback_rate: number;
      feedback_by_outcome: Record<string, { positive: number; negative: number; total: number }>;
    }>(`/admin/feedback/analytics${qs}`, { token, signal });
  },

  promoteFeedbackToTest: (token: string, messageId: string) => {
    return request<{
      status: string;
      case_id: string;
      query: string;
    }>(`/admin/feedback/${messageId}/promote-to-test`, {
      method: "POST",
      token,
    });
  },
};
