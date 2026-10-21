/** OR-6 list UX: last-active time instead of "expired", recency groups, no empty sessions. VA-32 hint contrast. */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { ChatSessionList } from "@/components/ChatSessionList";
import type { ChatSessionSummary } from "@/lib/types";

const NOW = new Date("2026-10-21T15:30:00Z");

function summary(id: string, lastActive: Date, extra: Partial<ChatSessionSummary> = {}): ChatSessionSummary {
  return {
    id,
    title: `Chat ${id}`,
    created_at: lastActive.toISOString(),
    last_activity_at: lastActive.toISOString(),
    expires_at: lastActive.toISOString(),
    is_expired: true,
    message_count: 2,
    ...extra,
  };
}

const noop = () => undefined;
const renderList = (sessions: ChatSessionSummary[]) =>
  render(
    <ChatSessionList
      sessions={sessions}
      activeSessionId={null}
      onSelect={noop}
      onNew={noop}
      onRename={noop}
      onDelete={noop}
    />,
  );

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());

describe("ChatSessionList", () => {
  it("shows the last-active time, never 'expired', with the full time in the tooltip", () => {
    renderList([summary("a", new Date(NOW.getTime() - 2 * 3_600_000))]);
    const time = screen.getByText("2h ago");
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("dateTime", new Date(NOW.getTime() - 2 * 3_600_000).toISOString());
    expect(time.getAttribute("title")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/expired/i);
  });

  it("groups chats by recency and hides empty sessions", () => {
    const day = 86_400_000;
    renderList([
      summary("today", new Date(NOW.getTime() - 60_000)),
      summary("empty", new Date(NOW.getTime() - 120_000), { message_count: 0, title: "New chat" }),
      summary("old", new Date(NOW.getTime() - 40 * day)),
    ]);
    const groups = screen.getAllByRole("region");
    expect(groups.map((g) => g.getAttribute("aria-label"))).toEqual(["Today", "Older"]);
    expect(within(groups[0]).getByText("Chat today")).toBeInTheDocument();
    expect(within(groups[1]).getByText("Chat old")).toBeInTheDocument();
    expect(screen.queryByText("New chat", { selector: "p" })).toBeNull();
  });

  it("shows a readable hint when there are no chats (fg-muted token)", () => {
    renderList([summary("empty", NOW, { message_count: 0 })]);
    const hint = screen.getByTestId("chat-sessions-empty");
    expect(hint).toHaveTextContent("No chats yet");
    expect(hint).toHaveClass("text-fg-muted");
    expect(hint.className).not.toMatch(/text-ink\/40/);
  });
});
