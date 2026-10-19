/**
 * Mocked responses that follow docs/technical/frontend/contracts/dashboard-aggregations.md.
 * `LEGACY_DASHBOARD` is the shape the backend returns today (no new fields).
 */
import type { ActivityFeed, DashboardData } from "@/lib/dashboard-api";

export const LEGACY_DASHBOARD: DashboardData = {
  users: 150,
  active_users: 42,
  sessions: 320,
  conversations: 320,
  queries: 1200,
  feedback: 85,
  avg_latency_ms: 2400,
  error_rate: 0.5,
  pipeline: { embedding_ms: 110, retrieval_ms: 180, reranker_enabled: true, reranker_ms: 140, llm_ms: 1900 },
  governance: { monitoring: "1,200 requests since start" },
  query_trend: [
    { date: "2026-10-10", queries: 100 },
    { date: "2026-10-11", queries: 120 },
    { date: "2026-10-12", queries: 90 },
    { date: "2026-10-13", queries: 150 },
    { date: "2026-10-14", queries: 170 },
    { date: "2026-10-15", queries: 130 },
    { date: "2026-10-16", queries: 140 },
  ],
  latency_breakdown: [{ stage: "LLM", ms: 1900 }],
};

const days = ["2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"];
const series = (values: number[]) => days.map((t, i) => ({ t, value: values[i] }));

export const FULL_DASHBOARD: DashboardData = {
  ...LEGACY_DASHBOARD,
  cost_usd: 41.5,
  window: {
    from: "2026-10-09T13:00:00.000Z",
    to: "2026-10-16T13:00:00.000Z",
    bucket: "day",
    previous_from: "2026-10-02T13:00:00.000Z",
    previous_to: "2026-10-09T13:00:00.000Z",
  },
  sparklines: {
    users: series([10, 12, 9, 14, 15, 13, 16]),
    queries: series([100, 120, 90, 150, 170, 130, 140]),
    feedback: series([3, 5, 2, 6, 4, 7, 5]),
    error_rate: series([0.2, 0.4, 0.1, 0.6, 0.5, 0.3, 0.5]),
    cost_usd: series([5, 6, 4, 7, 8, 5.5, 6]),
  },
  deltas: { users: 0.12, queries: 0.08, error_rate: 0.5, cost_usd: -0.1, feedback: null },
  queries_by_outcome: days.map((t, i) => ({
    t,
    answered: 80 + i,
    ungrounded: 5,
    blocked: 2,
    conversational: 10,
    llm_failed: i % 2,
    clarify: 1,
  })),
  latency_series: days.map((t, i) => ({ t, p50_ms: 1800 + i * 10, p95_ms: 3900 + i * 20, count: 100 + i })),
  cost: {
    currency: "USD",
    month: "2026-10",
    mtd_usd: 96.4,
    forecast_month_end_usd: 186.8,
    budget_usd: 250,
    days_elapsed: 16,
    days_in_month: 31,
  },
  satisfaction: { thumbs_up: 68, thumbs_down: 17, ratio: 0.8 },
  top_intents: [
    { intent: "mfa_reset", label: "MFA reset", count: 120, share: 0.3 },
    { intent: "essential_eight", label: "Essential Eight", count: 90, share: 0.225 },
    { intent: "zero_trust", label: "Zero Trust", count: 60, share: 0.15 },
  ],
};

export const ACTIVITY: ActivityFeed = {
  generated_at: "2026-10-16T13:00:00Z",
  items: [
    {
      id: "e1",
      type: "query",
      at: "2026-10-16T12:59:00Z",
      summary: "Query answered in 2.1 s",
      request_id: "req-123",
      conversation_id: "conv-9",
      message_id: null,
      document_id: null,
    },
    {
      id: "e2",
      type: "feedback",
      at: "2026-10-16T12:58:00Z",
      summary: "Thumbs down on an answer",
      request_id: "req-122",
      conversation_id: "conv-8",
      message_id: "msg-7",
      document_id: null,
    },
  ],
};
