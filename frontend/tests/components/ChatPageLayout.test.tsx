/**
 * /chat on a phone: the session list is a drawer closed by default (it used
 * to be a fixed 256 px column), opened with "Chats" and closed after a pick.
 */
import React from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";

const selectSession = vi.fn().mockResolvedValue(undefined);
vi.mock("@/lib/chat-context", () => ({
  useChat: () => ({
    sessions: [{ id: "s1", title: "First chat", created_at: "", last_activity_at: "", expires_at: "" }],
    activeSessionId: null,
    selectSession,
    startNewSession: vi.fn(),
    renameSession: vi.fn(),
    removeSession: vi.fn(),
  }),
}));
vi.mock("@/components/chat/ChatHeader", () => ({ ChatHeader: () => null }));
vi.mock("@/components/chat/ChatConversation", () => ({ ChatConversation: () => null }));
vi.mock("@/components/chat/use-reported-action", () => ({
  useReportedAction:
    () =>
    <A extends unknown[]>(_title: string, fn: (...args: A) => Promise<void>) =>
      fn,
}));

import ChatPage from "@/app/(app)/chat/page";

function listRoot(): HTMLElement {
  return screen.getByRole("button", { name: /new chat/i }).closest("div.flex-col") as HTMLElement;
}

describe("Chat page layout on phones", () => {
  it("keeps the history closed until Chats is pressed, and closes it after a pick", () => {
    render(<ChatPage />);
    expect(listRoot().className).toMatch(/(^|\s)hidden(\s|$)/);
    expect(listRoot().className).toContain("md:flex");

    const toggle = screen.getByRole("button", { name: "Chats (1)" });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(listRoot().className).not.toMatch(/(^|\s)hidden(\s|$)/);

    fireEvent.click(screen.getByText("First chat"));
    expect(selectSession).toHaveBeenCalledWith("s1");
    expect(listRoot().className).toMatch(/(^|\s)hidden(\s|$)/);
  });
});
