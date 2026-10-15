import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";

import AdminDashboard from "@/app/(app)/admin/page";
import UsersPage from "@/app/(app)/admin/users/page";
import ConversationsPage from "@/app/(app)/admin/conversations/page";
import FeedbackPage from "@/app/(app)/admin/feedback/page";
import EvaluationPage from "@/app/(app)/evaluation/page";
import GovernancePage from "@/app/(app)/admin/governance/page";
import DocumentsPage from "@/app/(app)/documents/page";
import UsageCostPage from "@/app/(app)/admin/usage/page";

import { api } from "@/lib/api";

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ token: "mock-token", user: { id: "admin-1", role: "admin" }, loading: false }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/admin",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/audience-context", () => ({
  useAudience: () => ({
    audience: "overview",
    setAudience: vi.fn(),
  }),
}));

describe("Phase 7: All Dashboard Pages Migration Verification", () => {
  beforeEach(() => {
    vi.restoreAllMocks();

    vi.spyOn(api, "adminGet").mockImplementation((token: string, path: string) => {
      if (path.startsWith("/dashboard")) {
        return Promise.resolve({
          users: 150,
          active_users: 42,
          sessions: 320,
          conversations: 290,
          queries: 1200,
          feedback: 85,
          avg_latency_ms: 240,
          error_rate: 0.12,
          query_trend: [{ date: "2026-10-14T00:00:00Z", queries: 100 }],
          latency_breakdown: [{ stage: "LLM", ms: 180 }],
          governance: { monitoring: "Healthy", tracing: "Active", audit_logs: "100%", guardrails: "0" },
        });
      }
      if (path.startsWith("/evaluation/summary")) {
        return Promise.resolve({
          overall_pass_rate: 0.94,
          total_cases: 150,
          scored_cases: 150,
          retrieval_recall: 0.92,
          groundedness_accuracy: 0.96,
          followup_accuracy: 0.93,
          radar_scores: [
            { metric: "Retrieval", score: 0.92 },
            { metric: "Answer Quality", score: 0.95 },
          ],
          case_type_pass_rates: [{ type: "Q&A", pass_rate: 0.94 }],
        });
      }
      if (path.startsWith("/evaluation/cases")) {
        return Promise.resolve({
          items: [
            { id: "tc-1", query: "What is essential eight?", category: "Q&A", passed: true, score: 0.95 }
          ],
          total: 1,
        });
      }
      if (path.startsWith("/governance/monitoring")) {
        return Promise.resolve({
          error_rate: 0.05,
          llm_latency_ms: 250,
          p95_latency_ms: 320,
          requests_per_min: 60,
        });
      }
      if (path.startsWith("/governance/traces")) {
        return Promise.resolve([
          { id: "tr-1", status: "ok", latency_ms: 120, stage: "retrieval", timestamp: "2026-10-14T00:00:00Z" }
        ]);
      }
      if (path.startsWith("/governance/logs")) {
        return Promise.resolve({
          items: [
            { id: "l-1", level: "info", message: "Query processed successfully", timestamp: "2026-10-14T00:00:00Z" }
          ],
          total: 1,
        });
      }
      return Promise.resolve({ items: [], total: 0 });
    });

    vi.spyOn(api, "getUsersList").mockResolvedValue({
      items: [
        { id: "u-1", email: "user@example.com", client_ip: "192.168.1.10", status: "active", queries_count: 12, created_at: "2026-10-14T00:00:00Z" }
      ],
      total: 1,
      page: 1,
      limit: 25,
      total_pages: 1,
    } as any);

    vi.spyOn(api, "getConversationsList").mockResolvedValue({
      items: [
        { id: "s-1", title: "Cybersecurity Framework Review", created_ip: "192.168.1.10", message_count: 5, created_at: "2026-10-14T00:00:00Z", last_activity_at: "2026-10-14T00:05:00Z" }
      ],
      total: 1,
      page: 1,
      limit: 25,
      total_pages: 1,
    } as any);

    vi.spyOn(api, "getFeedbackList").mockResolvedValue({
      items: [
        { id: "f-1", rating: 5, comment: "Excellent answer!", session_id: "s-1", created_at: "2026-10-14T00:00:00Z" }
      ],
      total: 1,
      page: 1,
      limit: 25,
      total_pages: 1,
    } as any);

    vi.spyOn(api, "listDocuments").mockResolvedValue([
      {
        document_id: "doc-1",
        source_name: "policy.md",
        source_type: "markdown",
        chunk_count: 10,
        created_at: new Date().toISOString(),
        content_hash: "hash123",
        quarantined: false,
        warnings: [],
      },
    ]);

    vi.spyOn(api, "evaluationReport").mockResolvedValue({
      overall: { total_cases: 10, passed_cases: 9, failed_cases: 1, pass_rate: 0.9 },
      retrieval: { recall: 0.92, precision: 0.88, hit_rate: 0.95 },
      citation: { precision: 0.9, recall: 0.85, coverage: 0.9 },
      answer_quality: { correctness: 0.94, helpfulness: 0.96 },
      abstention: { correct_abstention_rate: 0.98 },
      safety: { safety_score: 0.99 },
      robustness: { robustness_score: 0.93 },
      by_case_type: { "Q&A": { total: 10, passed: 9, pass_rate: 0.9 } },
      cases: [
        { id: "tc-1", query: "What is essential eight?", type: "Q&A", passed: true, score: 0.95 }
      ],
    } as any);

    vi.spyOn(api, "getAudienceCounts").mockResolvedValue({
      overview: { queries: 100, sessions: 20, ips: 5 },
      users: { queries: 80, sessions: 16, ips: 4 },
      testing: { queries: 20, sessions: 4, ips: 1 },
    });

    vi.spyOn(api, "getUsageSummary").mockResolvedValue({
      audience: "overview",
      as_of: new Date().toISOString(),
      financial: {
        total_cost_usd: 0.045,
        llm_cost_usd: 0.0076,
        embeddings_cost_usd: 0.000106,
        avg_cost_per_query_usd: 0.0045,
        stage_costs: {
          query_embed_cost: 0.00001,
          guardrail_embed_cost: 0.000006,
          prompt_uncached_cost: 0.003,
          cached_prompt_cost: 0.0005,
          cached_savings_usd: 0.0015,
          reasoning_cost: 0.003,
          visible_cost: 0.0016,
          ingestion_overhead_cost: 0.00009,
        },
      },
      tokens: {
        total_billed_tokens: 15100,
        llm_tokens: {
          prompt_context_tokens: 12000,
          cached_prompt_tokens: 4000,
          reasoning_tokens: 1500,
          visible_output_tokens: 800,
          total_llm_tokens: 14300,
        },
        embeddings_tokens: {
          query_embed_tokens: 500,
          guardrail_embed_tokens: 300,
          ingestion_embed_tokens: 4500,
          total_embeddings_tokens: 5300,
        },
      },
      pricing: {
        price_embed_per_million: 0.02,
        price_prompt_per_million: 0.25,
        price_output_per_million: 2.0,
        price_cached_per_million: 0.0625,
      },
      accounting: {
        total_turns: 10,
        actual_turns: 10,
        estimated_turns: 0,
        actual_pct: 100,
      },
      quality: {
        total_queries: 10,
        outcomes: { answered: 8, ungrounded: 2 },
        groundedness_pass_rate: 80.0,
        avg_billed_tokens_per_turn: 1510,
        latency: {
          avg_ms: 320,
          p50_ms: 280,
          p95_ms: 450,
        },
      },
    });

    vi.spyOn(api, "getUsageUsers").mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      limit: 25,
      total_pages: 1,
    } as any);

    vi.spyOn(api, "getUsageSessions").mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      limit: 25,
      total_pages: 1,
    } as any);

    vi.spyOn(api, "getUsageQueries").mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      limit: 25,
      total_pages: 1,
    } as any);

    vi.spyOn(api, "getTestIps").mockResolvedValue({
      default_ips: ["127.0.0.1"],
      custom_ips: [],
      effective_ips: ["127.0.0.1"],
    });
  });

  it("renders Admin Dashboard with DashboardHeader and metric cards", async () => {
    // The dashboard reads through lib/dashboard-api (fetch + query cache), not api.adminGet.
    const payload = await api.adminGet("mock-token", "/dashboard");
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith("/api/proxy/admin/dashboard?") || url === "/api/proxy/admin/dashboard") {
        return new Response(JSON.stringify(payload), { status: 200 });
      }
      return new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
    });
    render(<AdminDashboard />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    });
    expect(await screen.findByText("Total Users")).toBeInTheDocument();
  });

  it("renders Admin Users Page with DashboardHeader and TableCard", async () => {
    render(<UsersPage />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /Users/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /filters/i })).toBeInTheDocument();
  });

  it("renders Conversations Page with DashboardHeader and TableCard", async () => {
    render(<ConversationsPage />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /Conversations/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /filters/i })).toBeInTheDocument();
  });

  it("renders Feedback Page with DashboardHeader and TableCard", async () => {
    render(<FeedbackPage />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /Feedback/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /filters/i })).toBeInTheDocument();
  });

  it("renders Evaluation Page with DashboardHeader and TabGroup", async () => {
    render(<EvaluationPage />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /RAG Evaluation/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("tab", { name: /^retrieval$/i })).toBeInTheDocument();
  });

  it("renders Governance Page with DashboardHeader and TabGroup", async () => {
    render(<GovernancePage />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /Admin Governance/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("tab", { name: /^monitoring$/i })).toBeInTheDocument();
  });

  it("renders Documents Page with top hero cards and TableCard", async () => {
    render(<DocumentsPage />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /Knowledge Base Documents/i })).toBeInTheDocument();
    });
    expect(screen.getByText("Total Canonical Documents")).toBeInTheDocument();
  });

  it("renders Usage & Cost Page with DashboardHeader and TabGroup", async () => {
    render(<UsageCostPage />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /Usage & Financial Economics/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("tab", { name: /^all users$/i })).toBeInTheDocument();
  });
});
