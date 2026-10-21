/**
 * Chat state when a reply lands in a different session (the open one was
 * deleted elsewhere): messages above it still belong to the old session, so
 * rating and deleting them must target that session (the backend 404s them
 * in the new one). A failed delete puts the pair back and rejects.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, render, waitFor } from "@testing-library/react";

const apiMock = vi.hoisted(() => ({
  listSessions: vi.fn(),
  getSession: vi.fn(),
  query: vi.fn(),
  deleteExchange: vi.fn(),
  createSession: vi.fn(),
  renameSession: vi.fn(),
  deleteSession: vi.fn(),
}));
const submitMessageFeedback = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
}));
vi.mock("@/lib/auth-context", () => ({ useAuth: () => ({ token: "t" }) }));
vi.mock("@/lib/brand-context", () => ({ useBrand: () => ({ assistantName: "AskDIQ" }) }));
vi.mock("@/lib/feedback-api", () => ({ submitMessageFeedback }));

import { ChatProvider, useChat } from "@/lib/chat-context";

type Chat = ReturnType<typeof useChat>;

function Probe({ onChat }: { onChat: (chat: Chat) => void }) {
  onChat(useChat());
  return null;
}

async function renderExpiredChat() {
  apiMock.listSessions.mockResolvedValue([{ id: "old", title: "Old", created_at: "", last_activity_at: "", expires_at: "" }]);
  apiMock.getSession.mockResolvedValue({
    id: "old",
    title: "Old",
    messages: [
      { id: "u1", role: "user", content: "Q1", created_at: "2026-10-01T10:00:00" },
      { id: "a1", role: "assistant", content: "A1", created_at: "2026-10-01T10:00:01" },
    ],
  });
  apiMock.query.mockResolvedValue({
    answer: "A2",
    session_id: "new",
    user_message_id: "u2",
    assistant_message_id: "a2",
    sources: [],
  });
  let chat!: Chat;
  render(
    <ChatProvider>
      <Probe onChat={(c) => (chat = c)} />
    </ChatProvider>,
  );
  await waitFor(() => expect(chat.messages).toHaveLength(2));
  await act(async () => {
    await chat.sendQuery("Q2");
  });
  await waitFor(() => expect(chat.activeSessionId).toBe("new"));
  return () => chat;
}

afterEach(() => vi.clearAllMocks());

describe("ChatProvider after a session rotation", () => {
  it("rates an old message in its own (expired) session", async () => {
    const chat = await renderExpiredChat();
    await act(async () => {
      await chat().submitFeedback("a1", 1);
    });
    expect(submitMessageFeedback).toHaveBeenCalledWith("t", "old", "a1", expect.objectContaining({ rating: 1 }));

    await act(async () => {
      await chat().submitFeedback("a2", 5);
    });
    expect(submitMessageFeedback).toHaveBeenLastCalledWith("t", "new", "a2", expect.objectContaining({ rating: 5 }));
  });

  it("deletes an old exchange in its own session", async () => {
    apiMock.deleteExchange.mockResolvedValue(undefined);
    const chat = await renderExpiredChat();
    await act(async () => {
      await chat().deleteExchange("a1");
    });
    expect(apiMock.deleteExchange).toHaveBeenCalledWith("t", "old", "a1");
    expect(chat().messages.map((m) => m.id)).toEqual(["u2", "a2"]);
  });

  it("restores the pair and rejects when the server delete fails", async () => {
    apiMock.deleteExchange.mockRejectedValue(new Error("Message not found"));
    const chat = await renderExpiredChat();
    let error: unknown;
    await act(async () => {
      await chat()
        .deleteExchange("a2")
        .catch((err: unknown) => {
          error = err;
        });
    });
    expect(error).toBeInstanceOf(Error);
    expect(chat().messages.map((m) => m.id)).toEqual(["u1", "a1", "u2", "a2"]);
  });
});
