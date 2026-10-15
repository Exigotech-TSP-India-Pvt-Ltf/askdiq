/**
 * planchanges4 F3.5: Usage & Cost chart additions, Evaluation (Live) quality
 * radar and pass-rate trend, and the Chat brand header, user chip and
 * admin-only "View trace" link.
 */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { CostInsights } from "@/components/admin/usage/CostInsights";
import { PassRateTrendCard, QualityRadarCard } from "@/components/admin/evaluation/QualityCharts";
import { ChatHeader } from "@/components/chat/ChatHeader";
import { TraceLink } from "@/components/chat/TraceLink";
import { PermissionsProvider, ROLE_PERMISSIONS } from "@/lib/use-can";
import { chartTheme } from "@/lib/theme-tokens";
import { qualityRadarRows, type UsageSummaryWithInsights } from "@/lib/usage-insights-api";
import type { UsageSummary } from "@/lib/types";

const nav = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    token: "t",
    user: { id: "u1", full_name: "Ada Lovelace", email: "ada@example.com", created_at: "" },
    isLoading: false,
    logout: vi.fn(),
  }),
  // PermissionsProvider below overrides the real check in these tests.
  usePermission: () => false,
}));

beforeEach(() => {
  nav.push.mockClear();
  document.documentElement.classList.remove("dark");
});

const BASE_SUMMARY = {
  audience: "overview",
  as_of: "2026-10-16T13:00:00Z",
  financial: { total_cost_usd: 96.4 },
} as unknown as UsageSummary;

const WITH_INSIGHTS: UsageSummaryWithInsights = {
  ...BASE_SUMMARY,
  cost_by_stage_series: {
    bucket: "day",
    points: [
      { t: "2026-10-15", query_embed: 0.01, guardrail_embed: 0.01, prompt: 2, cached_prompt: 0.2, reasoning: 1, visible: 0.5, ingestion: 0 },
      { t: "2026-10-16", query_embed: 0.02, guardrail_embed: 0.01, prompt: 2.5, cached_prompt: 0.3, reasoning: 1.2, visible: 0.6, ingestion: 0.1 },
    ],
  },
  forecast: {
    currency: "USD",
    month: "2026-10",
    mtd_usd: 96.4,
    forecast_month_end_usd: 186.8,
    budget_usd: 250,
    method: "linear",
    daily: [
      { t: "2026-10-15", actual_usd: 90, forecast_usd: null },
      { t: "2026-10-16", actual_usd: 96.4, forecast_usd: 96.4 },
      { t: "2026-10-31", actual_usd: null, forecast_usd: 186.8 },
    ],
  },
  cache: { cached_prompt_tokens: 25_000, prompt_tokens: 100_000, hit_ratio: 0.25 },
};

describe("Usage & Cost additions", () => {
  it("renders cost by stage, the forecast with budget, and the cache-hit gauge", () => {
    render(<CostInsights summary={WITH_INSIGHTS} isLoading={false} error={null} onRetry={vi.fn()} />);
    expect(screen.getByRole("group", { name: "Cost by stage" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Month-end cost forecast" })).toBeInTheDocument();
    expect(screen.getByText("$186.80 forecast")).toBeInTheDocument();
    expect(screen.getByText("Budget $250.00")).toBeInTheDocument();
    expect(screen.getByText("25,000 of 100,000 tokens")).toBeInTheDocument();
  });

  it("shows empty states (no invented numbers) when today's summary lacks the fields", () => {
    render(<CostInsights summary={BASE_SUMMARY} isLoading={false} error={null} onRetry={vi.fn()} />);
    expect(screen.getByText("Cost by stage over time is not reported by the server")).toBeInTheDocument();
    expect(screen.getByText("The month-end forecast is not reported by the server")).toBeInTheDocument();
    expect(screen.getByText("Cache statistics are not reported by the server")).toBeInTheDocument();
  });

  it("shows skeletons while loading and a retryable error", () => {
    const onRetry = vi.fn();
    const { rerender } = render(<CostInsights summary={null} isLoading error={null} onRetry={onRetry} />);
    expect(screen.getAllByText("Loading chart")).toHaveLength(3);
    rerender(<CostInsights summary={null} isLoading={false} error="Usage unavailable" onRetry={onRetry} />);
    fireEvent.click(screen.getAllByRole("button", { name: /retry/i })[0]);
    expect(onRetry).toHaveBeenCalled();
  });
});

describe("Evaluation (Live) additions", () => {
  const report = {
    n_cases: 40,
    pass_rate: 0.9,
    quality_dimensions: {
      current_run: { run_id: "r2", started_at: "2026-10-16T00:00:00Z", n_cases: 40 },
      previous_run: { run_id: "r1", started_at: "2026-10-09T00:00:00Z", n_cases: 38 },
      dimensions: [
        { key: "groundedness", label: "Groundedness", current: 0.92, previous: 0.88 },
        { key: "faithfulness", label: "Faithfulness", current: 0.9, previous: 0.91 },
        { key: "recall", label: "Retrieval recall", current: 0.95, previous: null },
      ],
    },
    pass_rate_trend: [
      { run_id: "r1", started_at: "2026-10-09T00:00:00Z", pass_rate: 0.86, n_cases: 38 },
      { run_id: "r2", started_at: "2026-10-16T00:00:00Z", pass_rate: 0.9, n_cases: 40 },
    ],
  };

  it("compares the current run with the previous one on the radar", () => {
    render(<QualityRadarCard report={report} loading={false} />);
    expect(screen.getByText("Current vs previous run (%)")).toBeInTheDocument();
    expect(screen.getByText("Previous run")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Quality dimensions" })).toBeInTheDocument();
  });

  it("falls back to the current run's real measurements, without the old placeholder numbers", () => {
    const { rows, hasPrevious } = qualityRadarRows({ n_cases: 10, pass_rate: 0.5, judge: { faithfulness: 4 } });
    expect(hasPrevious).toBe(false);
    expect(rows).toEqual([
      { metric: "Faithfulness", current: 80, previous: 0 },
      { metric: "Pass rate", current: 50, previous: 0 },
    ]);
    expect(qualityRadarRows({ n_cases: 0, pass_rate: 0 }).rows).toEqual([]);
    render(<QualityRadarCard report={{ n_cases: 0 }} loading={false} />);
    expect(screen.getByText("No scored cases yet")).toBeInTheDocument();
  });

  it("opens the run when a pass-rate marker is clicked", () => {
    render(<PassRateTrendCard report={report} loading={false} />);
    const chart = screen.getByRole("group", { name: "Pass rate per run" });
    const markers = within(chart).getAllByRole("button");
    expect(markers).toHaveLength(2);
    fireEvent.click(markers[1]);
    expect(nav.push).toHaveBeenCalledWith("/admin/evaluation?run=r2");
  });

  it("explains a missing run history", () => {
    render(<PassRateTrendCard report={{ n_cases: 3 }} loading={false} />);
    expect(screen.getByText("Run history is not reported by the server")).toBeInTheDocument();
  });

  it("re-colours the radar when the theme flips (light and dark)", async () => {
    const { container } = render(<QualityRadarCard report={report} loading={false} />);
    const strokes = () => Array.from(container.querySelectorAll("svg [stroke]")).map((el) => el.getAttribute("stroke"));
    expect(strokes()).toContain(chartTheme.light.categorical[0]);
    act(() => {
      document.documentElement.classList.add("dark");
    });
    await waitFor(() => expect(strokes()).toContain(chartTheme.dark.categorical[0]));
  });
});

describe("Chat additions", () => {
  it("shows the brand header and the signed-in user chip", () => {
    render(<ChatHeader />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Assistant");
    const chip = screen.getByTestId("user-chip");
    expect(chip).toHaveAccessibleName("Signed in as Ada Lovelace");
    expect(within(chip).getByText("AL")).toBeInTheDocument();
    expect(within(chip).getByText("ada@example.com")).toBeInTheDocument();
  });

  it("shows View trace to console roles, linking to the trace", () => {
    render(
      <PermissionsProvider permissions={ROLE_PERMISSIONS.operator}>
        <TraceLink requestId="req-42" />
      </PermissionsProvider>,
    );
    expect(screen.getByRole("link", { name: /View trace/ })).toHaveAttribute(
      "href",
      "/admin/governance?tab=Traces&request_id=req-42",
    );
  });

  it("hides View trace for users without a role, and when there is no request id", () => {
    const { rerender } = render(
      <PermissionsProvider permissions={[]}>
        <TraceLink requestId="req-42" />
      </PermissionsProvider>,
    );
    expect(screen.queryByRole("link", { name: /View trace/ })).not.toBeInTheDocument();
    rerender(
      <PermissionsProvider permissions={ROLE_PERMISSIONS.admin}>
        <TraceLink requestId={undefined} />
      </PermissionsProvider>,
    );
    expect(screen.queryByRole("link", { name: /View trace/ })).not.toBeInTheDocument();
  });
});
