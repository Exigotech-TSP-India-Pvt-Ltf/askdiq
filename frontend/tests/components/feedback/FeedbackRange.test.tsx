/**
 * Global date range on Feedback: the header picker's range reaches the
 * analytics (Overview, Knowledge Gaps), the Review Queue list, Golden Cases
 * and the Preference Data export, and each cache key carries it.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { FeedbackWorkspace } from "@/components/admin/feedback/FeedbackWorkspace";
import { feedbackKeys } from "@/lib/feedback-api";
import { toListParams, parseFilters } from "@/lib/feedback-filters";
import { queryCache } from "@/lib/query-cache";
import { RangeProvider } from "@/lib/range-context";
import { Providers, analyticsFixture, history, installFetch, makeGolden, page, type FetchCall } from "./fixtures";

vi.mock("next/navigation", async () => (await import("./fixtures")).navigationMock());

let calls: FetchCall[];

beforeEach(() => {
  queryCache.clear();
  calls = installFetch((call) => {
    if (call.path.startsWith("/admin/feedback/analytics")) return { json: analyticsFixture };
    if (call.path.startsWith("/admin/feedback/golden")) return { json: { items: [makeGolden()], total: 1, limit: 100, offset: 0 } };
    if (call.path.startsWith("/admin/feedback/pairs")) return { json: { pairs: [], truncated: false } };
    if (call.path.startsWith("/admin/feedback/export")) return { text: "" };
    if (call.path.startsWith("/admin/feedback?")) return { json: page([]) };
    return undefined;
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderAt(url: string) {
  history.reset(url);
  render(
    <Providers>
      <RangeProvider>
        <FeedbackWorkspace />
      </RangeProvider>
    </Providers>,
  );
}

const params = (prefix: string) =>
  calls.filter((c) => c.path.startsWith(prefix)).map((c) => new URLSearchParams(c.path.split("?")[1] ?? ""));

describe("Feedback date range", () => {
  it("sends the custom range to the analytics", async () => {
    renderAt("/admin/feedback?from=2026-10-01&to=2026-10-05");
    await screen.findByText("Total feedback");
    const [analytics] = params("/admin/feedback/analytics");
    expect(analytics.get("from")).toBe("2026-10-01");
    expect(analytics.get("to")).toBe("2026-10-05");
  });

  it("bounds the Review Queue list by the range", async () => {
    renderAt("/admin/feedback?tab=queue&from=2026-10-01&to=2026-10-05");
    await waitFor(() => expect(params("/admin/feedback?").length).toBeGreaterThan(0));
    const list = params("/admin/feedback?").at(-1)!;
    expect(list.get("date_from")).toBe("2026-10-01T00:00:00Z");
    expect(list.get("date_to")).toBe("2026-10-05T23:59:59Z");
  });

  it("sends the range to Golden Cases", async () => {
    renderAt("/admin/feedback?tab=golden&from=2026-10-01&to=2026-10-05");
    await waitFor(() => expect(params("/admin/feedback/golden").length).toBeGreaterThan(0));
    expect(params("/admin/feedback/golden")[0].get("from")).toBe("2026-10-01");
    expect(params("/admin/feedback/golden")[0].get("to")).toBe("2026-10-05");
  });

  it("a chart-picked day wins over the range for the list", () => {
    const filters = parseFilters(new URLSearchParams("day=2026-10-03"));
    expect(toListParams(filters, { from: "2026-10-01", to: "2026-10-05" })).toMatchObject({
      date_from: "2026-10-03T00:00:00Z",
      date_to: "2026-10-03T23:59:59Z",
    });
    expect(toListParams(parseFilters(new URLSearchParams()), {})).toMatchObject({
      date_from: undefined,
      date_to: undefined,
    });
  });

  it("keys every cached feedback query by the range (unranged keys unchanged)", () => {
    const days = { from: "2026-10-01", to: "2026-10-05" };
    expect(feedbackKeys.analytics("users")).toBe("admin/feedback/analytics/users");
    expect(feedbackKeys.analytics("users", days)).not.toBe(feedbackKeys.analytics("users"));
    expect(feedbackKeys.goldenFor(days).startsWith(feedbackKeys.golden)).toBe(true);
    expect(feedbackKeys.goldenFor(days)).not.toBe(feedbackKeys.goldenFor());
    expect(feedbackKeys.pairs(true, days)).not.toBe(feedbackKeys.pairs(true));
  });
});
