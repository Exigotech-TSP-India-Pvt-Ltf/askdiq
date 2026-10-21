/**
 * The Analytics Conversations table: the console's standard table (search,
 * filters, columns, pagination with 10 rows by default), instant row
 * expansion from the questions embedded in the page, and the next page
 * warmed while idle.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { queryCache } from "@/lib/query-cache";
import type { ConversationList, QuestionSummary } from "@/lib/insights-api";
import AnalyticsPage from "@/app/(app)/admin/analytics/page";
import { RECORDED, currentParams, mockInsightsFetch, pagedConversations, setUrl } from "./analytics-harness";

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ token: "test-token", user: null, isLoading: false, logout: () => {} }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/admin/analytics",
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("@/components/charts/HeavyCharts", async () => {
  const harness = await import("./analytics-harness");
  return { TreemapChart: harness.TreemapStandIn, SankeyChart: () => null };
});

function renderPage(search = "") {
  setUrl(search);
  return render(
    <MotionProvider>
      <AnalyticsPage />
    </MotionProvider>,
  );
}

/** The recorded rows with their questions embedded, as the bundle serves them. */
const QUESTIONS: QuestionSummary[] = RECORDED.conversation_detail.turns.map((t) => ({
  turn: t.turn,
  time: t.timestamp.slice(11, 16),
  text: t.user_message,
  intent: t.intent,
  intent_label: t.intent_label,
  substantive: t.substantive ?? true,
  handled: t.handled,
  quality: t.quality,
  quality_label: t.quality_label,
  issue: t.issue,
  issue_label: t.issue_label,
  friction: t.friction,
}));
const WITH_QUESTIONS: ConversationList = {
  ...RECORDED.conversations,
  items: RECORDED.conversations.items.map((r) => ({ ...r, questions: QUESTIONS })),
};

let api: ReturnType<typeof mockInsightsFetch>;

const pageCalls = (offset: string) =>
  api.calls.filter((c) => c.endpoint === "conversations" && c.params.get("offset") === offset);

beforeEach(() => {
  queryCache.clear();
  api = mockInsightsFetch();
  try {
    window.localStorage.clear();
  } catch {
    // storage unavailable
  }
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setUrl("");
});

describe("Conversations table", { timeout: 15_000 }, () => {
  it("shows 10 rows by default, x–y of N, and pages through the server", async () => {
    renderPage("?tab=conversations");
    expect(await screen.findAllByTestId("conversation-row")).toHaveLength(10);
    const nav = screen.getByRole("navigation", { name: "Pagination Navigation" });
    expect(nav.textContent).toContain("Showing 1–10 of 49 entries");
    expect(within(nav).getByRole("combobox")).toHaveValue("10");
    fireEvent.click(within(nav).getByRole("button", { name: "2" }));
    expect(currentParams().get("page")).toBe("2");
    await waitFor(() => expect(nav.textContent).toContain("Showing 11–20 of 49 entries"));
    expect(pageCalls("10").length).toBeGreaterThan(0);
    fireEvent.change(within(nav).getByRole("combobox"), { target: { value: "25" } });
    expect(currentParams().get("limit")).toBe("25");
    expect(currentParams().get("page")).toBeNull();
  });

  it("warms the next page (with its questions) while idle", async () => {
    renderPage("?tab=conversations");
    await screen.findAllByTestId("conversation-row");
    await waitFor(() => expect(pageCalls("10").length).toBe(1));
    // The page did not move: the request only warmed the cache.
    expect(currentParams().get("page")).toBeNull();
  });

  it("searches on the server (shared with the filter bar)", async () => {
    renderPage("?tab=conversations");
    await screen.findAllByTestId("conversation-row");
    fireEvent.change(screen.getByPlaceholderText("Search id, person, issue or question…"), { target: { value: "jobs" } });
    await waitFor(() => expect(currentParams().get("q")).toBe("jobs"));
    await waitFor(() => expect(api.last("conversations")?.params.get("q")).toBe("jobs"));
    // The page filter bar's search shows the same query.
    expect(screen.getByLabelText("Search")).toHaveValue("jobs");
  });

  it("filters by Type, Outcome, Rating and Issue through the page's URL filters", async () => {
    renderPage("?tab=conversations");
    await screen.findAllByTestId("conversation-row");
    fireEvent.click(screen.getByRole("button", { name: /^Filters/ }));
    const panel = screen.getByRole("dialog", { name: "Filter Options" });
    for (const label of ["Type", "Outcome", "Rating", "Issue"]) expect(within(panel).getByText(label)).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "Served" }));
    expect(currentParams().get("outcome")).toBe("Served");
    await waitFor(() => expect(api.last("conversations")?.params.get("outcome")).toBe("Served"));
    // The page filter bar's Outcome control is the same filter.
    expect(screen.getByLabelText("Outcome")).toHaveValue("Served");
    fireEvent.click(within(panel).getByRole("button", { name: "No live job openings" }));
    expect(currentParams().get("issue")).toBe("JOBS");
  });

  it("sorts by any column on the server", async () => {
    renderPage("?tab=conversations");
    await screen.findAllByTestId("conversation-row");
    fireEvent.click(screen.getByRole("columnheader", { name: /Rating/ }));
    expect(currentParams().get("sort")).toBe("experience_rating");
    await waitFor(() => expect(api.last("conversations")?.params.get("sort")).toBe("experience_rating"));
    expect(screen.getByRole("columnheader", { name: /Rating/ })).toHaveAttribute("aria-sort", "descending");
  });

  it("shows an empty state when nothing matches", async () => {
    queryCache.clear();
    api = mockInsightsFetch({ conversations: { ...RECORDED.conversations, items: [], total: 0 } });
    renderPage("?tab=conversations&q=zzz");
    expect(await screen.findByText("No conversations match this search or these filters")).toBeInTheDocument();
  });
});

describe("instant row expansion", { timeout: 15_000 }, () => {
  it("renders the embedded questions on click with no loading state and no request", async () => {
    queryCache.clear();
    api = mockInsightsFetch({ conversations: (params: URLSearchParams) => pagedConversations(params, WITH_QUESTIONS) });
    renderPage("?tab=conversations");
    const rows = await screen.findAllByTestId("conversation-row");
    const toggle = within(rows[0]).getByRole("button", { name: /^View questions for/ });
    fireEvent.click(toggle);
    // Synchronous: the questions are on screen in the same render as the click.
    const list = screen.getByRole("list", { name: "Questions in this conversation" });
    expect(screen.queryByText("Loading questions…")).not.toBeInTheDocument();
    expect(within(list).getAllByRole("listitem")).toHaveLength(QUESTIONS.length);
    expect(within(list).getByText(QUESTIONS[0].text)).toBeInTheDocument();
    expect(api.last("conversation_detail")).toBeUndefined();
  });

  it("the next page's rows expand instantly too (warmed with their questions)", async () => {
    queryCache.clear();
    api = mockInsightsFetch({ conversations: (params: URLSearchParams) => pagedConversations(params, WITH_QUESTIONS) });
    renderPage("?tab=conversations");
    await screen.findAllByTestId("conversation-row");
    await waitFor(() => expect(pageCalls("10").length).toBe(1));
    const nav = screen.getByRole("navigation", { name: "Pagination Navigation" });
    fireEvent.click(within(nav).getByRole("button", { name: "2" }));
    await waitFor(() => expect(nav.textContent).toContain("Showing 11–20 of 49 entries"));
    expect(pageCalls("10")).toHaveLength(1); // served from the warmed cache
    const rows = screen.getAllByTestId("conversation-row");
    fireEvent.click(within(rows[0]).getByRole("button", { name: /^View questions for/ }));
    expect(screen.getByRole("list", { name: "Questions in this conversation" })).toBeInTheDocument();
    expect(api.last("conversation_detail")).toBeUndefined();
  });
});
