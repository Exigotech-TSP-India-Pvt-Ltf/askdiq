/**
 * Visitors list and Visitor detail. Mocked fetch per
 * docs/technical/frontend/contracts/entity-stats.md; in-memory router for URL round-trips.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import VisitorsPage from "@/app/(app)/admin/visitors/page";
import VisitorDetailPage from "@/app/(app)/admin/users/[id]/page";
import { callsTo, currentParams, envelope, history, mockFetch, renderPage, type Route } from "../helpers/entity-harness";
import {
  ASSISTANT_MSG,
  CONV_ID,
  IP,
  VISITOR_ID,
  visitorDetail,
  visitorRows,
  visitorStats,
  visitorTimeline,
} from "../helpers/entity-fixtures";

vi.mock("next/navigation", async () => (await import("../helpers/router-mock")).navigationMock());

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

const listRoute: Route = ["GET", /^\/admin\/users$/, () => ({ body: envelope(visitorRows) })];
const statsRoute: Route = ["GET", /^\/admin\/visitors\/stats$/, () => ({ body: visitorStats })];

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Visitors list", () => {
  beforeEach(() => history.reset("/admin/visitors"));

  it("shows the KPI strip and charts from /admin/visitors/stats", async () => {
    mockFetch([listRoute, statsRoute]);
    renderPage(<VisitorsPage />);
    expect(await screen.findByText("120")).toBeInTheDocument();
    expect(screen.getByText("New visitors")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "New vs returning visitors per day" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Sessions by weekday and hour" })).toBeInTheDocument();
  });

  it("keeps existing data and shows empty charts when the stats endpoint is absent (404)", async () => {
    mockFetch([listRoute]);
    renderPage(<VisitorsPage />);
    expect(await screen.findByText(IP)).toBeInTheDocument();
    expect(screen.getByText("Total filtered visitors")).toBeInTheDocument();
    expect(await screen.findAllByText("Visitor stats are not available yet")).toHaveLength(2);
  });

  it("a heatmap cell filters the table through the URL and the request", async () => {
    const calls = mockFetch([listRoute, statsRoute]);
    renderPage(<VisitorsPage />);
    const cell = await screen.findByRole("button", { name: "Mon 09: 12" });
    fireEvent.click(cell);
    await waitFor(() => expect(currentParams().get("weekday")).toBe("0"));
    expect(currentParams().get("hour")).toBe("9");
    await waitFor(() => {
      const last = callsTo(calls, "/admin/users").at(-1);
      expect(last?.searchParams.get("weekday")).toBe("0");
      expect(last?.searchParams.get("hour")).toBe("9");
    });
    const chip = screen.getByRole("button", { name: /Clear filter: Mon 09:00–10:00/ });
    fireEvent.click(chip);
    await waitFor(() => expect(currentParams().get("weekday")).toBeNull());
  });

  it("shows first-seen / last-active as <time> and sorts by first seen", async () => {
    const calls = mockFetch([listRoute, statsRoute]);
    const { container } = renderPage(<VisitorsPage />);
    await screen.findByRole("link", { name: IP });
    expect(container.querySelector('time[datetime="2026-10-01T00:00:00.000Z"]')).toHaveAttribute("title");
    expect(container.querySelector('time[datetime="2026-10-14T09:31:02.000Z"]')).not.toBeNull();
    fireEvent.click(screen.getByRole("columnheader", { name: /first seen/i }));
    await waitFor(() => expect(callsTo(calls, "/admin/users").at(-1)?.searchParams.get("sort")).toBe("first_seen_desc"));
  });

  it("links the IP to the visitor detail and 'view sessions' to Usage sessions", async () => {
    mockFetch([listRoute, statsRoute]);
    renderPage(<VisitorsPage />);
    const ipLink = await screen.findByRole("link", { name: IP });
    expect(ipLink).toHaveAttribute("href", `/admin/users/${encodeURIComponent(VISITOR_ID)}`);
    expect(screen.getByRole("link", { name: `View sessions for ${IP}` })).toHaveAttribute(
      "href",
      `/admin/usage?tab=sessions&ip=${encodeURIComponent(IP)}`,
    );
  });

  it("mark as test IP is optimistic and undoable", async () => {
    const post = deferred<{ status?: number; body?: unknown }>();
    const calls = mockFetch([
      listRoute,
      statsRoute,
      ["POST", /^\/admin\/usage\/test-ips$/, () => post.promise],
      ["DELETE", /^\/admin\/usage\/test-ips\//, () => ({ body: { deleted: true, ip_address: IP } })],
    ]);
    renderPage(<VisitorsPage />);
    const row = (await screen.findByRole("link", { name: IP })).closest("tr") as HTMLElement;
    expect(within(row).getByText("Production")).toBeInTheDocument();

    fireEvent.click(within(row).getByRole("button", { name: `Mark ${IP} as test IP` }));
    // Optimistic: the badge flips before the server answers.
    await waitFor(() => expect(within(row).getByText("Test")).toBeInTheDocument());
    expect(callsTo(calls, "/admin/usage/test-ips", "POST")).toHaveLength(1);

    post.resolve({ body: { id: "t1", ip_address: IP, description: null, is_active: true, created_at: null } });
    const toast = await screen.findByText(`${IP} marked as a test IP`);
    expect(toast).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /undo/i }));
    await waitFor(() => expect(callsTo(calls, /^\/admin\/usage\/test-ips\//, "DELETE")).toHaveLength(1));
    await waitFor(() => expect(within(screen.getByRole("link", { name: IP }).closest("tr") as HTMLElement).getByText("Production")).toBeInTheDocument());
  });

  it("rolls back and shows the conflict toast on 409", async () => {
    mockFetch([listRoute, statsRoute, ["POST", /^\/admin\/usage\/test-ips$/, () => ({ status: 409, body: { detail: "conflict" } })]]);
    renderPage(<VisitorsPage />);
    const row = (await screen.findByRole("link", { name: IP })).closest("tr") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: `Mark ${IP} as test IP` }));
    expect(await screen.findByText("Changed by someone else")).toBeInTheDocument();
    await waitFor(() => expect(within(screen.getByRole("link", { name: IP }).closest("tr") as HTMLElement).getByText("Production")).toBeInTheDocument());
  });

  it("an operator sees no test-IP controls", async () => {
    mockFetch([listRoute, statsRoute]);
    renderPage(<VisitorsPage />, { role: "operator" });
    await screen.findByRole("link", { name: IP });
    expect(screen.queryByRole("button", { name: /as test IP/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Test IPs$/ })).not.toBeInTheDocument();
  });
});

describe("Visitor detail", () => {
  const detailRoutes: Route[] = [
    ["GET", /^\/admin\/users\/client-/, () => ({ body: visitorDetail })],
    [
      "GET",
      /^\/admin\/usage\/queries\/req_ab12$/,
      () => ({ body: { request_id: "req_ab12", query: "How many leave days do I get?", answer: "20", tokens: {}, latencies: {} } }),
    ],
  ];

  beforeEach(() => history.reset(`/admin/users/${encodeURIComponent(VISITOR_ID)}`));

  it("has a DashboardHeader breadcrumb, sessions linked to conversations and visitor deep links", async () => {
    mockFetch([...detailRoutes, ["GET", /\/timeline$/, () => ({ body: visitorTimeline })]]);
    renderPage(<VisitorDetailPage />);
    expect(await screen.findByRole("heading", { level: 1, name: IP })).toBeInTheDocument();
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Visitors & Conversations" })).toHaveAttribute("href", "/admin/visitors");
    expect(screen.getByRole("link", { name: /Usage sessions/ })).toHaveAttribute("href", `/admin/usage?tab=sessions&ip=${encodeURIComponent(IP)}`);
    expect(screen.getByRole("link", { name: /^Conversations$/ })).toHaveAttribute("href", `/admin/visitors?tab=conversations&ip=${encodeURIComponent(IP)}`);
    const sessionLinks = screen.getAllByRole("link", { name: "Leave policy" });
    expect(sessionLinks[0]).toHaveAttribute("href", `/admin/conversations/${CONV_ID}`);
    expect(screen.getByRole("link", { name: "Open in Feedback" })).toHaveAttribute("href", `/admin/feedback?message_id=${ASSISTANT_MSG}`);
    expect(screen.getByRole("link", { name: "View turn #1" })).toHaveAttribute("href", `/admin/conversations/${CONV_ID}?turn=0`);
  });

  it("a timeline bar opens the conversation, and a query opens the inspector drawer", async () => {
    mockFetch([...detailRoutes, ["GET", /\/timeline$/, () => ({ body: visitorTimeline })]]);
    renderPage(<VisitorDetailPage />);
    const bar = await screen.findByRole("button", { name: /^Leave policy: / });
    fireEvent.click(bar);
    expect(history.push).toHaveBeenCalledWith(`/admin/conversations/${CONV_ID}`);

    history.reset(`/admin/users/${encodeURIComponent(VISITOR_ID)}`);
    fireEvent.click(await screen.findByText("How many leave days do I get?"));
    await waitFor(() => expect(currentParams().get("inspect")).toBe("req_ab12"));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("renders sessions without the timeline endpoint (404) and says what is missing", async () => {
    mockFetch(detailRoutes);
    renderPage(<VisitorDetailPage />);
    expect(await screen.findByRole("heading", { level: 1, name: IP })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Leave policy" }).length).toBeGreaterThan(0);
    expect(await screen.findByText("Per-visitor queries are not available yet")).toBeInTheDocument();
    expect(screen.getByText("Per-visitor feedback is not available yet")).toBeInTheDocument();
  });

  it("shows a not-found state for an unknown visitor", async () => {
    mockFetch([]);
    renderPage(<VisitorDetailPage />);
    expect(await screen.findByText("Visitor not found")).toBeInTheDocument();
  });
});
