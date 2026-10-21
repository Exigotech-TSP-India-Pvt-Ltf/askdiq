/**
 * OR-6 / D1 in ChatProvider:
 * - "New chat" is local (no request); the first send carries `new_session`
 *   and the returned session becomes active (one list entry after it).
 * - An opened old chat sends its own id and stays the active session.
 * - After a failed load the next send starts a NEW session.
 * - `settled` stays false while the last chat is resumed on first load.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, render, waitFor } from "@testing-library/react";
import { queryCache } from "@/lib/query-cache";

const apiMock = vi.hoisted(() => ({
  listSessions: vi.fn(),
  getSession: vi.fn(),
  query: vi.fn(),
  deleteExchange: vi.fn(),
  createSession: vi.fn(),
  renameSession: vi.fn(),
  deleteSession: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
}));
vi.mock("@/lib/auth-context", () => ({ useAuth: () => ({ token: "t" }) }));
vi.mock("@/lib/brand-context", () => ({ useBrand: () => ({ assistantName: "AskDIQ" }) }));
vi.mock("@/lib/feedback-api", () => ({ submitMessageFeedback: vi.fn() }));

import { ChatProvider, useChat } from "@/lib/chat-context";

type Chat = ReturnType<typeof useChat>;

const OLD = {
  id: "old",
  title: "Old chat",
  created_at: "2026-10-01T10:00:00",
  last_activity_at: "2026-10-01T10:05:00",
  expires_at: "2026-10-01T11:05:00",
  is_expired: true,
  message_count: 2,
};
const OLD_DETAIL = {
  ...OLD,
  messages: [
    { id: "u1", role: "user", content: "Q1", created_at: "2026-10-01T10:00:00" },
    { id: "a1", role: "assistant", content: "A1", created_at: "2026-10-01T10:00:01" },
  ],
};

function answer(sessionId: string, n: number) {
  return {
    answer: `A${n}`,
    session_id: sessionId,
    session_title: "T",
    user_message_id: `u${n}`,
    assistant_message_id: `a${n}`,
    sources: [],
  };
}

function Probe({ onChat }: { onChat: (chat: Chat) => void }) {
  onChat(useChat());
  return null;
}

function mount() {
  let chat!: Chat;
  render(
    <ChatProvider>
      <Probe onChat={(c) => (chat = c)} />
    </ChatProvider>,
  );
  return () => chat;
}

afterEach(() => {
  vi.clearAllMocks();
  queryCache.clear();
});

describe("ChatProvider session lifecycle", () => {
  it("New chat makes no request; the first send creates the session once", async () => {
    apiMock.listSessions.mockResolvedValue([OLD]);
    apiMock.getSession.mockResolvedValue(OLD_DETAIL);
    const chat = mount();
    await waitFor(() => expect(chat().activeSessionId).toBe("old"));
    const callsBefore = Object.values(apiMock).reduce((n, fn) => n + fn.mock.calls.length, 0);

    for (let i = 0; i < 3; i++) {
      await act(async () => {
        await chat().startNewSession();
      });
    }
    const callsAfter = Object.values(apiMock).reduce((n, fn) => n + fn.mock.calls.length, 0);
    expect(callsAfter).toBe(callsBefore);
    expect(apiMock.createSession).not.toHaveBeenCalled();
    expect(chat().activeSessionId).toBeNull();
    expect(chat().messages).toEqual([]);
    expect(chat().settled).toBe(true);

    apiMock.query.mockResolvedValueOnce(answer("fresh", 2)).mockResolvedValueOnce(answer("fresh", 3));
    apiMock.listSessions.mockResolvedValue([{ ...OLD, id: "fresh", title: "Q2" }, OLD]);
    await act(async () => {
      await chat().sendQuery("Q2");
    });
    expect(apiMock.query).toHaveBeenLastCalledWith("t", expect.objectContaining({ session_id: null, new_session: true }));
    await waitFor(() => expect(chat().activeSessionId).toBe("fresh"));
    await waitFor(() => expect(chat().sessions.map((s) => s.id)).toEqual(["fresh", "old"]));

    await act(async () => {
      await chat().sendQuery("Q3");
    });
    expect(apiMock.query).toHaveBeenLastCalledWith("t", expect.objectContaining({ session_id: "fresh", new_session: false }));
  });

  it("sending from an opened old chat resumes it by id and keeps it active", async () => {
    apiMock.listSessions.mockResolvedValue([OLD]);
    apiMock.getSession.mockResolvedValue(OLD_DETAIL);
    apiMock.query.mockResolvedValue(answer("old", 2));
    const chat = mount();
    await waitFor(() => expect(chat().messages).toHaveLength(2));

    await act(async () => {
      await chat().sendQuery("Q2");
    });
    expect(apiMock.query).toHaveBeenCalledWith("t", expect.objectContaining({ session_id: "old", new_session: false }));
    expect(chat().activeSessionId).toBe("old");
    expect(chat().messages.map((m) => m.id)).toEqual(["u1", "a1", "u2", "a2"]);
  });

  it("after a failed load the next send starts a new session", async () => {
    apiMock.listSessions.mockResolvedValue([OLD]);
    apiMock.getSession.mockRejectedValue(new Error("gone"));
    apiMock.query.mockResolvedValue(answer("fresh", 2));
    const chat = mount();
    await waitFor(() => expect(apiMock.getSession).toHaveBeenCalled());
    await waitFor(() => expect(chat().settled).toBe(true));
    expect(chat().activeSessionId).toBeNull();

    await act(async () => {
      await chat().sendQuery("Q2");
    });
    expect(apiMock.query).toHaveBeenCalledWith("t", expect.objectContaining({ session_id: null, new_session: true }));
  });

  it("is not settled until the resumed chat has loaded", async () => {
    apiMock.listSessions.mockResolvedValue([OLD]);
    let release!: (v: unknown) => void;
    apiMock.getSession.mockReturnValue(new Promise((resolve) => (release = resolve)));
    const chat = mount();
    expect(chat().settled).toBe(false);
    await waitFor(() => expect(apiMock.getSession).toHaveBeenCalledWith("t", "old"));
    expect(chat().settled).toBe(false);
    expect(chat().messages).toEqual([]);
    await act(async () => {
      release(OLD_DETAIL);
    });
    await waitFor(() => expect(chat().settled).toBe(true));
    expect(chat().messages).toHaveLength(2);
  });

  it("with no chats yet it settles on the new-chat state", async () => {
    apiMock.listSessions.mockResolvedValue([]);
    const chat = mount();
    await waitFor(() => expect(chat().settled).toBe(true));
    expect(chat().activeSessionId).toBeNull();
    expect(apiMock.getSession).not.toHaveBeenCalled();
  });
});
