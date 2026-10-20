/**
 * Analytics journeys: filter -> topic click ->
 * Conversations filtered through the URL -> row link; the trend's setDay()
 * toggle; per-turn expand by keyboard; the pending-classification state.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { queryCache } from "@/lib/query-cache";
import AnalyticsPage from "@/app/(app)/admin/analytics/page";
import {
  DEFAULT_STATE,
  parseAnalyticsState,
  serializeAnalyticsState,
  type AnalyticsState,
} from "@/components/admin/analytics/url-state";
import { RECORDED, currentParams, mockInsightsFetch, setUrl } from "./analytics-harness";

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

let api: ReturnType<typeof mockInsightsFetch>;

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

describe("URL state", () => {
  it("round-trips every field", () => {
    const state: AnalyticsState = {
      tab: "conversations",
      period: "custom",
      from: "2026-05-15",
      to: "2026-06-30",
      user_type: "Customer / prospect",
      outcome: "Partly served",
      rating: "Dropped",
      issue: "KB_GAP",
      intent: "SVC",
      quality: "PART",
      person: "0123456789abcdef",
      visitor: "client-203.0.113.9",
      hour: 14,
      q: "cloud & security",
      sort: "exchanges",
      dir: "asc",
      page: 3,
      limit: 50,
    };
    expect(parseAnalyticsState(`?${serializeAnalyticsState(state)}`)).toEqual(state);
    expect(serializeAnalyticsState(DEFAULT_STATE)).toBe("");
  });

  it("drops invalid or injected values (allow-lists)", () => {
    const parsed = parseAnalyticsState(
      "?tab=admin&period=custom&from=2026-02-31x&to=2026-01-01&outcome=Hacked&rating=1&intent=svc;drop&quality=%27OR%201&person=P01&sort=password&dir=up&page=-2&limit=1000&issue=KB_GAP",
    );
    expect(parsed).toEqual({ ...DEFAULT_STATE, issue: "KB_GAP" });
  });

  it("keeps parameters it does not own (audience)", () => {
    expect(serializeAnalyticsState({ ...DEFAULT_STATE, intent: "SVC" }, "audience=users")).toBe("audience=users&intent=SVC");
  });
});

describe("filter -> topic -> conversations journey", () => {
  it("filters, cross-filters by topic, and lands on a filtered Conversations table with row links", async () => {
    renderPage("?tab=topics");
    const outcome = await screen.findByLabelText("Outcome");
    fireEvent.change(outcome, { target: { value: "Not served" } });
    expect(currentParams().get("outcome")).toBe("Not served");
    await waitFor(() => expect(api.last("topics")?.params.get("outcome")).toBe("Not served"));

    fireEvent.click(await screen.findByRole("button", { name: /^Services: 33/ }));
    expect(currentParams().get("intent")).toBe("SVC");
    expect(screen.getByRole("button", { name: "Remove filter Services" })).toBeInTheDocument();
    await waitFor(() => expect(api.last("summary")?.params.get("intent")).toBe("SVC"));

    fireEvent.click(screen.getByRole("button", { name: /View conversations/ }));
    expect(currentParams().get("tab")).toBe("conversations");
    await waitFor(() => {
      // The page itself (the next one is warmed too, at offset 10).
      const call = [...api.calls].reverse().find((c) => c.endpoint === "conversations" && c.params.get("offset") === "0");
      expect(call?.params.get("intent")).toBe("SVC");
      expect(call?.params.get("outcome")).toBe("Not served");
      expect(call?.params.get("sort")).toBe("start");
      expect(call?.params.get("dir")).toBe("desc");
      expect(call?.params.get("limit")).toBe("10");
      expect(call?.params.get("offset")).toBe("0");
    });

    const rows = await screen.findAllByTestId("conversation-row");
    const first = RECORDED.conversations.items[0];
    const link = within(rows[0]).getByRole("link", { name: `Open conversation ${first.conv_id.split("-")[0]}` });
    expect(link).toHaveAttribute("href", `/admin/conversations/${first.conv_id}`);

    // Removing the chip clears the cross-filter from the URL and the request.
    fireEvent.click(screen.getByRole("button", { name: "Remove filter Services" }));
    expect(currentParams().get("intent")).toBeNull();
    await waitFor(() => expect(api.last("conversations")?.params.get("intent")).toBeNull());
  }, 20_000); // first journey of the file pays the cold render cost

  it("drop-off click filters by issue code", async () => {
    renderPage("?tab=dropoff");
    fireEvent.click(await screen.findByRole("button", { name: "No live job openings: 13 conversations." }));
    expect(currentParams().get("issue")).toBe("JOBS");
    expect(screen.getByRole("button", { name: "Remove filter No live job openings" })).toBeInTheDocument();
  });

  it("sorting uses the allow-listed column and resets to page 1", async () => {
    renderPage("?tab=conversations&page=2");
    fireEvent.click(await screen.findByRole("columnheader", { name: /Qs/ }));
    expect(currentParams().get("sort")).toBe("exchanges");
    expect(currentParams().get("dir")).toBeNull(); // desc is the default
    expect(currentParams().get("page")).toBeNull();
    await waitFor(() => expect(api.last("conversations")?.params.get("sort")).toBe("exchanges"));
    expect(screen.getByRole("columnheader", { name: /Qs/ })).toHaveAttribute("aria-sort", "descending");
  });
});

describe("trend setDay() toggle", () => {
  it("click selects from = to = day; clicking the same day again clears it", async () => {
    const { container } = renderPage("?tab=trend");
    await screen.findByRole("heading", { name: "Daily volume" });
    const day = await waitFor(() => {
      const el = container.querySelector<SVGRectElement>('[data-day="2026-05-15"]');
      expect(el).not.toBeNull();
      return el as SVGRectElement;
    });
    fireEvent.click(day);
    expect(currentParams().get("period")).toBe("custom");
    expect(currentParams().get("from")).toBe("2026-05-15");
    expect(currentParams().get("to")).toBe("2026-05-15");
    await waitFor(() => expect(api.last("trend")?.params.get("from")).toBe("2026-05-15"));
    // The range is shown by the header picker, not as a filter chip.
    expect(screen.queryByRole("button", { name: /^Remove filter 15 May/ })).not.toBeInTheDocument();

    const again = container.querySelector<SVGRectElement>('[data-day="2026-05-15"]');
    expect(again).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(again as SVGRectElement);
    expect(currentParams().get("period")).toBeNull();
    expect(currentParams().get("from")).toBeNull();
    // The full-period response is still cached (fresh), so no refetch is needed.
    await waitFor(() =>
      expect(container.querySelector('[data-day="2026-05-15"]')).toHaveAttribute("aria-pressed", "false"),
    );
  });

  it("Enter on a focused day does the same (keyboard)", async () => {
    const { container } = renderPage("?tab=trend");
    const day = await waitFor(() => {
      const el = container.querySelector<SVGRectElement>('[data-day="2026-05-16"]');
      expect(el).not.toBeNull();
      return el as SVGRectElement;
    });
    day.focus();
    fireEvent.keyDown(day, { key: "Enter" });
    expect(currentParams().get("from")).toBe("2026-05-16");
  });
});

describe("Conversations per-turn expand", () => {
  it("rows without embedded questions fetch the detail; keyboard expand and collapse", async () => {
    const user = userEvent.setup();
    renderPage("?tab=conversations");
    const rows = await screen.findAllByTestId("conversation-row");
    const toggle = within(rows[0]).getByRole("button", { name: /^View questions for/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    toggle.focus();
    await user.keyboard("{Enter}");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const list = await screen.findByRole("list", { name: "Questions in this conversation" });
    const turns = RECORDED.conversation_detail.turns;
    expect(within(list).getAllByRole("listitem")).toHaveLength(turns.length);
    expect(within(list).getByText(turns[0].user_message)).toBeInTheDocument();
    expect(api.last("conversation_detail")).toBeDefined();
    await user.keyboard(" ");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await waitFor(() => expect(screen.queryByRole("list", { name: "Questions in this conversation" })).not.toBeInTheDocument());
  });
});

describe("pending classification", () => {
  it("shows 'N turns pending classification' with progress but still renders the charts when nothing is labelled yet", async () => {
    queryCache.clear();
    api = mockInsightsFetch({
      summary: { ...RECORDED.summary, conversations: 0, substantive_questions: 0, pending_classification: 12 },
      pending: { pending: 12, labelled: 3, total: 15, progress_pct: 20 },
    });
    renderPage();
    expect(await screen.findByText("12 turns pending classification.")).toBeInTheDocument();
    expect(screen.getByText(/charts fill in as turns are labelled/)).toBeInTheDocument();
    // Not a full-page empty state: the layout and the table stay on screen.
    expect(screen.getByRole("heading", { name: "Daily volume" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Where conversations dropped" })).toBeInTheDocument();
    const bar = screen.getByRole("progressbar", { name: "Classification progress" });
    expect(bar).toHaveAttribute("aria-valuenow", "20");
    expect(bar).toHaveAttribute("aria-valuetext", "3 of 15 turns labelled");
  });

  it("shows a banner while a backlog is still being classified", async () => {
    queryCache.clear();
    api = mockInsightsFetch({ pending: { pending: 5, labelled: 112, total: 117, progress_pct: 95.7 } });
    renderPage();
    expect(await screen.findByText("5 turns pending classification.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Daily volume" })).toBeInTheDocument();
  });
});

describe("Guide tab removed", () => {
  it("has no Guide tab and ?tab=guide falls back to Overview", async () => {
    renderPage("?tab=guide");
    expect(await screen.findByRole("heading", { name: "Daily volume" })).toBeInTheDocument();
    const bar = screen.getByRole("tablist", { name: "Analytics sections" });
    const tabs = within(bar).getAllByRole("tab").map((t) => t.textContent?.trim());
    expect(tabs).toEqual(["Overview", "Trend", "Flow", "Topics", "Drop-off", "Conversations"]);
    expect(within(bar).getByRole("tab", { name: /Overview/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText(/Step 1 of/)).not.toBeInTheDocument();
  });
});
