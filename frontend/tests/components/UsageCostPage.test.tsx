import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import UsageCostPage from "@/app/(app)/admin/usage/page";
import { api } from "@/lib/api";
import { RangeProvider, presetRange, rangeUtcDays, toIsoDay } from "@/lib/range-context";
import { currentParams, history as routerHistory } from "../helpers/router-mock";
import type { UsageSummary, PageEnvelope, UsageUserRow, UsageSessionRow, UsageQueryRow } from "@/lib/types";

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ token: "mock-token", user: null, loading: false }),
}));

vi.mock("next/navigation", async () => (await import("../helpers/router-mock")).navigationMock());

vi.mock("@/lib/audience-context", () => ({
  useAudience: () => ({
    audience: "overview",
    setAudience: vi.fn(),
  }),
}));

const mockSummary: UsageSummary = {
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
};

const mockUsersResponse: PageEnvelope<UsageUserRow> = {
  items: [
    {
      ip_address: "192.168.1.100",
      is_test: false,
      type: "Production",
      sessions: 2,
      queries: 8,
      embed_tokens: 600,
      prompt_tokens: 9000,
      reasoning_tokens: 1200,
      output_tokens: 600,
      total_tokens: 10800,
      cost_usd: 0.035,
      last_active: "2026-10-16T01:00:00Z",
    },
    {
      ip_address: "127.0.0.1",
      is_test: true,
      type: "Test IP",
      sessions: 1,
      queries: 2,
      embed_tokens: 200,
      prompt_tokens: 3000,
      reasoning_tokens: 300,
      output_tokens: 200,
      total_tokens: 3500,
      cost_usd: 0.01,
      last_active: null,
    },
  ],
  total: 2,
  limit: 10,
  offset: 0,
  stats: {
    unique_users: 2,
    active_sessions: 3,
    total_queries: 10,
    top_consumer_share_pct: 77.8,
    top_3_users: [
      { ip_address: "192.168.1.100", queries: 8, cost_usd: 0.035, tokens: 10800 },
    ],
  },
};

const mockSessionsResponse: PageEnvelope<UsageSessionRow> = {
  items: [
    {
      id: "sess-123",
      title: "Security posture check",
      ip_address: "192.168.1.100",
      queries: 8,
      embed_tokens: 600,
      prompt_tokens: 9000,
      reasoning_tokens: 1200,
      output_tokens: 600,
      total_tokens: 10800,
      cost_usd: 0.035,
      last_active: new Date().toISOString(),
    },
  ],
  total: 1,
  limit: 10,
  offset: 0,
  stats: {
    total_sessions: 1,
    avg_turns_per_session: 8,
    avg_cost_per_session_usd: 0.035,
    max_session_turns: 8,
    top_3_sessions: [
      {
        id: "sess-123",
        title: "Security posture check",
        ip_address: "192.168.1.100",
        queries: 8,
        cost_usd: 0.035,
        tokens: 10800,
      },
    ],
  },
};

const mockQueriesResponse: PageEnvelope<UsageQueryRow> = {
  items: [
    {
      request_id: "req-999",
      created_at: new Date().toISOString(),
      query: "How is Essential Eight implemented?",
      answer: "Through automated controls and MFA verification.",
      ip_address: "192.168.1.100",
      session_id: "sess-123",
      session_title: "Security posture check",
      prompt_tokens: 1200,
      reasoning_tokens: 150,
      output_tokens: 150,
      embed_tokens: 80,
      cost_usd: 0.0045,
      outcome: "answered",
      grounded: true,
      latency_ms: 280,
    },
  ],
  total: 1,
  limit: 10,
  offset: 0,
  stats: {
    total_queries: 1,
    groundedness_pass_rate: 100,
    groundedness_evaluated_turns: 1,
    groundedness_passed_turns: 1,
    avg_latency_ms: 280,
    avg_tokens_per_query: 1580,
    outcomes: { answered: 1 },
  },
};

const LIVE_TELEMETRY_HEADING = "Chatbot Live Telemetry Command Center";
// The tab's accessible name is its label followed by the "Live" badge.
const OVERVIEW_TAB_NAME = /^overview\s*live$/i;

describe("UsageCostPage Enterprise Intelligence Console", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState(null, "", "/");
    routerHistory.reset("/admin/usage");

    vi.spyOn(api, "getUsageSummary").mockResolvedValue(mockSummary);
    vi.spyOn(api, "getUsageUsers").mockResolvedValue(mockUsersResponse);
    vi.spyOn(api, "getUsageSessions").mockResolvedValue(mockSessionsResponse);
    vi.spyOn(api, "getUsageQueries").mockResolvedValue(mockQueriesResponse);
    vi.spyOn(api, "getTestIps").mockResolvedValue({
      default_ips: ["127.0.0.1"],
      custom_ips: [],
      effective_ips: ["127.0.0.1"],
      classification_note: "Audience Re-classification Guarantee active.",
    });
  });

  it("renders 4 tabs with Live Telemetry as the first 'Overview' tab", async () => {
    render(<UsageCostPage />);

    await waitFor(() => {
      expect(screen.getByText(LIVE_TELEMETRY_HEADING)).toBeInTheDocument();
    });

    const tablist = screen.getByRole("tablist", { name: /usage console navigation/i });
    const tabs = within(tablist).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["OverviewLive", "All Users", "All Sessions", "All Queries"]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("tab", { name: /live telemetry studio/i })).not.toBeInTheDocument();
  });

  it("keeps Test IP configuration out of the Usage toolbar", async () => {
    render(<UsageCostPage />);
    await screen.findByRole("tablist", { name: "Usage console navigation" });
    expect(screen.queryByRole("button", { name: "Test IPs" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Manage Test IPs" })).not.toBeInTheDocument();
  });

  it.each([
    ["live_telemetry", OVERVIEW_TAB_NAME],
    ["overall", OVERVIEW_TAB_NAME],
    ["overview", OVERVIEW_TAB_NAME],
    ["bogus", OVERVIEW_TAB_NAME],
    ["users", /^all users$/i],
  ])("resolves ?tab=%s from an old or current bookmark", async (tab, expected) => {
    window.history.replaceState(null, "", `/admin/usage?tab=${tab}`);
    render(<UsageCostPage />);

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: expected })).toHaveAttribute("aria-selected", "true");
    });
  });

  it("navigates to All Users tab and shows user IP and test IP rows", async () => {
    render(<UsageCostPage />);

    await waitFor(() => {
      expect(screen.getByText(LIVE_TELEMETRY_HEADING)).toBeInTheDocument();
    });

    const usersTab = screen.getByRole("tab", { name: /all users/i });
    fireEvent.click(usersTab);

    await waitFor(() => {
      expect(screen.getAllByText("192.168.1.100")[0]).toBeInTheDocument();
    });

    expect(screen.getByText("127.0.0.1")).toBeInTheDocument();
    expect(screen.getByText("Production")).toBeInTheDocument();
    expect(screen.getByText("Test IP")).toBeInTheDocument();
  });

  it("shows when each IP was last active and sorts the Users table by it", async () => {
    const { container } = render(<UsageCostPage />);
    await waitFor(() => expect(screen.getByText(LIVE_TELEMETRY_HEADING)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: /all users/i }));
    await waitFor(() => expect(screen.getByText("127.0.0.1")).toBeInTheDocument());

    const stamp = container.querySelector('time[datetime="2026-10-16T01:00:00.000Z"]');
    expect(stamp).not.toBeNull();
    expect(stamp?.getAttribute("title")).toMatch(/2026/);

    const header = screen.getByRole("columnheader", { name: /last active/i });
    fireEvent.click(header);
    await waitFor(() =>
      expect(api.getUsageUsers).toHaveBeenLastCalledWith(
        expect.anything(),
        expect.objectContaining({ sort: "last_active_desc" }),
        expect.anything(),
      ),
    );
  });

  it("shows when each query was asked and sorts the Queries table by it", async () => {
    const { container } = render(<UsageCostPage />);
    await waitFor(() => expect(screen.getByText(LIVE_TELEMETRY_HEADING)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: /all queries/i }));
    await waitFor(() => expect(screen.getByText("How is Essential Eight implemented?")).toBeInTheDocument());

    expect(container.querySelector("tbody time[datetime]")?.textContent).toBe("just now");
    const header = screen.getByRole("columnheader", { name: /asked/i });
    expect(header).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(header);
    await waitFor(() =>
      expect(api.getUsageQueries).toHaveBeenLastCalledWith(
        expect.anything(),
        expect.objectContaining({ sort: "created_at_asc" }),
        expect.anything(),
      ),
    );
  });

  it("navigates to All Sessions tab and displays session metrics", async () => {
    render(<UsageCostPage />);

    await waitFor(() => {
      expect(screen.getByText(LIVE_TELEMETRY_HEADING)).toBeInTheDocument();
    });

    const sessionsTab = screen.getByRole("tab", { name: /all sessions/i });
    fireEvent.click(sessionsTab);

    await waitFor(() => {
      expect(screen.getAllByText("Security posture check")[0]).toBeInTheDocument();
    });
  });

  it("navigates to All Queries tab and opens Inspector Drawer on click", async () => {
    vi.spyOn(api, "getQueryDetail").mockResolvedValue({
      request_id: "req-999",
      session: { id: "sess-123", title: "Security posture check", ip: "192.168.1.100" },
      user_query: "How is Essential Eight implemented?",
      assistant_answer: "Through automated controls and MFA verification.",
      outcome: "answered",
      grounded: true,
      cost_usd: 0.0045,
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
        reasoning: 150,
        visible: 150,
        total_billed: 1580,
      },
      latency: {
        total_ms: 280,
        retrieval_ms: 45,
        llm_generation_ms: 210,
        guardrails_ms: 25,
      },
    });

    render(<UsageCostPage />);

    await waitFor(() => {
      expect(screen.getByText(LIVE_TELEMETRY_HEADING)).toBeInTheDocument();
    });

    const queriesTab = screen.getByRole("tab", { name: /all queries/i });
    fireEvent.click(queriesTab);

    await waitFor(() => {
      expect(screen.getByText("How is Essential Eight implemented?")).toBeInTheDocument();
    });

    const inspectBtn = screen.getByRole("button", { name: /inspect query req-999/i });
    fireEvent.click(inspectBtn);

    await waitFor(() => {
      expect(screen.getByRole("dialog", { name: /single-query economic inspector/i })).toBeInTheDocument();
    });

    expect(screen.getByText(/single-query economic inspector/i)).toBeInTheDocument();
    expect(screen.getByText(/stage-by-stage mathematical pricing/i)).toBeInTheDocument();
  });

  // ---- Deep links and filters that used to be dropped (interaction audit) ----

  const lastQueriesCall = () => {
    const calls = vi.mocked(api.getUsageQueries).mock.calls;
    return calls[calls.length - 1]?.[1] ?? {};
  };

  /** The page inside the admin layout's global date range, at `url`. */
  const renderRanged = (url: string) => {
    window.history.replaceState(null, "", url);
    routerHistory.reset(url);
    return render(
      <RangeProvider>
        <UsageCostPage />
      </RangeProvider>,
    );
  };
  const lastCall = (fn: (...args: never[]) => unknown) => {
    const calls = vi.mocked(fn).mock.calls as unknown[][];
    return (calls[calls.length - 1]?.[1] ?? {}) as Record<string, unknown>;
  };

  it("applies a dashboard day link (?tab=queries&from=&to=) as the global range and clears it from the chip", async () => {
    renderRanged("/admin/usage?tab=queries&from=2026-10-14&to=2026-10-14");

    await waitFor(() => expect(lastQueriesCall()).toMatchObject({ from_date: "2026-10-14", to_date: "2026-10-14" }));
    expect(screen.getByRole("tab", { name: /all queries/i })).toHaveAttribute("aria-selected", "true");

    // Clearing the day returns to the default range (one source of truth).
    fireEvent.click(screen.getByRole("button", { name: /remove filter day: 2026-10-14/i }));
    const week = rangeUtcDays(presetRange("7d"));
    await waitFor(() => expect(lastQueriesCall()).toMatchObject({ from_date: week.from, to_date: week.to }));
    expect(currentParams().get("from")).toBeNull();
    expect(currentParams().get("to")).toBeNull();
  });

  it("sends the global range to the summary and every tab's list", async () => {
    renderRanged("/admin/usage?range=30d");
    const days = rangeUtcDays(presetRange("30d"));

    await waitFor(() => expect(lastCall(api.getUsageSummary)).toMatchObject({ from_date: days.from, to_date: days.to }));
    await waitFor(() => expect(lastQueriesCall()).toMatchObject({ from_date: days.from, to_date: days.to }));
    // A preset range is not a removable day filter.
    expect(screen.queryByRole("button", { name: /remove filter day/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /all users/i }));
    await waitFor(() => expect(lastCall(api.getUsageUsers)).toMatchObject({ from_date: days.from, to_date: days.to }));

    fireEvent.click(screen.getByRole("tab", { name: /all sessions/i }));
    await waitFor(() => expect(lastCall(api.getUsageSessions)).toMatchObject({ from_date: days.from, to_date: days.to }));
  });

  it("keeps an old one-sided ?from= link working by turning it into a custom range", async () => {
    renderRanged("/admin/usage?tab=queries&from=2026-10-14");

    const today = toIsoDay(new Date());
    await waitFor(() => expect(currentParams().get("to")).toBe(today));
    expect(currentParams().get("from")).toBe("2026-10-14");
    await waitFor(() => expect(lastQueriesCall()).toMatchObject({ from_date: "2026-10-14", to_date: today }));
  });

  it("an IP filter can be cleared (it no longer snaps back)", async () => {
    window.history.replaceState(null, "", "/admin/usage?tab=queries&ip=192.168.1.100");
    render(<UsageCostPage />);

    await waitFor(() => expect(lastQueriesCall().ip).toBe("192.168.1.100"));
    fireEvent.click(screen.getByRole("button", { name: /remove filter ip: 192.168.1.100/i }));
    await waitFor(() => expect(lastQueriesCall().ip).toBeUndefined());
    expect(window.location.search).not.toContain("ip=");
  });

  it("sorting by latency sends the backend's latency sort", async () => {
    window.history.replaceState(null, "", "/admin/usage?tab=queries");
    render(<UsageCostPage />);
    await screen.findByText("How is Essential Eight implemented?");

    fireEvent.click(screen.getByRole("columnheader", { name: /latency/i }).querySelector("button") ?? screen.getByText("Latency"));
    await waitFor(() => expect(lastQueriesCall().sort).toBe("latency_desc"));
  });
});
