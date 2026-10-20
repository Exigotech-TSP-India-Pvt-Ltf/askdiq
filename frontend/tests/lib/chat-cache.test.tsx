/**
 * Chat history is instant: the session list comes from the query cache, a
 * hovered conversation is prefetched, and opening it shows the cached
 * messages at once (revalidated in the background).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { AuthProvider } from "@/lib/auth-context";
import { ChatProvider, CHAT_SESSIONS_KEY, chatSessionKey, useChat } from "@/lib/chat-context";
import { queryCache } from "@/lib/query-cache";
import { LIVE_DATA_PREFIXES } from "@/lib/live-updates";

vi.mock("@/lib/brand-context", () => ({ useBrand: () => ({ assistantName: "AskDIQ" }) }));

const ME = {
  user: { id: "u-1", email: "a@example.com", name: "A" },
  role: "superadmin" as const,
  status: "active" as const,
  permissions: [],
  version: 1,
  access_contact_email: null,
};
const session = (id: string) => ({
  id,
  title: `Chat ${id}`,
  created_at: "2026-10-20T10:00:00Z",
  last_activity_at: "2026-10-20T10:00:00Z",
  is_expired: false,
  message_count: 2,
});
const detail = (id: string) => ({
  ...session(id),
  messages: [
    { id: `${id}-q`, role: "user", content: `question ${id}`, created_at: "2026-10-20T10:00:00Z" },
    { id: `${id}-a`, role: "assistant", content: `answer ${id}`, created_at: "2026-10-20T10:00:01Z" },
  ],
});

let calls: string[] = [];

beforeEach(() => {
  queryCache.clear();
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://localhost").pathname.replace(/^\/api\/proxy/, "");
      calls.push(path);
      const body = path === "/sessions" ? [session("s1"), session("s2")] : detail(path.split("/").pop() ?? "");
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <AuthProvider initialMe={ME}>
    <ChatProvider>{children}</ChatProvider>
  </AuthProvider>
);

describe("chat history cache", () => {
  it("lists sessions from the cache, resumes the newest, and opens a prefetched one instantly", async () => {
    const { result } = renderHook(() => useChat(), { wrapper });
    await waitFor(() => expect(result.current.sessions.map((s) => s.id)).toEqual(["s1", "s2"]));
    await waitFor(() => expect(result.current.messages[0]?.content).toBe("question s1"));
    expect(queryCache.getData(CHAT_SESSIONS_KEY)).toHaveLength(2);

    act(() => result.current.prefetchSession("s2"));
    await waitFor(() => expect(queryCache.getData(chatSessionKey("s2"))).toBeTruthy());

    let opened: Promise<void> = Promise.resolve();
    act(() => {
      opened = result.current.selectSession("s2");
    });
    // Shown from the cache before the revalidation answers.
    expect(result.current.activeSessionId).toBe("s2");
    expect(result.current.loadingMessages).toBe(false);
    expect(result.current.messages[1]?.content).toBe("answer s2");
    await act(() => opened);

    // Back to the first conversation: what it showed is reused at once.
    act(() => void result.current.selectSession("s1"));
    expect(result.current.messages[0]?.content).toBe("question s1");
  });

  it("the live stream refreshes the chat list", () => {
    expect(LIVE_DATA_PREFIXES).toContain(CHAT_SESSIONS_KEY);
  });
});
