/**
 * Analytics on the global date range: inside a RangeProvider the period comes
 * from the header picker's `range` / `from` / `to` URL params, old
 * `period=` links are rewritten, and other filters never drop the range.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import AnalyticsPage from "@/app/(app)/admin/analytics/page";
import { normaliseLegacyPeriod, periodFromRange } from "@/components/admin/analytics/url-state";
import { clampCustomRange, presetRange } from "@/lib/range-context";
import { currentParams, history, renderPage } from "../../helpers/entity-harness";
import { mockInsightsFetch } from "../../journeys/analytics-harness";

vi.mock("next/navigation", async () => (await import("../../helpers/router-mock")).navigationMock());
vi.mock("@/components/charts/HeavyCharts", async () => {
  const harness = await import("../../journeys/analytics-harness");
  return { TreemapChart: harness.TreemapStandIn, SankeyChart: () => null };
});

const NOW = new Date(2026, 9, 19, 12, 0, 0);

let api: ReturnType<typeof mockInsightsFetch>;

function renderAnalytics(search = "") {
  return renderPage(
    <MotionProvider>
      <AnalyticsPage />
    </MotionProvider>,
    { url: `/admin/analytics${search}` },
  );
}

/** Every insights aggregate / table request made so far (not the backlog poll). */
function rangedCalls() {
  return api.calls.filter((c) => c.endpoint !== "pending" && c.endpoint !== "conversation_detail");
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  api = mockInsightsFetch();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("range -> insights period mapping", () => {
  it("maps presets and custom ranges to the API period", () => {
    expect(periodFromRange(presetRange("7d", NOW))).toEqual({ period: "7d", from: "", to: "" });
    expect(periodFromRange(presetRange("30d", NOW))).toEqual({ period: "30d", from: "", to: "" });
    expect(periodFromRange(presetRange("90d", NOW))).toEqual({ period: "custom", from: "2026-07-22", to: "2026-10-19" });
    expect(periodFromRange(presetRange("24h", NOW))).toEqual({ period: "custom", from: "2026-10-18", to: "2026-10-19" });
    const custom = clampCustomRange(new Date(2026, 9, 1), new Date(2026, 9, 5), NOW);
    expect(periodFromRange(custom)).toEqual({ period: "custom", from: "2026-10-01", to: "2026-10-05" });
  });

  it("rewrites old period links for the global range", () => {
    expect(normaliseLegacyPeriod("tab=trend")).toBeNull();
    expect(normaliseLegacyPeriod("period=30d&tab=trend")).toBe("tab=trend&range=30d");
    expect(normaliseLegacyPeriod("period=7d&outcome=Served")).toBe("outcome=Served");
    expect(normaliseLegacyPeriod("period=all")).toBe("");
    expect(normaliseLegacyPeriod("period=custom&from=2026-10-01&to=2026-10-05")).toBe("from=2026-10-01&to=2026-10-05");
  });
});

describe("Analytics with the global date range", { timeout: 15_000 }, () => {
  it("shows the header picker and no Period select", async () => {
    renderAnalytics();
    await waitFor(() => expect(api.last("summary")).toBeDefined());
    expect(screen.queryByLabelText("Period")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Outcome")).toBeInTheDocument();
  });

  it("?range=30d sends period=30d on every insights request", async () => {
    renderAnalytics("?range=30d");
    await waitFor(() => {
      expect(api.last("summary")).toBeDefined();
      expect(api.last("flow")).toBeDefined();
    });
    const calls = rangedCalls();
    expect(calls.length).toBeGreaterThan(1);
    for (const call of calls) {
      expect(call.params.get("period")).toBe("30d");
      expect(call.params.get("from")).toBeNull();
    }
  });

  it("a custom from / to sends period=custom with those days", async () => {
    renderAnalytics("?tab=conversations&from=2026-10-01&to=2026-10-05");
    await waitFor(() => expect(api.last("conversations")).toBeDefined());
    for (const call of rangedCalls()) {
      expect(call.params.get("period")).toBe("custom");
      expect(call.params.get("from")).toBe("2026-10-01");
      expect(call.params.get("to")).toBe("2026-10-05");
    }
  });

  it("the default range (no params) is the last 7 days", async () => {
    renderAnalytics();
    await waitFor(() => expect(api.last("summary")?.params.get("period")).toBe("7d"));
  });

  it("an old ?period=30d link becomes range=30d without a wrong first request", async () => {
    renderAnalytics("?period=30d&tab=trend");
    await waitFor(() => expect(currentParams().get("range")).toBe("30d"));
    expect(currentParams().get("period")).toBeNull();
    expect(currentParams().get("tab")).toBe("trend");
    await waitFor(() => expect(api.last("trend")).toBeDefined());
    for (const call of rangedCalls()) expect(call.params.get("period")).toBe("30d");
  });

  it("an old custom period link keeps its days and drops period", async () => {
    renderAnalytics("?period=custom&from=2026-10-01&to=2026-10-05");
    await waitFor(() => expect(currentParams().get("period")).toBeNull());
    expect(currentParams().get("from")).toBe("2026-10-01");
    expect(currentParams().get("to")).toBe("2026-10-05");
  });

  it("an old ?period=all link falls back to the default range", async () => {
    renderAnalytics("?period=all");
    await waitFor(() => expect(currentParams().get("period")).toBeNull());
    expect(currentParams().get("range")).toBeNull();
    await waitFor(() => expect(api.last("summary")?.params.get("period")).toBe("7d"));
  });

  it("changing another filter keeps range / from / to in the URL", async () => {
    renderAnalytics("?range=30d");
    fireEvent.change(await screen.findByLabelText("Outcome"), { target: { value: "Not served" } });
    await waitFor(() => expect(currentParams().get("outcome")).toBe("Not served"));
    expect(currentParams().get("range")).toBe("30d");
    await waitFor(() => {
      const call = api.last("summary");
      expect(call?.params.get("outcome")).toBe("Not served");
      expect(call?.params.get("period")).toBe("30d");
    });

    renderAnalytics("?from=2026-10-01&to=2026-10-05");
    fireEvent.change(await screen.findByLabelText("Rating"), { target: { value: "Dropped" } });
    await waitFor(() => expect(currentParams().get("rating")).toBe("Dropped"));
    expect(currentParams().get("from")).toBe("2026-10-01");
    expect(currentParams().get("to")).toBe("2026-10-05");

    // Clear filters resets this page's filters, not the global range.
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(currentParams().get("rating")).toBeNull());
    expect(currentParams().get("from")).toBe("2026-10-01");
  });

  it("a trend day click sets the global range to that day; clicking again resets it", async () => {
    const { container } = renderAnalytics("?tab=trend&range=30d");
    const day = await waitFor(() => {
      const el = container.querySelector<SVGRectElement>('[data-day="2026-05-15"]');
      expect(el).not.toBeNull();
      return el as SVGRectElement;
    });
    fireEvent.click(day);
    await waitFor(() => expect(currentParams().get("from")).toBe("2026-05-15"));
    expect(currentParams().get("to")).toBe("2026-05-15");
    expect(currentParams().get("range")).toBeNull();
    expect(currentParams().get("period")).toBeNull();
    await waitFor(() => expect(api.last("trend")?.params.get("from")).toBe("2026-05-15"));

    await waitFor(() =>
      expect(container.querySelector('[data-day="2026-05-15"]')).toHaveAttribute("aria-pressed", "true"),
    );
    fireEvent.click(container.querySelector('[data-day="2026-05-15"]') as SVGRectElement);
    await waitFor(() => expect(currentParams().get("from")).toBeNull());
    expect(currentParams().get("to")).toBeNull();
    expect(currentParams().get("range")).toBeNull();
  });

  it("a range change from the header resets the Conversations page", async () => {
    renderAnalytics("?tab=conversations&page=3");
    await waitFor(() => expect(api.last("conversations")).toBeDefined());
    expect(currentParams().get("page")).toBe("3");
    act(() => history.replace("/admin/analytics?tab=conversations&page=3&range=90d"));
    await waitFor(() => expect(currentParams().get("page")).toBeNull());
    expect(currentParams().get("range")).toBe("90d");
  });
});
