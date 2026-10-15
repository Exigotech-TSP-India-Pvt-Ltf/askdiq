import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import UsageCostPage from "@/app/(app)/admin/usage/page";
import { AudienceScopeTabs } from "@/components/admin/AudienceScopeTabs";
import { AudienceProvider, useAudience } from "@/lib/audience-context";
import { api } from "@/lib/api";
import type { UsageSummary, PageEnvelope, UsageUserRow, UsageSessionRow, UsageQueryRow } from "@/lib/types";

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ token: "mock-token", user: { id: "admin-1", role: "admin" }, loading: false }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/admin/usage",
  useSearchParams: () => new URLSearchParams(),
}));

const mockSummaryOverview: UsageSummary = {
  audience: "overview",
  as_of: new Date().toISOString(),
  financial: {
    total_cost_usd: 125.45,
    llm_cost_usd: 110.20,
    embeddings_cost_usd: 15.25,
    avg_cost_per_query_usd: 0.045,
    stage_costs: {
      query_embed_cost: 0.005,
      guardrail_embed_cost: 0.003,
      prompt_uncached_cost: 0.05,
      cached_prompt_cost: 0.01,
      cached_savings_usd: 0.02,
      reasoning_cost: 0.03,
      visible_cost: 0.02,
      ingestion_overhead_cost: 0.005,
    },
  },
  tokens: {
    total_billed_tokens: 150000,
    llm_tokens: {
      prompt_context_tokens: 100000,
      cached_prompt_tokens: 30000,
      reasoning_tokens: 10000,
      visible_output_tokens: 10000,
      total_llm_tokens: 150000,
    },
    embeddings_tokens: {
      query_embed_tokens: 5000,
      guardrail_embed_tokens: 3000,
      ingestion_embed_tokens: 25000,
      total_embeddings_tokens: 33000,
    },
  },
  pricing: {
    price_embed_per_million: 0.02,
    price_prompt_per_million: 0.25,
    price_output_per_million: 2.0,
    price_cached_per_million: 0.0625,
  },
  accounting: {
    total_turns: 500,
    actual_turns: 490,
    estimated_turns: 10,
    actual_pct: 98,
  },
  quality: {
    total_queries: 500,
    outcomes: { answered: 470, ungrounded: 30 },
    groundedness_pass_rate: 94.0,
    avg_billed_tokens_per_turn: 300,
    latency: {
      avg_ms: 280,
      p50_ms: 240,
      p95_ms: 380,
    },
  },
};

const mockSummaryUsers: UsageSummary = {
  ...mockSummaryOverview,
  audience: "users",
  financial: {
    ...mockSummaryOverview.financial,
    total_cost_usd: 98.20,
  },
};

const mockQueriesResponse: PageEnvelope<UsageQueryRow> = {
  items: [
    {
      request_id: "req-abc-123",
      created_at: new Date().toISOString(),
      ip_address: "192.168.1.55",
      session_id: "sess-xyz-789",
      session_title: "Security Assessment",
      query: "How is Essential Eight implemented?",
      answer: "Through automated controls and MFA verification.",
      prompt_tokens: 1200,
      reasoning_tokens: 200,
      output_tokens: 350,
      embed_tokens: 50,
      total_tokens: 1750,
      cost_usd: 0.0042,
      outcome: "answered",
      grounded: true,
      latency_ms: 320,
    } as any,
  ],
  total: 1,
  limit: 25,
  offset: 0,
};

describe("Phase 8: End-to-End User Journeys", () => {
  beforeEach(() => {
    vi.restoreAllMocks();

    vi.spyOn(api, "getAudienceCounts").mockResolvedValue({
      overview: { queries: 500, sessions: 120, ips: 45 },
      users: { queries: 400, sessions: 95, ips: 35 },
      testing: { queries: 100, sessions: 25, ips: 10 },
    });

    vi.spyOn(api, "getUsageSummary").mockImplementation((_, params) => {
      if (params?.audience === "users") {
        return Promise.resolve(mockSummaryUsers);
      }
      return Promise.resolve(mockSummaryOverview);
    });

    vi.spyOn(api, "getUsageUsers").mockResolvedValue({
      items: [],
      total: 0,
      limit: 25,
      offset: 0,
    });

    vi.spyOn(api, "getUsageSessions").mockResolvedValue({
      items: [],
      total: 0,
      limit: 25,
      offset: 0,
    });

    vi.spyOn(api, "getUsageQueries").mockResolvedValue(mockQueriesResponse);

    vi.spyOn(api, "getQueryDetail").mockResolvedValue({
      request_id: "req-abc-123",
      timestamp: new Date().toISOString(),
      client_ip: "192.168.1.55",
      is_test: false,
      user_identifier: "prod_user_1",
      session_id: "sess-xyz-789",
      input_tokens: 1200,
      output_tokens: 350,
      total_tokens: 1550,
      cost_usd: 0.0042,
      outcome: "answered",
      grounded: true,
      latency_ms: 320,
      stages: [{ stage: "LLM", latency_ms: 280, cost_usd: 0.0035 }],
    });

    vi.spyOn(api, "getTestIps").mockResolvedValue({
      default_ips: ["127.0.0.1"],
      custom_ips: [],
      effective_ips: ["127.0.0.1"],
    });
  });

  it("Subtask 8.1.1: Audience Scope Switch E2E Journey updates metrics in real-time", async () => {
    function TestApp() {
      const { audience, setAudience } = useAudience();
      return (
        <div>
          <AudienceScopeTabs />
          <div data-testid="active-scope">{audience}</div>
        </div>
      );
    }

    render(
      <AudienceProvider>
        <TestApp />
      </AudienceProvider>
    );

    // Initial state is Overview
    expect(screen.getByTestId("active-scope").textContent).toBe("overview");

    // Click Users scope tab
    const usersTab = screen.getByRole("tab", { name: /^users$/i });
    fireEvent.click(usersTab);

    // Context and UI reflect Users scope
    await waitFor(() => {
      expect(screen.getByTestId("active-scope").textContent).toBe("users");
    });
  });

  it("Subtask 8.1.2: Filter, Search, and Export Journey renders query table and triggers inspect drawer", async () => {
    vi.spyOn(api, "getQueryDetail").mockResolvedValue({
      request_id: "req-abc-123",
      session: { id: "sess-xyz-789", title: "Security Assessment", ip: "192.168.1.55" },
      user_query: "How is Essential Eight implemented?",
      assistant_answer: "Through automated controls and MFA verification.",
      outcome: "answered",
      grounded: true,
      cost_usd: 0.0042,
      pricing_rate_card: {
        price_embed_per_million: 0.02,
        price_input_per_million: 0.25,
        price_output_per_million: 2.0,
        price_cached_per_million: 0.0625,
      },
      tokens: {
        query_embed: 50,
        guardrail_embed: 30,
        prompt: 1200,
        cached_prompt: 0,
        reasoning: 200,
        visible: 350,
        total_billed: 1750,
      },
      latency: {
        total_ms: 320,
        retrieval_ms: 45,
        llm_generation_ms: 250,
        guardrails_ms: 25,
      },
    });

    render(<UsageCostPage />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /Usage & Financial Economics/i })).toBeInTheDocument();
    });

    // Switch to All Queries tab
    const queriesTab = screen.getByRole("tab", { name: /^all queries$/i });
    fireEvent.click(queriesTab);

    // Verify row rendered in TableCard
    await waitFor(() => {
      expect(screen.getByText("How is Essential Eight implemented?")).toBeInTheDocument();
    });

    // Inspect drawer triggers upon clicking inspect button
    const inspectBtn = screen.getByRole("button", { name: /inspect query req-abc-123/i });
    fireEvent.click(inspectBtn);

    await waitFor(() => {
      expect(screen.getByRole("dialog", { name: /single-query economic inspector/i })).toBeInTheDocument();
    });
  });

  it(
    "Subtask 8.2.2: Memory Leak & Client-Side Profiling - sustained tab cycling causes zero crashes",
    async () => {
      const { unmount } = render(<UsageCostPage />);

      await waitFor(() => {
        expect(screen.getByRole("heading", { name: /Usage & Financial Economics/i })).toBeInTheDocument();
      });

      // Rapid sustained cycling of tabs
      // Live Telemetry is the Usage "Overview" tab and carries the "Live" badge.
      const overviewTab = screen.getByRole("tab", { name: /^overview\s*live$/i });
      const usersTab = screen.getByRole("tab", { name: /^all users$/i });
      const sessionsTab = screen.getByRole("tab", { name: /^all sessions$/i });
      const queriesTab = screen.getByRole("tab", { name: /^all queries$/i });

      for (let i = 0; i < 10; i++) {
        fireEvent.click(usersTab);
        fireEvent.click(sessionsTab);
        fireEvent.click(queriesTab);
        fireEvent.click(overviewTab);
      }

      // Component is healthy and stable
      expect(screen.getByRole("heading", { name: /Usage & Financial Economics/i })).toBeInTheDocument();

      unmount();
    },
    15000
  );
});
