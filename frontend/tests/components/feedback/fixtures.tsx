/**
 * Shared fixtures for the Feedback (PR-24) tests: API payloads, a routed
 * `fetch` mock and an in-memory `next/navigation` history.
 */
import React from "react";
import { vi } from "vitest";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { ToastProvider } from "@/components/ui/ToastProvider";
import { AuthProvider } from "@/lib/auth-context";
import { makeMe } from "../../helpers/auth";
import type { FeedbackAnalytics, FeedbackItem, GoldenCase } from "@/lib/feedback-api";

// ---------------------------------------------------------------------------
// next/navigation
// ---------------------------------------------------------------------------

export const history = (() => {
  const state = { entries: ["/admin/feedback"], index: 0, listeners: new Set<() => void>() };
  const notify = () => state.listeners.forEach((listener) => listener());
  return {
    state,
    current: () => state.entries[state.index],
    params: () => new URLSearchParams(state.entries[state.index].split("?")[1] ?? ""),
    reset(url: string) {
      state.entries = [url];
      state.index = 0;
      notify();
    },
    push: vi.fn((url: string) => {
      state.entries = [...state.entries.slice(0, state.index + 1), url];
      state.index += 1;
      notify();
    }),
    replace: vi.fn((url: string) => {
      state.entries[state.index] = url;
      notify();
    }),
  };
})();

export async function navigationMock() {
  const { useSyncExternalStore } = await import("react");
  const subscribe = (cb: () => void) => {
    history.state.listeners.add(cb);
    return () => {
      history.state.listeners.delete(cb);
    };
  };
  const useUrl = () => useSyncExternalStore(subscribe, history.current);
  return {
    useRouter: () => ({ push: history.push, replace: history.replace }),
    usePathname: () => useUrl().split("?")[0],
    useSearchParams: () => new URLSearchParams(useUrl().split("?")[1] ?? ""),
  };
}

export const authMock = {
  useAuth: () => ({ token: "t", user: { id: "u1", full_name: "Admin", email: "admin@example.com", created_at: "" }, isLoading: false, logout: () => undefined }),
};

// ---------------------------------------------------------------------------
// fetch
// ---------------------------------------------------------------------------

export interface FetchCall {
  method: string;
  path: string;
  body: unknown;
}

export interface Reply {
  status?: number;
  json?: unknown;
  text?: string;
  headers?: Record<string, string>;
}

export type Route = (call: FetchCall) => Reply | undefined;

/** Installs a routed `fetch`; returns the recorded calls. Unrouted calls get 404. */
export function installFetch(route: Route): FetchCall[] {
  const calls: FetchCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.replace(/^\/api\/proxy/, "");
    const method = (init?.method ?? "GET").toUpperCase();
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    const call = { method, path, body };
    calls.push(call);
    const reply = route(call) ?? { status: 404, json: { detail: "Not found" } };
    const status = reply.status ?? 200;
    const payload = reply.text ?? JSON.stringify(reply.json ?? {});
    return new Response(payload, {
      status,
      headers: { "Content-Type": reply.text ? "application/x-ndjson" : "application/json", ...reply.headers },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

// ---------------------------------------------------------------------------
// payloads
// ---------------------------------------------------------------------------

let seq = 0;
export function makeItem(overrides: Partial<FeedbackItem> = {}): FeedbackItem {
  seq += 1;
  const n = String(seq).padStart(4, "0");
  return {
    id: `00000000-0000-4000-8000-00000000${n}`,
    rating: 1,
    comment: "",
    ip_address: "10.0.0.1",
    conversation_id: `11111111-0000-4000-8000-00000000${n}`,
    message_id: `22222222-0000-4000-8000-00000000${n}`,
    created_at: "2026-10-14T10:00:00Z",
    updated_at: null,
    actor: "user@example.com",
    user_query: `Question ${n}`,
    answer: `Answer ${n}`,
    outcome: "answered",
    grounded: true,
    guardrail_flags: [],
    sources: [{ source_name: "policy.md", section_heading: "Scope", score: 0.812, chunk_id: `c-${n}` }],
    confidence: 0.7,
    latency_ms: 900,
    cost_usd: 0.0012,
    reason_tags: [],
    corrected_answer: null,
    triage_status: "new",
    reviewed_by: null,
    reviewed_at: null,
    review_note: null,
    promoted_case_id: null,
    version: 1,
    ...overrides,
  };
}

export function page(items: FeedbackItem[], nextCursor: string | null = null) {
  return { items, total: items.length, limit: 25, offset: 0, next_cursor: nextCursor };
}

export const analyticsFixture: FeedbackAnalytics = {
  total: 42,
  total_feedback: 42,
  positive_count: 20,
  negative_count: 18,
  neutral_count: 4,
  positive_ratio: 47.6,
  negative_ratio: 42.9,
  feedback_rate: 12.5,
  feedback_by_outcome: {
    answered: { positive: 18, negative: 10, total: 30 },
    ungrounded: { positive: 2, negative: 8, total: 12 },
  },
  daily_trend: [
    { day: "2026-10-12", total: 10, positive: 6, negative: 3, avg_rating: 3.4 },
    { day: "2026-10-13", total: 14, positive: 6, negative: 7, avg_rating: 2.9 },
    { day: "2026-10-14", total: 18, positive: 8, negative: 8, avg_rating: 3.1 },
  ],
  rating_distribution: { "1": 15, "2": 3, "3": 4, "4": 5, "5": 15 },
  top_reasons: [
    { key: "wrong", count: 9 },
    { key: "incomplete", count: 4 },
  ],
  triage_funnel: { new: 30, reviewed: 6, promoted: 4, dismissed: 2 },
  disagreement: { positive_grounded: 15, positive_ungrounded: 5, negative_grounded: 6, negative_ungrounded: 12 },
  knowledge_gaps: [
    { query: "How do I rotate keys?", count: 3, avg_rating: 1.3, avg_confidence: 0.21, last_seen: "2026-10-14T09:00:00Z", message_id: "22222222-0000-4000-8000-000000009999" },
    { query: "What is the SLA?", count: 5, avg_rating: 1.0, avg_confidence: 0.18, last_seen: null, message_id: "22222222-0000-4000-8000-000000009998" },
  ],
};

export function makeGolden(overrides: Partial<GoldenCase> = {}): GoldenCase {
  return {
    id: "fb_case_1",
    query: "How do I rotate keys?",
    case_type: "golden",
    expect_grounded: true,
    expected_outcome: "answered",
    source_message_id: "22222222-0000-4000-8000-000000000001",
    feedback_id: null,
    reference_answer: null,
    reason_tags: ["wrong"],
    promoted_by: "admin@example.com",
    promoted_at: "2026-10-13T10:00:00Z",
    last_run_at: null,
    last_run_passed: null,
    last_run_detail: null,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// providers
// ---------------------------------------------------------------------------

export type Role = "operator" | "admin" | "superadmin";

export function Providers({ role = "admin", children }: { role?: Role; children: React.ReactNode }) {
  return (
    <AuthProvider key={role} initialMe={makeMe(role)}>
      <MotionProvider>
        <ToastProvider>{children}</ToastProvider>
      </MotionProvider>
    </AuthProvider>
  );
}
