/**
 * Conversation preview drawer: header chips, chat thread with the full
 * answer (collapsible), per-turn footer, permission-gated trace link,
 * keyboard navigation and the cached (no loading state) render.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ConversationPreviewDrawer } from "@/components/admin/entity/ConversationPreviewDrawer";
import { estimateLines, LONG_ANSWER_LINES } from "@/components/admin/entity/ConversationThread";
import { AuthProvider } from "@/lib/auth-context";
import { conversationKey } from "@/lib/audience-data";
import type { ConversationDetail } from "@/lib/entity-stats-api";
import { queryCache } from "@/lib/query-cache";
import { PermissionsProvider } from "@/lib/use-can";
import type { Permission } from "@/lib/permissions";
import { makeMe } from "../helpers/auth";
import { callsTo, history, mockFetch } from "../helpers/entity-harness";
import { ASSISTANT_MSG, CHUNK_ID, CONV_ID, DOC_ID, IP, conversationDetail } from "../helpers/entity-fixtures";

vi.mock("next/navigation", async () => (await import("../helpers/router-mock")).navigationMock());

const LONG_ANSWER = Array.from({ length: 20 }, (_, i) => `Line ${i + 1} of the Essential Eight answer.`).join("\n");

const withLongAnswer: ConversationDetail = {
  ...conversationDetail,
  messages: conversationDetail.messages.map((m) => (m.id === ASSISTANT_MSG ? { ...m, content: LONG_ANSWER } : m)),
};

interface Options {
  /** `null`: nothing cached. */
  data?: ConversationDetail | null;
  permissions?: Permission[];
  onPrev?: () => void;
  onNext?: () => void;
  onClose?: () => void;
}

function renderDrawer({ data = conversationDetail, permissions, onPrev, onNext, onClose = () => undefined }: Options = {}) {
  queryCache.clear();
  if (data) queryCache.setData(conversationKey(CONV_ID), data);
  const drawer = (
    <ConversationPreviewDrawer
      token="session"
      conversationId={CONV_ID}
      onClose={onClose}
      onPrev={onPrev}
      onNext={onNext}
      audience="users"
    />
  );
  return render(
    <AuthProvider initialMe={makeMe("superadmin")}>
      {permissions ? <PermissionsProvider permissions={permissions}>{drawer}</PermissionsProvider> : drawer}
    </AuthProvider>,
  );
}

beforeEach(() => history.reset("/admin/visitors?tab=conversations"));
afterEach(() => vi.unstubAllGlobals());

describe("ConversationPreviewDrawer", () => {
  it("renders from cache with no loading state and no request", async () => {
    const calls = mockFetch([]);
    renderDrawer();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("status", { name: "Loading conversation" })).not.toBeInTheDocument();
    expect(within(dialog).getByText("And sick leave?")).toBeInTheDocument();
    expect(callsTo(calls, `/admin/conversations/${CONV_ID}`)).toHaveLength(0);
  });

  it("header: title, visitor link, audience, started, duration, turns, outcome, rating, tokens and cost", async () => {
    mockFetch([]);
    renderDrawer();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Leave policy" })).toBeInTheDocument();
    const chips = within(dialog).getByRole("list", { name: "Conversation summary" });
    expect(within(chips).getByRole("link", { name: IP })).toHaveAttribute(
      "href",
      expect.stringContaining(`/admin/users/client-${IP}`),
    );
    expect(within(chips).getByText("User")).toBeInTheDocument();
    expect(chips.querySelector('time[datetime="2026-10-14T09:12:44.000Z"]')).toHaveAttribute("title");
    expect(within(chips).getByText("18 min")).toBeInTheDocument();
    expect(within(chips).getByText("2 turns")).toBeInTheDocument();
    expect(within(chips).getByText("Answered ×2")).toBeInTheDocument();
    expect(within(chips).getByLabelText("1 thumbs down")).toBeInTheDocument();
    expect(within(chips).getByText("5,600 tokens")).toBeInTheDocument();
    expect(within(chips).getByText("$0.0062")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Copy conversation id" })).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: /Open full view/ })).toHaveAttribute("href", `/admin/conversations/${CONV_ID}`);
  });

  it("shows the full answer (no ellipsis) collapsed past the line budget, with Show more", async () => {
    mockFetch([]);
    expect(estimateLines(LONG_ANSWER)).toBeGreaterThan(LONG_ANSWER_LINES);
    renderDrawer({ data: withLongAnswer });
    const dialog = await screen.findByRole("dialog");
    const turn = within(dialog).getByRole("listitem", { name: "Turn 2" });
    expect(within(turn).getByText("Line 20 of the Essential Eight answer.")).toBeInTheDocument();
    expect(turn.textContent).not.toContain("…");
    const more = within(turn).getByRole("button", { name: "Show more" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    expect(turn.querySelector("[data-collapsed]")).not.toBeNull();
    fireEvent.click(more);
    expect(within(turn).getByRole("button", { name: "Show less" })).toHaveAttribute("aria-expanded", "true");
    expect(turn.querySelector("[data-collapsed]")).toBeNull();
    // A short answer has no toggle.
    const first = within(dialog).getByRole("listitem", { name: "Turn 1" });
    expect(within(first).queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();
  });

  it("turn footer: status, latency, tokens, cost, source pills, rating and reason, trace link", async () => {
    mockFetch([]);
    renderDrawer();
    const dialog = await screen.findByRole("dialog");
    const turn = within(dialog).getByRole("listitem", { name: "Turn 2" });
    const footer = within(turn).getByTestId("turn-footer");
    expect(within(footer).getByText("Answered")).toBeInTheDocument();
    expect(within(footer).getByText("1.79 s")).toBeInTheDocument();
    expect(within(footer).getByText("2,800 tokens")).toBeInTheDocument();
    expect(within(footer).getByText("$0.0031")).toBeInTheDocument();
    expect(within(footer).getByRole("link", { name: "Leave policy.md" })).toHaveAttribute(
      "href",
      `/documents/${DOC_ID}?chunk=${CHUNK_ID}`,
    );
    const feedback = within(footer).getByTestId("turn-feedback");
    expect(within(feedback).getByLabelText("Thumbs down")).toBeInTheDocument();
    expect(within(feedback).getByText("wrong")).toBeInTheDocument();
    expect(within(feedback).getByText("“Out of date”")).toBeInTheDocument();
    expect(within(footer).getByRole("link", { name: "View trace" })).toHaveAttribute(
      "href",
      expect.stringContaining("request_id=req_ab12"),
    );
    expect(within(turn).getByRole("link", { name: "Turn 2" })).toHaveAttribute("href", `/admin/conversations/${CONV_ID}?turn=1`);
  });

  it("hides View trace without dashboard:read", async () => {
    mockFetch([]);
    renderDrawer({ permissions: [] });
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("And sick leave?")).toBeInTheDocument();
    expect(within(dialog).queryByRole("link", { name: "View trace" })).not.toBeInTheDocument();
  });

  it("↑/↓ and k/j move between conversations; Esc closes", async () => {
    mockFetch([]);
    const onPrev = vi.fn();
    const onNext = vi.fn();
    const onClose = vi.fn();
    renderDrawer({ onPrev, onNext, onClose });
    await screen.findByRole("dialog");
    const target = () => document.activeElement ?? document.body;
    fireEvent.keyDown(target(), { key: "ArrowDown" });
    expect(onNext).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(target(), { key: "ArrowUp" });
    expect(onPrev).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(target(), { key: "j" });
    fireEvent.keyDown(target(), { key: "k" });
    expect(onNext).toHaveBeenCalledTimes(2);
    expect(onPrev).toHaveBeenCalledTimes(2);
    // Modified keys are left alone.
    fireEvent.keyDown(target(), { key: "ArrowDown", altKey: true });
    expect(onNext).toHaveBeenCalledTimes(2);
    fireEvent.keyDown(target(), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("flags a message the batch shortened, with a link to the full view", async () => {
    mockFetch([]);
    renderDrawer({
      data: {
        ...conversationDetail,
        messages: conversationDetail.messages.map((m) =>
          m.id === ASSISTANT_MSG ? { ...m, content_truncated: true, content_length: 9000 } : m,
        ),
      },
    });
    const dialog = await screen.findByRole("dialog");
    const turn = within(dialog).getByRole("listitem", { name: "Turn 2" });
    expect(within(turn).getByText(/Long message shortened in the preview/)).toBeInTheDocument();
  });

  it("shows the skeleton only when nothing is cached, then the thread", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    mockFetch([
      ["GET", new RegExp(`^/admin/conversations/${CONV_ID}$`), async () => gate.then(() => ({ body: conversationDetail }))],
    ]);
    renderDrawer({ data: null });
    expect(await screen.findByRole("status", { name: "Loading conversation" })).toBeInTheDocument();
    release();
    expect(await screen.findByText("And sick leave?")).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "Loading conversation" })).not.toBeInTheDocument();
  });

  it("error state retries; empty state when there are no turns", async () => {
    let fail = true;
    mockFetch([
      [
        "GET",
        new RegExp(`^/admin/conversations/${CONV_ID}$`),
        () => (fail ? { status: 500, body: { detail: "boom" } } : { body: { ...conversationDetail, messages: [], turns: [] } }),
      ],
    ]);
    renderDrawer({ data: null });
    const retry = await screen.findByRole("button", { name: /try again|retry/i });
    fail = false;
    fireEvent.click(retry);
    await waitFor(() => expect(screen.getByText("No turns in this conversation")).toBeInTheDocument());
  });
});
