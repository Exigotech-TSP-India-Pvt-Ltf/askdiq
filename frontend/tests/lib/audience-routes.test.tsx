import { describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { AUDIENCE_PAGE, audiencePageHref, parseAudienceTab } from "@/lib/audience-routes";
import { conversationsHref, visitorsHref } from "@/lib/entity-routes";
import { tourIdForPath } from "@/lib/tour/routes";
import { useClientTable } from "@/lib/use-client-table";
import { activitySparks } from "@/lib/visitor-activity";
import { isNavItemActive, NAV_ITEMS } from "@/components/Sidebar";
import type { VisitorTimeline } from "@/lib/entity-stats-api";

const redirect = vi.fn((url: string) => {
  throw new Error(`REDIRECT ${url}`);
});
vi.mock("next/navigation", async (orig) => ({ ...(await orig<object>()), redirect: (url: string) => redirect(url) }));

describe("Visitors & Conversations routes", () => {
  it("parses the tab (unknown = visitors)", () => {
    expect(parseAudienceTab("conversations")).toBe("conversations");
    expect(parseAudienceTab("visitors")).toBe("visitors");
    expect(parseAudienceTab(null)).toBe("visitors");
    expect(parseAudienceTab("nope")).toBe("visitors");
  });

  it("keeps old query strings when moving to the merged page", () => {
    expect(audiencePageHref("visitors")).toBe(AUDIENCE_PAGE);
    expect(audiencePageHref("conversations", { ip: "10.0.0.1", tab: "x" })).toBe("/admin/visitors?ip=10.0.0.1&tab=conversations");
    expect(audiencePageHref("visitors", { weekday: "1", hour: ["9"] })).toBe("/admin/visitors?weekday=1&hour=9");
  });

  it("list deep links target the merged page tabs", () => {
    expect(visitorsHref({ weekday: 0, hour: 9 })).toBe("/admin/visitors?weekday=0&hour=9");
    expect(conversationsHref({ ip: "1.2.3.4" })).toBe("/admin/visitors?tab=conversations&ip=1.2.3.4");
  });

  it("the old list pages redirect into the right tab with their filters", async () => {
    const users = (await import("@/app/(app)/admin/users/page")).default;
    const convs = (await import("@/app/(app)/admin/conversations/page")).default;
    await expect(users({ searchParams: Promise.resolve({ weekday: "2" }) })).rejects.toThrow(
      "REDIRECT /admin/visitors?weekday=2",
    );
    await expect(convs({ searchParams: Promise.resolve({ ip: "1.2.3.4" }) })).rejects.toThrow(
      "REDIRECT /admin/visitors?ip=1.2.3.4&tab=conversations",
    );
  });

  it("the page tour follows the open tab; detail pages keep theirs", () => {
    expect(tourIdForPath("/admin/visitors")).toBe("visitors");
    expect(tourIdForPath("/admin/visitors", "?tab=conversations")).toBe("conversations");
    expect(tourIdForPath("/admin/users/client-1.2.3.4")).toBe("visitors");
    expect(tourIdForPath("/admin/conversations/abc")).toBe("conversations");
  });

  it("one sidebar item covers the merged page and both detail pages", () => {
    const item = NAV_ITEMS.find((i) => i.href === AUDIENCE_PAGE);
    expect(item?.label).toBe("Visitors & Conversations");
    expect(NAV_ITEMS.some((i) => i.label === "Conversation Logs")).toBe(false);
    for (const path of ["/admin/visitors", "/admin/users/client-1", "/admin/conversations/abc"]) {
      expect(isNavItemActive(path, item!)).toBe(true);
    }
    expect(isNavItemActive("/admin/usage", item!)).toBe(false);
  });
});

describe("useClientTable", () => {
  const rows = Array.from({ length: 23 }, (_, i) => ({ id: i, text: i % 2 ? "odd row" : "even row" }));

  it("searches, filters and pages in memory", () => {
    const { result, rerender } = renderHook(({ odd }) =>
      useClientTable(rows, { searchText: (r) => r.text, filter: odd ? (r) => r.id % 2 === 1 : undefined }),
    { initialProps: { odd: false } });
    expect(result.current.total).toBe(23);
    expect(result.current.pageCount).toBe(3);
    expect(result.current.paged).toHaveLength(10);
    act(() => result.current.setPage(3));
    expect(result.current.paged.map((r) => r.id)).toEqual([20, 21, 22]);
    act(() => result.current.setSearch("even"));
    expect(result.current.page).toBe(1);
    expect(result.current.total).toBe(12);
    rerender({ odd: true });
    expect(result.current.total).toBe(0);
    expect(result.current.page).toBe(1);
  });
});

describe("activitySparks", () => {
  const base = {
    visitor_id: "client-1",
    ip_address: "1",
    sessions: [],
    queries: [],
    feedback: [],
    truncated: { sessions: false, queries: false, feedback: false },
    cost_series: [
      { day: "2026-10-01", cost_usd: 0.5, queries: 2 },
      { day: "2026-10-02", cost_usd: 0, queries: 0 },
    ],
  } satisfies VisitorTimeline;

  it("uses activity_series per day and totals the range", () => {
    const s = activitySparks({
      ...base,
      activity_series: [
        { day: "2026-10-01", sessions: 1, queries: 2, feedback: 1 },
        { day: "2026-10-02", sessions: 0, queries: 0, feedback: 0 },
      ],
    });
    expect(s?.totals).toEqual({ sessions: 1, queries: 2, feedback: 1, activeDays: 1, days: 2 });
    expect(s?.cost.map((p) => p.value)).toEqual([0.5, 0]);
    expect(s?.active.map((p) => p.value)).toEqual([1, 0]);
  });

  it("falls back to cost_series queries on older backends", () => {
    const s = activitySparks(base);
    expect(s?.queries.map((p) => p.value)).toEqual([2, 0]);
    expect(s?.totals.sessions).toBe(0);
    expect(activitySparks(null)).toBeNull();
  });
});
