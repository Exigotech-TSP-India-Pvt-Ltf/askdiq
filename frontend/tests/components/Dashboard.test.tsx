/**
 * Dashboard building blocks: contract helpers, KPI
 * mapping, activity links, health pill and the generic LineTrend chart.
 */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { queryCache } from "@/lib/query-cache";
import { ApiError } from "@/lib/api";
import { clampCustomRange, presetRange } from "@/lib/range-context";
import { bucketLabel, isNotImplemented, rangeParams } from "@/lib/dashboard-api";
import { DASHBOARD_KPIS, kpiTrend, kpiValue } from "@/components/admin/dashboard/DashboardKpis";
import { activityHref } from "@/components/admin/dashboard/ActivityFeed";
import { queriesHrefFor } from "@/components/admin/dashboard/DashboardCharts";
import { HealthPill } from "@/components/admin/dashboard/HealthPill";
import { LineTrend } from "@/components/charts/LineTrend";
import { ACTIVITY, FULL_DASHBOARD, LEGACY_DASHBOARD } from "../fixtures/dashboard";

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ token: "t", user: null, isLoading: false, logout: vi.fn() }),
}));
vi.mock("@/lib/audience-context", () => ({
  useAudience: () => ({ audience: "users", setAudience: vi.fn() }),
}));

beforeEach(() => {
  queryCache.clear();
  vi.unstubAllGlobals();
});

describe("dashboard contract helpers", () => {
  it("builds the request window from presets and inclusive custom ranges", () => {
    const now = new Date("2026-10-16T13:00:30Z");
    const p = rangeParams(presetRange("7d", now), "overview");
    expect(p).toEqual({ audience: "overview", from: "2026-10-09T13:00:00.000Z", to: "2026-10-16T13:00:00.000Z" });
    const custom = rangeParams(clampCustomRange(new Date(2026, 9, 1), new Date(2026, 9, 3), now), "users");
    expect(new Date(custom.to).getTime() - new Date(custom.from).getTime()).toBe(3 * 24 * 3600 * 1000);
  });

  it("labels day and hour buckets in UTC and treats 404/405 as not implemented", () => {
    expect(bucketLabel("2026-10-16")).toBe("16 Oct");
    expect(bucketLabel("not-a-date")).toBe("not-a-date");
    expect(isNotImplemented(new ApiError("Not Found", 404))).toBe(true);
    expect(isNotImplemented(new ApiError("boom", 500))).toBe(false);
  });

  it("every KPI has a deep link and a prefetch target", () => {
    expect(DASHBOARD_KPIS).toHaveLength(8);
    for (const def of DASHBOARD_KPIS) {
      expect(def.href.startsWith("/admin/")).toBe(true);
      expect(def.prefetchPath.startsWith("/")).toBe(true);
    }
  });
});

describe("KPI mapping", () => {
  it("reads cost from cost_usd, then cost.mtd_usd, else null", () => {
    expect(kpiValue(FULL_DASHBOARD, "cost_usd")).toBe(41.5);
    expect(kpiValue({ ...FULL_DASHBOARD, cost_usd: undefined }, "cost_usd")).toBe(96.4);
    expect(kpiValue(LEGACY_DASHBOARD, "cost_usd")).toBeNull();
    expect(kpiValue(LEGACY_DASHBOARD, "queries")).toBe(1200);
  });

  it("uses the server sparkline, falls back to query_trend for queries only, and skips null buckets", () => {
    expect(kpiTrend(FULL_DASHBOARD, "users")).toHaveLength(7);
    expect(kpiTrend(LEGACY_DASHBOARD, "queries")).toHaveLength(7);
    expect(kpiTrend(LEGACY_DASHBOARD, "users")).toEqual([]);
    const gappy = { ...FULL_DASHBOARD, sparklines: { users: [{ t: "2026-10-16", value: null }, { t: "2026-10-15", value: 3 }] } };
    expect(kpiTrend(gappy, "users")).toEqual([{ label: "15 Oct", value: 3 }]);
  });
});

describe("activity links", () => {
  it("links feedback to Feedback, other events to their trace, conversation or document", () => {
    const [query, feedback] = ACTIVITY.items;
    expect(activityHref(query)).toBe("/admin/governance?tab=Traces&request_id=req-123");
    expect(activityHref(feedback)).toBe("/admin/feedback?message_id=msg-7");
    expect(activityHref({ ...query, request_id: null })).toBe("/admin/conversations/conv-9");
    expect(activityHref({ ...query, request_id: null, conversation_id: null, document_id: "d1" })).toBe("/documents/d1");
    expect(activityHref({ ...query, request_id: null, conversation_id: null })).toBeNull();
  });

  it("limits the queries deep link to the clicked bucket's day", () => {
    expect(queriesHrefFor("2026-10-16")).toBe("/admin/usage?tab=queries&from=2026-10-16&to=2026-10-16");
    expect(queriesHrefFor("2026-10-16T09:00:00Z")).toBe("/admin/usage?tab=queries&from=2026-10-16&to=2026-10-16");
  });
});

describe("HealthPill", () => {
  it("shows the governance health with the audience and links to the governance overview", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL) =>
      Promise.resolve(new Response(JSON.stringify({ health: { status: "Incident", window_minutes: 15 } }))),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<HealthPill />);
    const pill = await screen.findByRole("link", { name: /System health: Incident/ });
    expect(pill).toHaveAttribute("href", "/admin/governance?tab=Overall");
    expect(pill.className).toContain("bg-status-danger-bg");
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/proxy/admin/governance/overview?audience=users");
  });

  it("renders nothing when the endpoint is missing or returns an unknown state", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 404 }))));
    const { container } = render(<HealthPill />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
  });
});

describe("LineTrend", () => {
  const rows = [
    { label: "Run 1", rate: 80, forecast: null },
    { label: "Run 2", rate: null, forecast: 85 },
    { label: "Run 3", rate: 90, forecast: 92 },
  ];

  it("draws one path per contiguous segment, markers, a dashed series and a reference line", () => {
    const { container } = render(
      <LineTrend
        data={rows}
        xKey="label"
        series={[
          { key: "rate", label: "Rate" },
          { key: "forecast", label: "Forecast", dashed: true },
        ]}
        markers
        reference={{ value: 95, label: "Budget" }}
        label="Trend"
      />,
    );
    expect(container.querySelectorAll('[data-series="rate"] circle')).toHaveLength(2);
    expect(container.querySelector('[data-series="forecast"] path')).toHaveAttribute("stroke-dasharray", "5 4");
    expect(screen.getByText("Budget")).toBeInTheDocument();
  });

  it("selects a row on click and Enter and moves with arrow keys", () => {
    const onSelect = vi.fn();
    render(<LineTrend data={rows} xKey="label" series={[{ key: "rate", label: "Rate" }]} onSelect={onSelect} label="Trend" />);
    const marks = screen.getAllByRole("button");
    fireEvent.click(marks[0]);
    expect(onSelect).toHaveBeenLastCalledWith(rows[0]);
    fireEvent.keyDown(marks[0], { key: "ArrowRight" });
    expect(marks[1]).toHaveFocus();
    fireEvent.keyDown(marks[1], { key: "Enter" });
    expect(onSelect).toHaveBeenLastCalledWith(rows[1]);
    expect(marks[2]).toHaveAccessibleName("Run 3: Rate 90");
  });

  it("shows the empty state without data", () => {
    render(<LineTrend data={[]} xKey={"label" as never} series={[]} label="Trend" />);
    expect(screen.getByRole("status")).toHaveTextContent("No data for this period");
  });
});
