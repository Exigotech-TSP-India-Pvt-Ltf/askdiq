/**
 * Parity with the reference HTML: the page
 * renders the recorded `/admin/insights/*` responses for the HTML's own
 * dataset (49 conversations, 117 exchanges) and must show the HTML's numbers.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import fs from "fs";
import path from "path";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { queryCache } from "@/lib/query-cache";
import AnalyticsPage from "@/app/(app)/admin/analytics/page";
import { RECORDED, mockInsightsFetch, setUrl } from "./analytics-harness";

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

const FIXTURES = path.resolve(__dirname, "../fixtures/analytics");

function renderPage(search = "") {
  setUrl(search);
  return render(
    <MotionProvider>
      <AnalyticsPage />
    </MotionProvider>,
  );
}

async function kpi(key: string): Promise<string> {
  const card = await screen.findByTestId(`kpi-${key}`);
  await waitFor(() => expect(within(card).getByTestId("count-final")).toBeInTheDocument());
  return within(card).getByTestId("count-final").textContent ?? "";
}

/** Sums the first integer after `pattern` in the accessible names of marks. */
function sumLabels(root: HTMLElement, pattern: RegExp): number {
  return Array.from(root.querySelectorAll<SVGElement | HTMLElement>("[aria-label]"))
    .map((el) => pattern.exec(el.getAttribute("aria-label") ?? ""))
    .filter((m): m is RegExpExecArray => m !== null)
    .reduce((s, m) => s + Number(m[1]), 0);
}

function card(title: string): HTMLElement {
  const heading = screen.getByRole("heading", { name: title });
  const section = heading.closest("section");
  if (!section) throw new Error(`no card ${title}`);
  return section;
}

beforeEach(() => {
  queryCache.clear();
  mockInsightsFetch();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setUrl("");
});

describe("recorded fixture (derived from the HTML's __EMBED__ data)", { timeout: 15_000 }, () => {
  it("holds the reference dataset's headline numbers", () => {
    const s = RECORDED.summary;
    expect(s.conversations).toBe(49);
    expect(s.exchanges).toBe(117);
    expect(s.handled_exchanges).toBe(62);
    expect(s.substantive_questions).toBe(106);
    expect(s.handled_questions).toBe(51);
    const outcomes = Object.fromEntries(RECORDED.flow.outcomes.map((o) => [o.name, o.conversations]));
    expect(outcomes).toEqual({ Served: 11, "Partly served": 14, "Not served": 22 });
    expect(s.conversations - RECORDED.flow.total).toBe(2); // Greeting only
    expect(RECORDED.topics.items.find((t) => t.intent === "SVC")?.questions).toBe(33);
    const quality = Object.fromEntries(RECORDED.quality.items.map((q) => [q.quality, q.questions]));
    expect(quality.PART).toBe(37);
    expect(quality.OK).toBe(32);
    expect(RECORDED.dropoff.items[0]).toEqual({ issue: "JOBS", label: "No live job openings", conversations: 13 });
  });

  it("the sanitized reference HTML keeps the renderer but no data or base64", () => {
    const html = fs.readFileSync(path.join(FIXTURES, "chatbot_insights_reference.html"), "utf-8");
    expect(html).toContain("function metrics(sc)");
    expect(html).toContain("window.__EMBED__={conversations:[],exchanges:[]}");
    expect(html).not.toMatch(/;base64,/);
    expect(html).not.toMatch(/"conv_id": "C01"/);
  });
});

describe("Analytics page renders the HTML's numbers", { timeout: 15_000 }, () => {
  it("KPI strip (metrics())", async () => {
    renderPage();
    expect(await kpi("conversations")).toBe("49");
    expect(await kpi("questions")).toBe("106");
    expect(await kpi("handled")).toBe("48%");
    expect(await kpi("served")).toBe("23%");
    expect(await kpi("people")).toBe("46");
    expect(await kpi("avg")).toBe("2.3");
    expect(screen.getByText("of 117 exchanges (62 handled)")).toBeInTheDocument();
  });

  it("chart totals on the Overview tab", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "Daily volume" });
    await waitFor(() => expect(sumLabels(card("Daily volume"), /: (\d+) questions, \d+ conversations\.$/)).toBe(106));
    expect(sumLabels(card("Daily volume"), /, (\d+) conversations\.$/)).toBe(49);

    const hours = card("Questions by hour of day");
    await waitFor(() => expect(within(hours).getByRole("group", { name: /peak hour 12:00 with 21 questions/ })).toBeInTheDocument());
    expect(sumLabels(hours, /^\d+:00, (\d+) questions$/)).toBe(106);

    const flow = card("User type → outcome");
    await waitFor(() => expect(sumLabels(flow, /to Served: (\d+) conversations/)).toBe(11));
    expect(sumLabels(flow, /to Partly served: (\d+) conversations/)).toBe(14);
    expect(sumLabels(flow, /to Not served: (\d+) conversations/)).toBe(22);

    const topics = card("Question topics");
    await waitFor(() => expect(within(topics).getByRole("button", { name: "Services: 33 questions, 58% handled" })).toBeInTheDocument());
    // Top 10 first; "Show all 14" expands the list in place.
    fireEvent.click(within(topics).getByRole("button", { name: "Show all 14" }));
    expect(sumLabels(topics, /^[^:]+: (\d+) questions, \d+% handled$/)).toBe(106);

    const donut = card("How questions were answered");
    await waitFor(() => expect(within(donut).getByRole("button", { name: "Partly answered: 37 questions." })).toBeInTheDocument());
    expect(within(donut).getByRole("button", { name: "Answered correctly: 32 questions." })).toBeInTheDocument();
    expect(sumLabels(donut, /: (\d+) questions\.$/)).toBe(106);
    // Fixed slice order (donut()).
    const order = within(donut)
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label")?.split(":")[0])
      .filter((n): n is string => Boolean(n) && !n?.startsWith("View"));
    expect(order).toEqual([
      "Answered correctly",
      "Correct refusal",
      "Out of scope",
      "Partly answered",
      "No-info reply",
      "Clarifying question",
      "Off-target",
      "False action claim",
    ]);

    const drop = card("Where conversations dropped");
    await waitFor(() => expect(within(drop).getByRole("button", { name: "No live job openings: 13 conversations." })).toBeInTheDocument());
    expect(sumLabels(drop, /: (\d+) conversations\.$/)).toBe(RECORDED.dropoff.total);

    const people = card("Conversations per person");
    await waitFor(() => expect(sumLabels(people, /^P\d+: (\d+) conversation/)).toBe(49));
    expect(within(people).getAllByRole("button", { name: /^P\d+: / })).toHaveLength(46);
  });

  it("'View as table' lists the same numbers as the chart", async () => {
    renderPage("?tab=topics");
    const topics = await waitFor(() => card("Question topics"));
    fireEvent.click(await within(topics).findByRole("button", { name: "View as table" }));
    const table = within(topics).getByRole("table");
    const svc = within(table).getByText("Services").closest("tr");
    expect(svc).not.toBeNull();
    expect(within(svc as HTMLElement).getByText("33")).toBeInTheDocument();
    expect(within(svc as HTMLElement).getByText("58%")).toBeInTheDocument();
  });

  it("Conversations tab shows the first page of 49 with the HTML's columns", async () => {
    renderPage("?tab=conversations");
    expect((await screen.findAllByText("49 conversations")).length).toBeGreaterThan(0);
    const rows = await screen.findAllByTestId("conversation-row");
    expect(rows).toHaveLength(10); // the console's default page size
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent?.trim()).filter(Boolean);
    // "Details" labels the row-expand toggle column (VA-29); the rest are the HTML's columns.
    expect(headers).toEqual(["Details", "Conv", "Person", "Started", "Type", "Outcome", "Rating", "Issue", "Qs"]);
  });
});
