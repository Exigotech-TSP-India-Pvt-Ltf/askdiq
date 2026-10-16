/**
 * Dashboard journey (planchanges4 F3.1): the interactive dashboard against
 * mocked responses that follow docs/contracts/dashboard-aggregations.md, and
 * against today's backend shape (new fields absent).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { queryCache } from "@/lib/query-cache";
import { RangeProvider } from "@/lib/range-context";
import { chartTheme } from "@/lib/theme-tokens";
import AdminDashboard from "@/app/(app)/admin/page";
import { ACTIVITY, FULL_DASHBOARD, LEGACY_DASHBOARD } from "../fixtures/dashboard";

const nav = vi.hoisted(() => ({
  url: "/admin",
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: nav.replace }),
  usePathname: () => nav.url.split("?")[0],
  useSearchParams: () => new URLSearchParams(nav.url.split("?")[1] ?? ""),
}));

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ token: "t", user: { id: "u1", full_name: "Ada Admin", email: "ada@example.com", created_at: "" }, isLoading: false, logout: vi.fn() }),
}));

vi.mock("@/lib/audience-context", () => ({
  useAudience: () => ({ audience: "overview", setAudience: vi.fn() }),
}));

// The real Treemap is a code-split Recharts chart that draws nothing in jsdom.
vi.mock("@/components/charts/HeavyCharts", () => ({
  TreemapChart: ({ data, onSelect }: { data: { name: string; value: number }[]; onSelect?: (d: { name: string; value: number }) => void }) => (
    <div role="group" aria-label="Top intents">
      {data.map((d) => (
        <button key={d.name} type="button" onClick={() => onSelect?.(d)}>
          {d.name}
        </button>
      ))}
    </div>
  ),
  SankeyChart: () => null,
}));

type Handler = () => Promise<Response>;
const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));
const never: Handler = () => new Promise<Response>(() => undefined);

let routes: Record<string, Handler>;
let fetchMock: ReturnType<typeof vi.fn>;

function route(url: string): Handler {
  const path = url.replace("/api/proxy/admin", "").split("?")[0];
  return routes[path] ?? (() => json({ detail: "Not Found" }, 404));
}

function renderDashboard(url = "/admin") {
  nav.url = url;
  return render(
    <RangeProvider>
      <AdminDashboard />
    </RangeProvider>,
  );
}

const urls = () => fetchMock.mock.calls.map((c) => String(c[0]));

beforeEach(() => {
  queryCache.clear();
  nav.push.mockClear();
  nav.replace.mockClear();
  document.documentElement.classList.remove("dark");
  routes = {
    "/dashboard": () => json(FULL_DASHBOARD),
    "/dashboard/activity": () => json(ACTIVITY),
    "/governance/overview": () => json({ health: { status: "Degraded", window_minutes: 15 } }),
  };
  fetchMock = vi.fn((input: RequestInfo | URL) => route(String(input))());
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("Dashboard: KPI cards (F3.1.1)", () => {
  it("deep-links every card to the page that owns the number, pre-filtered (newplan §1)", async () => {
    renderDashboard();
    const expected: Record<string, string> = {
      users: "/admin/users",
      active_users: "/admin/usage?tab=users",
      sessions: "/admin/usage?tab=sessions",
      queries: "/admin/usage?tab=queries",
      feedback: "/admin/feedback",
      avg_latency_ms: "/admin/governance?tab=Monitoring",
      error_rate: "/admin/governance?tab=Monitoring",
      cost_usd: "/admin/usage",
    };
    for (const [key, href] of Object.entries(expected)) {
      const card = await screen.findByTestId(`kpi-${key}`);
      expect(within(card).getByRole("link")).toHaveAttribute("href", href);
    }
  });

  it("shows the value, delta, window label and a sparkline from the contract", async () => {
    renderDashboard();
    const queries = await screen.findByTestId("kpi-queries");
    expect(within(queries).getByText("1,200")).toBeInTheDocument();
    expect(within(queries).getByText("Last 7 days")).toBeInTheDocument();
    expect(within(queries).getByText("(improving)")).toBeInTheDocument();
    expect(within(queries).getByTestId("spark-line")).toBeInTheDocument();
    const errors = screen.getByTestId("kpi-error_rate");
    expect(within(errors).getByText("(worsening)")).toBeInTheDocument();
    expect(within(screen.getByTestId("kpi-cost_usd")).getByText("$41.50")).toBeInTheDocument();
  });

  it("prefetches the target page's first query after a 120 ms dwell, not on a quick pass", async () => {
    renderDashboard();
    const feedback = within(await screen.findByTestId("kpi-feedback")).getByRole("link");
    vi.useFakeTimers();
    fireEvent.mouseEnter(feedback);
    act(() => {
      vi.advanceTimersByTime(60);
    });
    fireEvent.mouseLeave(feedback);
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(urls().some((u) => u.startsWith("/api/proxy/admin/feedback/analytics"))).toBe(false);

    fireEvent.focus(feedback);
    act(() => {
      vi.advanceTimersByTime(120);
    });
    expect(urls().some((u) => u.startsWith("/api/proxy/admin/feedback/analytics"))).toBe(true);
  });

  it("sends the DateRangePicker window to the dashboard request", async () => {
    renderDashboard("/admin?from=2026-10-01&to=2026-10-14");
    await screen.findByTestId("kpi-queries");
    const call = urls().find((u) => u.startsWith("/api/proxy/admin/dashboard?"));
    expect(call).toBeDefined();
    const params = new URL(call!, "http://x").searchParams;
    expect(new Date(params.get("from")!).getDate()).toBe(1);
    // `to` is inclusive in the picker, so the request ends at the next midnight.
    expect(new Date(params.get("to")!).getDate()).toBe(15);
  });
});

describe("Dashboard: charts (F3.1.2)", () => {
  it("navigates with the filter when a stacked-area day is clicked", async () => {
    renderDashboard();
    const chart = await screen.findByRole("group", { name: "Queries by outcome" });
    const [first] = within(chart).getAllByRole("button");
    fireEvent.click(first);
    expect(nav.push).toHaveBeenCalledWith("/admin/usage?tab=queries&from=2026-10-10&to=2026-10-10");
  });

  it("opens Analytics filtered by the clicked intent", async () => {
    renderDashboard();
    fireEvent.click(await screen.findByRole("button", { name: "MFA reset" }));
    expect(nav.push).toHaveBeenCalledWith("/admin/analytics?intent=mfa_reset");
  });

  it("renders latency, cost vs budget, satisfaction, activity and health from their sources", async () => {
    renderDashboard();
    expect(await screen.findByRole("group", { name: "Latency percentiles" })).toBeInTheDocument();
    expect(screen.getByText("$250.00")).toBeInTheDocument();
    expect(screen.getByText("$186.80")).toBeInTheDocument();
    expect(screen.getByText("85 ratings")).toBeInTheDocument();
    expect(await screen.findByText("Query answered in 2.1 s")).toBeInTheDocument();
    expect(screen.getByText("Query answered in 2.1 s").closest("a")).toHaveAttribute(
      "href",
      "/admin/governance?tab=Traces&request_id=req-123",
    );
    expect(screen.getByText("Thumbs down on an answer").closest("a")).toHaveAttribute("href", "/admin/feedback?message_id=msg-7");
    expect(await screen.findByRole("link", { name: /System health: Degraded/ })).toHaveAttribute(
      "href",
      "/admin/governance?tab=Overall",
    );
  });

  it("one slow widget does not block the others", async () => {
    routes["/dashboard/activity"] = never;
    routes["/governance/overview"] = never;
    renderDashboard();
    expect(await screen.findByTestId("kpi-queries")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Queries by outcome" })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Loading activity" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /System health/ })).not.toBeInTheDocument();
  });

  it("a slow summary keeps its own skeletons while the activity feed renders", async () => {
    routes["/dashboard"] = never;
    renderDashboard();
    expect(await screen.findByText("Query answered in 2.1 s")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Loading Queries" })).toBeInTheDocument();
  });
});

describe("Dashboard against today's backend (new fields absent)", () => {
  beforeEach(() => {
    routes = { "/dashboard": () => json(LEGACY_DASHBOARD) };
  });

  it("shows the existing data with honest windows and empty states, no invented numbers", async () => {
    renderDashboard();
    const queries = await screen.findByTestId("kpi-queries");
    expect(within(queries).getByText("All time")).toBeInTheDocument();
    expect(within(queries).getByText("Trend: last 7 days")).toBeInTheDocument();
    expect(within(screen.getByTestId("kpi-active_users")).getByText("Last 24 hours")).toBeInTheDocument();
    const cost = screen.getByTestId("kpi-cost_usd");
    expect(within(cost).getByText("—")).toBeInTheDocument();
    expect(within(cost).getByText("Not reported by the server")).toBeInTheDocument();

    expect(screen.getByRole("group", { name: "Queries per day" })).toBeInTheDocument();
    expect(screen.getByText("Latency percentiles are not reported by the server")).toBeInTheDocument();
    expect(screen.getByText("Month-to-date cost is not reported by the server")).toBeInTheDocument();
    expect(screen.getByText("Satisfaction is not reported by the server")).toBeInTheDocument();
    expect(screen.getByText("Intents are not reported by the server")).toBeInTheDocument();
    expect(await screen.findByText("No activity feed on this server")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /System health/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/coming soon/i)).not.toBeInTheDocument();
    // Pipeline stages are real 24 h averages.
    expect(screen.getByText("RAG pipeline")).toBeInTheDocument();
  });

  it("shows a retryable error when the summary fails", async () => {
    routes = { "/dashboard": () => json({ detail: "Database unavailable" }, 503) };
    renderDashboard();
    expect(await screen.findByText("Couldn't load the key metrics")).toBeInTheDocument();
    routes = { "/dashboard": () => json(LEGACY_DASHBOARD) };
    fireEvent.click(screen.getAllByRole("button", { name: /retry/i })[0]);
    expect(await screen.findByTestId("kpi-queries")).toBeInTheDocument();
  });
});

describe("Dashboard: light and dark", () => {
  it("re-colours charts from the chart theme when the theme flips, without a reload", async () => {
    renderDashboard();
    const card = await screen.findByTestId("kpi-queries");
    const line = () => within(card).getByTestId("spark-line");
    expect(line()).toHaveAttribute("stroke", chartTheme.light.categorical[0]);
    act(() => {
      document.documentElement.classList.add("dark");
    });
    await waitFor(() => expect(line()).toHaveAttribute("stroke", chartTheme.dark.categorical[0]));
    expect(card.querySelector(".bg-surface-card")).not.toBeNull();
  });
});
