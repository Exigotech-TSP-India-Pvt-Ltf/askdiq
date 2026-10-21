/**
 * KPIs use the unfiltered analytics total, the spike alert
 * shows, and a click on each chart type sets the right filter chip (synced to
 * the URL) and opens the Review Queue. Chart colours follow light / dark.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FeedbackWorkspace } from "@/components/admin/feedback/FeedbackWorkspace";
import { queryCache } from "@/lib/query-cache";
import { chartTheme } from "@/lib/theme-tokens";
import { Providers, analyticsFixture, history, installFetch, page, type FetchCall } from "./fixtures";

vi.mock("next/navigation", async () => (await import("./fixtures")).navigationMock());

let calls: FetchCall[];

beforeEach(() => {
  queryCache.clear();
  calls = installFetch((call) => {
    if (call.path.startsWith("/admin/feedback/analytics")) return { json: analyticsFixture };
    if (call.path.startsWith("/admin/feedback?")) return { json: { ...page([]), total: 3 } };
    return undefined;
  });
  history.reset("/admin/feedback");
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.classList.remove("dark");
});

async function renderOverview() {
  render(
    <Providers>
      <FeedbackWorkspace />
    </Providers>,
  );
  await screen.findByText("Total feedback");
}

function chips() {
  return screen.getByRole("region", { name: "Active filters" });
}

const CASES: { chart: string; mark: RegExp; param: [string, string]; chip: string }[] = [
  { chart: "rating bar", mark: /^1 star: /, param: ["rating", "1"], chip: "Rating: 1" },
  { chart: "outcome bar", mark: /^Ungrounded: /, param: ["outcome", "ungrounded"], chip: "Outcome: Ungrounded" },
  { chart: "reasons bar", mark: /^Wrong: /, param: ["reason", "wrong"], chip: "Reason: Wrong" },
  // One date tick format across the console: "13 Oct" (VA-18).
  { chart: "trend point", mark: /^13 Oct: Positive/, param: ["day", "2026-10-13"], chip: "Day: 2026-10-13" },
  { chart: "funnel stage", mark: /^Reviewed: /, param: ["status", "reviewed"], chip: "Status: Reviewed" },
  { chart: "KPI sparkline point", mark: /^2026-10-14: /, param: ["day", "2026-10-14"], chip: "Day: 2026-10-14" },
];

describe("Feedback Overview", () => {
  it("uses the unfiltered analytics total for the KPI and shows the spike alert", async () => {
    await renderOverview();
    // PR-21's KpiTrendCard wraps the label (window label slot): climb to the tile.
    const card = screen.getByText("Total feedback").closest("div")!.parentElement!.parentElement!;
    expect(card).toHaveTextContent("42");
    expect(screen.getByRole("alert")).toHaveTextContent(/negative feedback spike/i);
    // KPIs come from /analytics (asked first); the list is only warmed for the
    // Review Queue tab once the page is idle.
    expect(calls[0].path).toMatch(/^\/admin\/feedback\/analytics/);
  });

  it.each(CASES)("a click on a $chart sets the $chip chip and opens the queue", async ({ mark, param, chip }) => {
    await renderOverview();
    const [target] = screen.getAllByRole("button", { name: mark });
    fireEvent.click(target);
    await waitFor(() => expect(history.params().get(param[0])).toBe(param[1]));
    expect(history.params().get("tab")).toBe("queue");
    expect(within(chips()).getByText(chip)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /review queue/i })).toHaveAttribute("aria-selected", "true");
  });

  it("a quadrant click filters by groundedness and sentiment", async () => {
    await renderOverview();
    fireEvent.click(screen.getByRole("button", { name: /^Disliked and ungrounded/ }));
    await waitFor(() => expect(history.params().get("grounded")).toBe("false"));
    expect(history.params().get("sentiment")).toBe("negative");
    expect(within(chips()).getByText("Ungrounded")).toBeInTheDocument();
    expect(within(chips()).getByText("Negative (1-2)")).toBeInTheDocument();
  });

  it("chart marks are keyboard reachable (Enter selects)", async () => {
    await renderOverview();
    const mark = screen.getByRole("button", { name: /^5 stars: / });
    fireEvent.keyDown(mark, { key: "Enter" });
    await waitFor(() => expect(history.params().get("rating")).toBe("5"));
  });

  it("removing a chip clears its URL param; Clear all widens the queue", async () => {
    history.reset("/admin/feedback?tab=queue&rating=2&reason=wrong");
    render(
      <Providers>
        <FeedbackWorkspace />
      </Providers>,
    );
    fireEvent.click(within(chips()).getByRole("button", { name: "Remove filter Reason: Wrong" }));
    expect(history.params().get("reason")).toBeNull();
    expect(history.params().get("rating")).toBe("2");
    fireEvent.click(within(chips()).getByRole("button", { name: "Clear all" }));
    expect(history.params().get("rating")).toBeNull();
    expect(history.params().get("status")).toBe("all");
  });

  it("chart colours follow the theme (light and dark)", async () => {
    await renderOverview();
    const bar = () => screen.getByRole("button", { name: /^5 stars: / }).querySelectorAll("rect")[2];
    expect(bar()).toHaveAttribute("fill", chartTheme.light.status.ok);
    act(() => document.documentElement.classList.add("dark"));
    await waitFor(() => expect(bar()).toHaveAttribute("fill", chartTheme.dark.status.ok));
  });
});

describe("Feedback Overview data fidelity", () => {
  it("the Coverage sparkline plots daily ratings / answers, not the ratings count", async () => {
    queryCache.clear();
    installFetch((call) => {
      if (call.path.startsWith("/admin/feedback/analytics")) {
        return {
          json: {
            ...analyticsFixture,
            daily_trend: analyticsFixture.daily_trend.map((p, i) => ({ ...p, answers: 100, coverage: [0.1, 0.14, 0.18][i] })),
          },
        };
      }
      if (call.path.startsWith("/admin/feedback?")) return { json: { ...page([]), total: 3 } };
      return undefined;
    });
    await renderOverview();
    const sparkline = screen.getByRole("group", { name: "Coverage trend" });
    expect(within(sparkline).getAllByRole("button", { name: /^2026-10-14: 18$/ }).length).toBeGreaterThan(0);
  });

  it("funnel stages show the rows in each status, matching what a click filters to", async () => {
    await renderOverview();
    // Fixture: new 30, reviewed 6, promoted 4 (not cumulative 40 / 10 / 4).
    expect(screen.getAllByRole("button", { name: /^New: 30 / }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /^Reviewed: 6 / }).length).toBeGreaterThan(0);
  });
});

describe("Feedback outcomes", () => {
  it("charts ratings on empty LLM replies instead of dropping them", async () => {
    queryCache.clear();
    installFetch((call) => {
      if (call.path.startsWith("/admin/feedback/analytics")) {
        return {
          json: {
            ...analyticsFixture,
            feedback_by_outcome: { ...analyticsFixture.feedback_by_outcome, empty_llm_output: { positive: 0, negative: 2, total: 2 } },
          },
        };
      }
      if (call.path.startsWith("/admin/feedback?")) return { json: { ...page([]), total: 3 } };
      return undefined;
    });
    await renderOverview();
    expect(screen.getAllByRole("button", { name: /^Empty LLM reply: / }).length).toBeGreaterThan(0);
  });
});

describe("Feedback filter chips on the tab row (OR-13)", () => {
  it("regression: the Status: New chip opens the statuses and choosing Reviewed sets status=reviewed in the URL", async () => {
    history.reset("/admin/feedback?tab=queue");
    render(
      <Providers>
        <FeedbackWorkspace />
      </Providers>,
    );
    const bar = chips();
    // Same row as the tabs and the date filter.
    const row = bar.closest("[data-page-toolbar]");
    expect(row).toContainElement(screen.getByRole("tablist", { name: "Feedback sections" }));
    fireEvent.click(within(bar).getByRole("button", { name: "Status: New, change filter" }));
    const list = screen.getByRole("listbox", { name: "Status" });
    expect(within(list).getByRole("option", { name: "New" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(within(list).getByRole("option", { name: "Reviewed" }));
    await waitFor(() => expect(history.params().get("status")).toBe("reviewed"));
    expect(within(chips()).getByRole("button", { name: "Status: Reviewed, change filter" })).toBeInTheDocument();
    expect(within(chips()).getByRole("button", { name: "Filters, 1 active" })).toBeInTheDocument();
  });

  it("the Filters popover edits rating and reason through the URL", async () => {
    history.reset("/admin/feedback?tab=queue");
    render(
      <Providers>
        <FeedbackWorkspace />
      </Providers>,
    );
    fireEvent.click(within(chips()).getByRole("button", { name: /^Filters/ }));
    const dialog = screen.getByRole("dialog", { name: "Filters" });
    fireEvent.click(within(within(dialog).getByRole("group", { name: "Rating" })).getByRole("button", { name: "2" }));
    await waitFor(() => expect(history.params().get("rating")).toBe("2"));
    fireEvent.click(within(within(screen.getByRole("dialog", { name: "Filters" })).getByRole("group", { name: "Reason" })).getByRole("button", { name: "Wrong" }));
    await waitFor(() => expect(history.params().get("reason")).toBe("wrong"));
    expect(screen.getByRole("dialog", { name: "Filters" })).toHaveTextContent("Reason and sentiment narrow the loaded page.");
  });
});
