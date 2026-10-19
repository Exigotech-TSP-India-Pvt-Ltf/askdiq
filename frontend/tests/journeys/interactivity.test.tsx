/**
 * Phase F3 integration journey (planchanges4 F3.6, newplan §1 audit table).
 *
 * For every row of the audit table that PR-22 owns, each clickable element
 * produces the expected deep-link href, and the target page parses that URL
 * back into the same filter / selection state (round-trip). Targets owned by
 * other PRs (Dashboard + Chat: PR-21; Feedback `?message_id=`: PR-24;
 * Governance `?tab=Traces&request_id=`: PR-23; Analytics `?intent=`: PR-20)
 * are checked here for the href; their parsers are tested in those PRs.
 * The global range and audience carry across pages.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import VisitorsPage from "@/app/(app)/admin/visitors/page";
import VisitorDetailPage from "@/app/(app)/admin/users/[id]/page";
import ConversationsPage from "@/app/(app)/admin/visitors/page";
import ConversationDetailPage from "@/app/(app)/admin/conversations/[id]/page";
import DocumentsPage from "@/app/(app)/documents/page";
import DocumentDetailPage from "@/app/(app)/documents/[id]/page";
import UsageCostPage from "@/app/(app)/admin/usage/page";
import { AudienceProvider } from "@/lib/audience-context";
import { conversationsHref, visitorDetailHref, visitorsHref } from "@/lib/entity-routes";
import { entityHref } from "@/lib/entity-links";
import { callsTo, currentParams, envelope, history, mockFetch, renderPage, type Route } from "../helpers/entity-harness";
import {
  ASSISTANT_MSG,
  CHUNK_ID,
  CONV_ID,
  DOC_ID,
  IP,
  VISITOR_ID,
  chunks,
  citationStats,
  conversationDetail,
  conversationRows,
  conversationStats,
  documents,
  lowRated,
  visitorDetail,
  visitorRows,
  visitorStats,
  visitorTimeline,
} from "../helpers/entity-fixtures";

vi.mock("next/navigation", async () => (await import("../helpers/router-mock")).navigationMock());

const ROUTES: Route[] = [
  ["GET", /^\/admin\/users$/, () => ({ body: envelope(visitorRows) })],
  ["GET", /^\/admin\/users\/client-/, () => ({ body: visitorDetail })],
  ["GET", /^\/admin\/visitors\/stats$/, () => ({ body: visitorStats })],
  ["GET", /^\/admin\/visitors\/[^/]+\/timeline$/, () => ({ body: visitorTimeline })],
  ["GET", /^\/admin\/conversations$/, () => ({ body: envelope(conversationRows) })],
  ["GET", /^\/admin\/conversations\/stats$/, () => ({ body: conversationStats })],
  ["GET", /^\/admin\/conversations\/[^/]+$/, () => ({ body: conversationDetail })],
  ["GET", /^\/admin\/usage\/queries\/[^/]+$/, () => ({ body: { request_id: "req_ab12", tokens: {}, latencies: {} } })],
  ["GET", /^\/admin\/usage\/sessions$/, () => ({ body: envelope([]) })],
  ["GET", /^\/documents$/, () => ({ body: documents })],
  ["GET", /^\/documents\/stats\/citations$/, () => ({ body: citationStats })],
  ["GET", /^\/documents\/[^/]+\/chunks$/, () => ({ body: chunks })],
  ["GET", /^\/documents\/[^/]+\/low-rated-citations$/, () => ({ body: lowRated })],
];

let calls: ReturnType<typeof mockFetch>;

beforeEach(() => {
  calls = mockFetch(ROUTES);
  Element.prototype.scrollIntoView = vi.fn();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const lastCall = (path: string) => callsTo(calls, path).at(-1);

describe("Visitors (newplan §1 row: Users -> Visitors)", () => {
  it("heatmap cell -> /admin/users?weekday=&hour= -> same filter on load", async () => {
    renderPage(<VisitorsPage />, { url: "/admin/visitors" });
    fireEvent.click(await screen.findByRole("button", { name: "Wed 14: 5" }));
    await waitFor(() => expect(history.current()).toBe(visitorsHref({ weekday: 2, hour: 14 })));

    const href = history.current();
    calls.length = 0;
    renderPage(<VisitorsPage />, { url: href });
    expect(await screen.findByRole("button", { name: "Clear filter: Wed 14:00–15:00" })).toBeInTheDocument();
    await waitFor(() => expect(lastCall("/admin/users")?.searchParams.get("weekday")).toBe("2"));
    expect(lastCall("/admin/users")?.searchParams.get("hour")).toBe("14");
  });

  it("'View sessions' -> /admin/usage?tab=sessions&ip= -> Usage parses the IP into the Sessions request", async () => {
    renderPage(<VisitorsPage />, { url: "/admin/visitors" });
    const link = await screen.findByRole("link", { name: `View sessions for ${IP}` });
    const href = link.getAttribute("href") ?? "";
    expect(href).toBe(entityHref({ kind: "visitor", id: IP }));

    window.history.replaceState(null, "", href);
    renderPage(<UsageCostPage />, { url: href });
    await waitFor(() => expect(lastCall("/admin/usage/sessions")?.searchParams.get("ip")).toBe(IP));
    window.history.replaceState(null, "", "/");
  });

  it("row / IP -> visitor detail -> loads that visitor", async () => {
    renderPage(<VisitorsPage />, { url: "/admin/visitors" });
    const href = (await screen.findByRole("link", { name: IP })).getAttribute("href") ?? "";
    expect(href).toBe(visitorDetailHref(VISITOR_ID));
    renderPage(<VisitorDetailPage />, { url: href });
    expect(await screen.findByRole("heading", { level: 1, name: IP })).toBeInTheDocument();
    expect(lastCall(`/admin/users/${VISITOR_ID}`)).toBeDefined();
  });
});

describe("Visitor detail (row: sessions, queries, feedback, cost)", () => {
  const url = visitorDetailHref(VISITOR_ID);

  it("sessions -> conversation detail; 'Conversations' -> list filtered by IP", async () => {
    renderPage(<VisitorDetailPage />, { url });
    const session = (await screen.findAllByRole("link", { name: "Leave policy" }))[0];
    expect(session).toHaveAttribute("href", entityHref({ kind: "conversation", id: CONV_ID }));
    const convHref = screen.getByRole("link", { name: /^Conversations$/ }).getAttribute("href") ?? "";
    expect(convHref).toBe(conversationsHref({ ip: IP }));

    calls.length = 0;
    renderPage(<ConversationsPage />, { url: convHref });
    expect(await screen.findByRole("button", { name: `Clear filter: Visitor ${IP}` })).toBeInTheDocument();
    await waitFor(() => expect(lastCall("/admin/conversations")?.searchParams.get("ip")).toBe(IP));
  });

  it("query -> ?inspect= -> the inspector drawer reopens from the URL", async () => {
    renderPage(<VisitorDetailPage />, { url });
    fireEvent.click(await screen.findByText("How many leave days do I get?"));
    await waitFor(() => expect(currentParams().get("inspect")).toBe("req_ab12"));
    const href = history.current();
    renderPage(<VisitorDetailPage />, { url: href });
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("feedback -> /admin/feedback?message_id= and the turn link", async () => {
    renderPage(<VisitorDetailPage />, { url });
    expect(await screen.findByRole("link", { name: "Open in Feedback" })).toHaveAttribute(
      "href",
      entityHref({ kind: "feedback", id: ASSISTANT_MSG }),
    );
    expect(screen.getByRole("link", { name: "View turn #1" })).toHaveAttribute(
      "href",
      entityHref({ kind: "turn", id: "0", conversationId: CONV_ID }),
    );
  });
});

describe("Conversations (row: overview + drawer keeps list state)", () => {
  it("histogram bin and outcome segment URLs round-trip into chips and request params", async () => {
    renderPage(<ConversationsPage />, { url: "/admin/visitors?tab=conversations" });
    fireEvent.click(await screen.findByRole("button", { name: "1: 80 Conversations" }));
    await waitFor(() => expect(history.current()).toBe(conversationsHref({ min_turns: 1, max_turns: 1 })));
    fireEvent.click(screen.getByRole("button", { name: /^10 Oct, LLM failed: 1$/ }));
    await waitFor(() => expect(currentParams().get("outcome")).toBe("llm_failed"));

    const href = history.current();
    calls.length = 0;
    renderPage(<ConversationsPage />, { url: href });
    expect(await screen.findByRole("button", { name: "Clear filter: LLM failed on 10 Oct" })).toBeInTheDocument();
    await waitFor(() => expect(lastCall("/admin/conversations")?.searchParams.get("outcome")).toBe("llm_failed"));
    expect(lastCall("/admin/conversations")?.searchParams.get("day")).toBe("2026-10-10");
  });

  it("?preview= opens the drawer on load; Back closes it without touching filters", async () => {
    renderPage(<ConversationsPage />, { url: `/admin/visitors?tab=conversations&ip=${IP}` });
    fireEvent.click(await screen.findByText("Leave policy"));
    await waitFor(() => expect(currentParams().get("preview")).toBe(CONV_ID));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    history.back();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(currentParams().get("ip")).toBe(IP);

    renderPage(<ConversationsPage />, { url: conversationsHref({ ip: IP, preview: CONV_ID }) });
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});

describe("Conversation detail (row: per-turn inspection, feedback, labels)", () => {
  it("turn link -> ?turn= -> that turn is highlighted on load", async () => {
    const href = entityHref({ kind: "turn", id: "1", conversationId: CONV_ID });
    renderPage(<ConversationDetailPage />, { url: href });
    expect(await screen.findByRole("article", { name: "Turn 2" })).toHaveAttribute("data-highlighted", "true");
    await waitFor(() => expect(lastCall(`/admin/conversations/${CONV_ID}`)?.searchParams.get("turn")).toBe("1"));
  });

  it("trace, feedback, analytics and chunk links", async () => {
    renderPage(<ConversationDetailPage />, { url: `/admin/conversations/${CONV_ID}` });
    const turn = await screen.findByRole("article", { name: "Turn 2" });
    expect(within(turn).getByRole("link", { name: "Trace" })).toHaveAttribute("href", entityHref({ kind: "trace", id: "req_ab12" }));
    expect(within(turn).getByRole("link", { name: "Open in Feedback" })).toHaveAttribute(
      "href",
      entityHref({ kind: "feedback", id: ASSISTANT_MSG }),
    );
    expect(within(turn).getByRole("link", { name: "leave" })).toHaveAttribute("href", entityHref({ kind: "analytics", intent: "leave" }));
    expect(within(turn).getByRole("link", { name: "partial" })).toHaveAttribute("href", entityHref({ kind: "analytics", quality: "partial" }));
    const chunkHref = within(turn).getByRole("link", { name: /chunk 4/ }).getAttribute("href") ?? "";
    expect(chunkHref).toBe(entityHref({ kind: "chunk", id: CHUNK_ID, documentId: DOC_ID }));

    renderPage(<DocumentDetailPage />, { url: chunkHref });
    expect(await screen.findByRole("article", { name: "Chunk 4" })).toHaveAttribute("data-highlighted", "true");
  });
});

describe("Documents and Document detail (row: usage signal, chunks)", () => {
  it("top-cited bar -> /documents/[id] -> that document's chunks load", async () => {
    renderPage(<DocumentsPage />, { url: "/documents" });
    fireEvent.click(await screen.findByRole("button", { name: "Leave policy.md, Citations: 120" }));
    const href = entityHref({ kind: "document", id: DOC_ID });
    expect(history.push).toHaveBeenCalledWith(href);
    calls.length = 0;
    renderPage(<DocumentDetailPage />, { url: href });
    await screen.findByText("42 hits");
    expect(callsTo(calls, `/documents/${DOC_ID}/chunks`)).not.toHaveLength(0);
  });

  it("?q=&sort=hits round-trips into the search box, the sort and the request", async () => {
    renderPage(<DocumentDetailPage />, { url: `/documents/${DOC_ID}?q=annual&sort=hits` });
    expect(await screen.findByRole("searchbox", { name: "Search chunks" })).toHaveValue("annual");
    expect(screen.getByRole("combobox")).toHaveValue("hits");
    await waitFor(() => expect(lastCall(`/documents/${DOC_ID}/chunks`)?.searchParams.get("q")).toBe("annual"));
    expect(lastCall(`/documents/${DOC_ID}/chunks`)?.searchParams.get("sort")).toBe("hits");
  });

  it("low-rated citation -> Feedback and back to the chunk", async () => {
    renderPage(<DocumentDetailPage />, { url: `/documents/${DOC_ID}?tab=citations` });
    expect(await screen.findByRole("tab", { name: /Citations/ })).toHaveAttribute("aria-selected", "true");
    const list = await screen.findByRole("list", { name: "Low-rated citations" });
    expect(within(list).getByRole("link", { name: "Open in Feedback" })).toHaveAttribute(
      "href",
      entityHref({ kind: "feedback", id: ASSISTANT_MSG }),
    );
    expect(within(list).getByRole("link", { name: "View turn" })).toHaveAttribute(
      "href",
      entityHref({ kind: "turn", id: "1", conversationId: CONV_ID }),
    );
  });
});

describe("Global state carries across pages", () => {
  it("the date range follows links and drives the stats requests", async () => {
    renderPage(<VisitorsPage />, { url: "/admin/users?range=30d" });
    const ipLink = await screen.findByRole("link", { name: IP });
    expect(ipLink.getAttribute("href")).toBe(`${visitorDetailHref(VISITOR_ID)}?range=30d`);
    expect(screen.getByRole("link", { name: `View sessions for ${IP}` }).getAttribute("href")).toContain("range=30d");
    await waitFor(() => {
      const stats = lastCall("/admin/visitors/stats");
      // Day-based endpoints get inclusive UTC calendar days: 30d = 30 days, from..to.
      const fromDay = stats?.searchParams.get("from") ?? "";
      const toDay = stats?.searchParams.get("to") ?? "";
      expect(fromDay).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(toDay).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      const from = new Date(`${fromDay}T00:00:00Z`);
      const to = new Date(`${toDay}T00:00:00Z`);
      expect(Math.round((to.getTime() - from.getTime()) / 86_400_000) + 1).toBe(30);
    });

    renderPage(<VisitorDetailPage />, { url: `${visitorDetailHref(VISITOR_ID)}?range=30d` });
    const session = (await screen.findAllByRole("link", { name: "Leave policy" }))[0];
    expect(session.getAttribute("href")).toBe(`/admin/conversations/${CONV_ID}?range=30d`);
  });

  it("the audience scope applies to every entity page", async () => {
    window.localStorage.setItem("askdiq.audience_scope", "users");
    renderPage(
      <AudienceProvider>
        <VisitorsPage />
      </AudienceProvider>,
      { url: "/admin/visitors" },
    );
    await waitFor(() => expect(lastCall("/admin/users")?.searchParams.get("audience")).toBe("users"));
    expect(lastCall("/admin/visitors/stats")?.searchParams.get("audience")).toBe("users");

    renderPage(
      <AudienceProvider>
        <ConversationsPage />
      </AudienceProvider>,
      { url: "/admin/visitors?tab=conversations" },
    );
    await waitFor(() => expect(lastCall("/admin/conversations")?.searchParams.get("audience")).toBe("users"));
    expect(lastCall("/admin/conversations/stats")?.searchParams.get("audience")).toBe("users");
  });
});
