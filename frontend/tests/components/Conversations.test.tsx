/**
 * F3.3: Conversations list (charts, filters, preview drawer) and
 * Conversation detail (per-turn charts, inspector, promote, `?turn=`).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import ConversationsPage from "@/app/(app)/admin/conversations/page";
import ConversationDetailPage from "@/app/(app)/admin/conversations/[id]/page";
import { callsTo, currentParams, envelope, history, mockFetch, renderPage, type Route } from "../helpers/entity-harness";
import {
  ASSISTANT_MSG,
  CHUNK_ID,
  CONV_ID,
  DOC_ID,
  IP,
  conversationDetail,
  conversationDetailBase,
  conversationRows,
  conversationStats,
} from "../helpers/entity-fixtures";

vi.mock("next/navigation", async () => (await import("../helpers/router-mock")).navigationMock());

const listRoute: Route = ["GET", /^\/admin\/conversations$/, () => ({ body: envelope(conversationRows, 25) })];
const statsRoute: Route = ["GET", /^\/admin\/conversations\/stats$/, () => ({ body: conversationStats })];
const detailRoute: Route = ["GET", new RegExp(`^/admin/conversations/${CONV_ID}$`), () => ({ body: conversationDetail })];

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Conversations list", () => {
  beforeEach(() => history.reset("/admin/conversations"));

  it("a histogram bin filters by turn count (URL + request) and the chip clears it", async () => {
    const calls = mockFetch([listRoute, statsRoute]);
    renderPage(<ConversationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "2–3: 120" }));
    await waitFor(() => expect(currentParams().get("min_turns")).toBe("2"));
    expect(currentParams().get("max_turns")).toBe("3");
    await waitFor(() => {
      const last = callsTo(calls, "/admin/conversations").at(-1);
      expect(last?.searchParams.get("min_turns")).toBe("2");
      expect(last?.searchParams.get("max_turns")).toBe("3");
    });
    fireEvent.click(screen.getByRole("button", { name: "Clear filter: 2–3 turns" }));
    await waitFor(() => expect(currentParams().get("min_turns")).toBeNull());
  });

  it("shows created and last-active times as <time> and sorts by created date", async () => {
    const calls = mockFetch([listRoute, statsRoute]);
    const { container } = renderPage(<ConversationsPage />);
    await waitFor(() => expect(container.querySelector('time[datetime="2026-10-14T09:12:44.000Z"]')).not.toBeNull());
    expect(container.querySelector('time[datetime="2026-10-14T09:31:02.000Z"]')).toHaveAttribute("title");
    fireEvent.click(screen.getByRole("columnheader", { name: /created/i }));
    await waitFor(() =>
      expect(callsTo(calls, "/admin/conversations").at(-1)?.searchParams.get("sort")).toBe("created_at_desc"),
    );
  });

  it("the open-ended bin sends only min_turns", async () => {
    const calls = mockFetch([listRoute, statsRoute]);
    renderPage(<ConversationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "10+: 10" }));
    await waitFor(() => expect(callsTo(calls, "/admin/conversations").at(-1)?.searchParams.get("min_turns")).toBe("10"));
    expect(callsTo(calls, "/admin/conversations").at(-1)?.searchParams.has("max_turns")).toBe(false);
  });

  it("an outcome segment filters by outcome and day", async () => {
    const calls = mockFetch([listRoute, statsRoute]);
    renderPage(<ConversationsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /^9 Oct, Blocked: 1$/ }));
    await waitFor(() => expect(currentParams().get("outcome")).toBe("blocked"));
    expect(currentParams().get("day")).toBe("2026-10-09");
    await waitFor(() => expect(callsTo(calls, "/admin/conversations").at(-1)?.searchParams.get("outcome")).toBe("blocked"));
    expect(screen.getByRole("button", { name: "Clear filter: Blocked on 9 Oct" })).toBeInTheDocument();
  });

  it("row click opens the preview drawer and closing it keeps the page and filters", async () => {
    history.reset(`/admin/conversations?ip=${IP}`);
    const calls = mockFetch([listRoute, statsRoute, detailRoute]);
    renderPage(<ConversationsPage />);
    await screen.findByText("Leave policy");
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(callsTo(calls, "/admin/conversations").at(-1)?.searchParams.get("offset")).toBe("10"));
    const listCalls = callsTo(calls, "/admin/conversations").length;

    fireEvent.click(screen.getByText("Leave policy"));
    await waitFor(() => expect(currentParams().get("preview")).toBe(CONV_ID));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("And sick leave?")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: /Open full view/ })).toHaveAttribute("href", `/admin/conversations/${CONV_ID}`);
    expect(within(dialog).getByRole("link", { name: "Turn 2" })).toHaveAttribute("href", `/admin/conversations/${CONV_ID}?turn=1`);

    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await waitFor(() => expect(currentParams().get("preview")).toBeNull());
    expect(currentParams().get("ip")).toBe(IP);
    // Same page, same filters: no list refetch with a different offset.
    const after = callsTo(calls, "/admin/conversations");
    expect(after.length).toBe(listCalls);
    expect(after.at(-1)?.searchParams.get("offset")).toBe("10");
    expect(after.at(-1)?.searchParams.get("ip")).toBe(IP);
  });

  it("renders the table when the stats endpoint is absent", async () => {
    mockFetch([listRoute]);
    renderPage(<ConversationsPage />);
    expect(await screen.findByText("Leave policy")).toBeInTheDocument();
    expect(await screen.findAllByText("Conversation stats are not available yet")).toHaveLength(2);
  });
});

describe("Conversation detail", () => {
  const scrollIntoView = vi.fn();
  beforeEach(() => {
    scrollIntoView.mockClear();
    Element.prototype.scrollIntoView = scrollIntoView;
  });

  it("`?turn=` scrolls to and highlights the turn", async () => {
    mockFetch([detailRoute]);
    renderPage(<ConversationDetailPage />, { url: `/admin/conversations/${CONV_ID}?turn=1` });
    const turn = await screen.findByRole("article", { name: "Turn 2" });
    expect(turn).toHaveAttribute("data-highlighted", "true");
    expect(screen.getByRole("article", { name: "Turn 1" })).not.toHaveAttribute("data-highlighted");
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
  });

  it("shows header, per-turn charts, labels, sources and feedback links", async () => {
    mockFetch([detailRoute]);
    renderPage(<ConversationDetailPage />, { url: `/admin/conversations/${CONV_ID}` });
    expect(await screen.findByRole("heading", { level: 1, name: "Leave policy" })).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("link", { name: "Conversations" })).toHaveAttribute(
      "href",
      "/admin/conversations",
    );
    expect(screen.getByRole("group", { name: "Stage latency for the selected turn" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Tokens per turn" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "leave" })[0]).toHaveAttribute("href", "/admin/analytics?intent=leave");
    expect(screen.getAllByRole("link", { name: /Leave policy.md · chunk 4/ })[0]).toHaveAttribute(
      "href",
      `/documents/${DOC_ID}?chunk=${CHUNK_ID}`,
    );
    expect(screen.getByRole("link", { name: "Open in Feedback" })).toHaveAttribute("href", `/admin/feedback?message_id=${ASSISTANT_MSG}`);
    expect(screen.getAllByRole("link", { name: "Trace" })[1]).toHaveAttribute("href", "/admin/governance?tab=Traces&request_id=req_ab12");
  });

  it("a token bar selects the turn; Inspect opens the inspector drawer", async () => {
    mockFetch([
      detailRoute,
      ["GET", /^\/admin\/usage\/queries\/req_ab12$/, () => ({ body: { request_id: "req_ab12", tokens: {}, latencies: {} } })],
    ]);
    renderPage(<ConversationDetailPage />, { url: `/admin/conversations/${CONV_ID}` });
    fireEvent.click(await screen.findByRole("button", { name: /^#2, Prompt: / }));
    await waitFor(() => expect(currentParams().get("turn")).toBe("1"));
    fireEvent.click(screen.getByRole("button", { name: "Inspect turn 2" }));
    await waitFor(() => expect(currentParams().get("inspect")).toBe("req_ab12"));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("Promote to test is permission-gated and posts the assistant message id", async () => {
    const calls = mockFetch([
      detailRoute,
      ["POST", /promote-to-test$/, () => ({ body: { status: "ok", case_id: "promoted_a5500000", query: "And sick leave?" } })],
    ]);
    const { unmount } = renderPage(<ConversationDetailPage />, { url: `/admin/conversations/${CONV_ID}` });
    fireEvent.click(await screen.findByRole("button", { name: "Promote turn 2 to test" }));
    expect(await screen.findByText("Turn 2 promoted to a test case")).toBeInTheDocument();
    expect(callsTo(calls, `/admin/feedback/${ASSISTANT_MSG}/promote-to-test`, "POST")).toHaveLength(1);
    unmount();

    mockFetch([detailRoute]);
    renderPage(<ConversationDetailPage />, { url: `/admin/conversations/${CONV_ID}`, role: "operator" });
    await screen.findByRole("article", { name: "Turn 2" });
    expect(screen.queryByRole("button", { name: /Promote turn/ })).not.toBeInTheDocument();
  });

  it("uses themed tokens and renders messages without B7.2 turn detail", async () => {
    mockFetch([["GET", new RegExp(`^/admin/conversations/${CONV_ID}$`), () => ({ body: conversationDetailBase })]]);
    const { container } = renderPage(<ConversationDetailPage />, { url: `/admin/conversations/${CONV_ID}` });
    expect(await screen.findByText("10 days of sick leave.")).toBeInTheDocument();
    expect(screen.getByText("Per-turn stage timings are not available yet")).toBeInTheDocument();
    expect(container.innerHTML).not.toMatch(/text-gray-\d/);
  });
});
