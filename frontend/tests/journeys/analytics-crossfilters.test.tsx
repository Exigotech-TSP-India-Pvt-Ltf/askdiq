/**
 * Analytics cross-filters: every filter (topic, quality, issue, person,
 * outcome, user type, day, hour, visitor) deep-links, combines and clears
 * without crashing; an unknown person shows an empty state with a clear
 * action; clicking a person bubble (the reported crash) narrows the page.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { queryCache } from "@/lib/query-cache";
import AnalyticsPage from "@/app/(app)/admin/analytics/page";
import type { PersonDetail } from "@/lib/insights-api";
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

const TARGET = RECORDED.people.items[RECORDED.people.items.length - 1];

function personDetail(overrides: Partial<PersonDetail> = {}): PersonDetail {
  return {
    person: TARGET.person,
    person_id: TARGET.person_id,
    identity: "account",
    conversations: TARGET.conversations,
    sessions: TARGET.conversations + 1,
    first_seen: "2026-05-02T09:00:00",
    last_seen: "2026-06-20T17:30:00",
    visitors: [{ visitor_id: "client-203.0.113.7", sessions: 2, first_seen: "2026-05-02T09:00:00", last_seen: "2026-06-20T17:30:00" }],
    account_visitor_id: null,
    ...overrides,
  };
}

/** People narrowed to the filtered person, as the backend answers. */
function peopleFor(params: URLSearchParams) {
  const id = params.get("person");
  if (!id) return RECORDED.people;
  const items = RECORDED.people.items.filter((p) => p.person_id === id);
  return { total: items.length, items };
}

function renderPage(search = "") {
  setUrl(search);
  return render(
    <MotionProvider>
      <AnalyticsPage />
    </MotionProvider>,
  );
}

let errorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  queryCache.clear();
  errorSpy = vi.spyOn(console, "error");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  errorSpy.mockRestore();
  setUrl("");
});

function expectNoCrash() {
  expect(screen.queryByText(/could not be displayed/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Something went wrong/i)).not.toBeInTheDocument();
}

describe("person filter", () => {
  it("clicking a hovered bubble narrows the page instead of crashing it", async () => {
    mockInsightsFetch({ people: peopleFor, [`people/${TARGET.person_id}`]: personDetail() });
    renderPage("?tab=dropoff");
    const bubble = await screen.findByRole("button", { name: new RegExp(`^${TARGET.person}: `) });
    fireEvent.mouseEnter(bubble);
    fireEvent.click(bubble);
    expect(currentParams().get("person")).toBe(TARGET.person_id);
    await waitFor(() => expect(screen.getAllByRole("button", { name: /conversation\(s\), mostly/ })).toHaveLength(1));
    expectNoCrash();
  });

  it("deep-links to a person and shows the real visitor records behind it", async () => {
    const api = mockInsightsFetch({ people: peopleFor, [`people/${TARGET.person_id}`]: personDetail() });
    renderPage(`?person=${TARGET.person_id}`);
    const panel = await screen.findByTestId("person-panel");
    expect(await within(panel).findByText(`Person ${TARGET.person}`)).toBeInTheDocument();
    expect(within(panel).getByText(/signed-in account/)).toBeInTheDocument();
    const visitor = within(panel).getByRole("link", { name: /203\.0\.113\.7/ });
    expect(visitor).toHaveAttribute("href", "/admin/users/client-203.0.113.7");
    await waitFor(() => expect(api.last("summary")?.params.get("person")).toBe(TARGET.person_id));
    // The chip uses the person's label, and removing it clears the filter.
    fireEvent.click(await screen.findByRole("button", { name: `Remove filter ${TARGET.person}` }));
    expect(currentParams().get("person")).toBeNull();
    expectNoCrash();
  });

  it("explains a person without a client IP", async () => {
    mockInsightsFetch({ people: peopleFor, [`people/${TARGET.person_id}`]: personDetail({ visitors: [] }) });
    renderPage(`?person=${TARGET.person_id}`);
    expect(await screen.findByText(/created without a client IP/)).toBeInTheDocument();
  });

  it("shows an empty state with a clear action for an unknown or stale person", async () => {
    const stale = "0123456789abcdef";
    mockInsightsFetch({
      people: { total: 0, items: [] },
      summary: { ...RECORDED.summary, conversations: 0, active_conversations: 0, people: 0 },
    });
    renderPage(`?person=${stale}`);
    expect(await screen.findByText("No one matches this person link.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear person filter" }));
    expect(currentParams().get("person")).toBeNull();
    expectNoCrash();
  });
});

describe("every cross-filter deep-links, combines and clears", () => {
  const ALL =
    "?period=custom&from=2026-05-15&to=2026-05-15&user_type=Customer+%2F+prospect&outcome=Partly+served" +
    "&issue=KB_GAP&intent=SVC&quality=PART&person=d4ed3e8809100c89&hour=9&visitor=client-203.0.113.7";

  it("sends every filter to every aggregate and clears them in one action", async () => {
    const api = mockInsightsFetch({ "people/d4ed3e8809100c89": personDetail({ person_id: "d4ed3e8809100c89", person: "P01" }) });
    renderPage(ALL);
    await waitFor(() => expect(api.last("hourly")).toBeDefined());
    for (const endpoint of ["summary", "trend", "hourly", "flow", "topics", "quality", "dropoff", "people", "conversations"]) {
      await waitFor(() => {
        const p = api.last(endpoint)?.params;
        expect(p?.get("intent"), endpoint).toBe("SVC");
        expect(p?.get("quality")).toBe("PART");
        expect(p?.get("issue")).toBe("KB_GAP");
        expect(p?.get("person")).toBe("d4ed3e8809100c89");
        expect(p?.get("outcome")).toBe("Partly served");
        expect(p?.get("user_type")).toBe("Customer / prospect");
        expect(p?.get("from")).toBe("2026-05-15");
        expect(p?.get("hour")).toBe("9");
        expect(p?.get("visitor")).toBe("client-203.0.113.7");
      });
    }
    expect(screen.getByRole("button", { name: "Remove filter 09:00–10:00" })).toBeInTheDocument();
    expect(screen.getByTestId("visitor-panel")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open visitor/ })).toHaveAttribute("href", "/admin/users/client-203.0.113.7");
    expectNoCrash();

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    const left = currentParams();
    for (const key of ["period", "from", "to", "user_type", "outcome", "issue", "intent", "quality", "person", "hour", "visitor"]) {
      expect(left.get(key), key).toBeNull();
    }
  });

  it("drops an out-of-range hour and an invalid visitor id", async () => {
    const api = mockInsightsFetch();
    renderPage("?hour=24&visitor=client-<script>");
    await waitFor(() => expect(api.last("summary")).toBeDefined());
    expect(api.last("summary")?.params.get("hour")).toBeNull();
    expect(api.last("summary")?.params.get("visitor")).toBeNull();
  });

  it("an hour click toggles the hour filter", async () => {
    mockInsightsFetch();
    renderPage("?tab=trend");
    const nine = await screen.findByRole("button", { name: /^9:00, \d+ questions/ });
    fireEvent.click(nine);
    expect(currentParams().get("hour")).toBe("9");
    fireEvent.click(await screen.findByRole("button", { name: /^9:00, \d+ questions/ }));
    expect(currentParams().get("hour")).toBeNull();
  });

  it("shows a clear-filters empty state when nothing matches", async () => {
    mockInsightsFetch({ summary: { ...RECORDED.summary, conversations: 0, active_conversations: 0 } });
    renderPage("?outcome=Served&intent=SVC");
    expect(await screen.findByText("No conversations match these filters.")).toBeInTheDocument();
  });
});

describe("KPI cards open the records behind the number", () => {
  it("Conversations served opens the served conversations", async () => {
    mockInsightsFetch();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Conversations served: open the served conversations" }));
    expect(currentParams().get("tab")).toBe("conversations");
    expect(currentParams().get("outcome")).toBe("Served");
  });

  it("a person in the Conversations table filters to that person", async () => {
    mockInsightsFetch({ people: peopleFor });
    renderPage("?tab=conversations");
    const row = (await screen.findAllByTestId("conversation-row"))[0];
    const first = RECORDED.conversations.items[0];
    fireEvent.click(within(row).getByRole("button", { name: `Filter to person ${first.person}` }));
    expect(currentParams().get("person")).toBe(first.person_id);
  });
});
