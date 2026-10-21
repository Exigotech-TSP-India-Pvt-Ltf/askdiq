/**
 * OR-5: a new, settled chat shows one centred stack (heading, composer,
 * suggestions); once a message exists the composer sits at the bottom. While
 * the last chat is still loading the centred stack never renders (no flash).
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import type { ChatMessage } from "@/lib/types";

const chat = vi.hoisted(() => ({
  current: {} as Record<string, unknown>,
}));
vi.mock("@/lib/chat-context", () => ({ useChat: () => chat.current }));
vi.mock("@/lib/brand-context", () => ({
  useBrand: () => ({ assistantName: "AskDIQ", chatSampleQuestions: ["What is Zero Trust?", "Explain Essential Eight"] }),
}));
vi.mock("@/components/MessageBubble", () => ({
  MessageBubble: ({ message }: { message: ChatMessage }) => <p>{message.content}</p>,
}));
vi.mock("@/components/chat/TraceLink", () => ({ TraceLink: () => null }));

import { ChatConversation } from "@/components/chat/ChatConversation";

function state(overrides: Record<string, unknown> = {}) {
  chat.current = {
    messages: [],
    input: "",
    setInput: vi.fn(),
    sending: false,
    loadingMessages: false,
    settled: true,
    sendQuery: vi.fn().mockResolvedValue(undefined),
    deleteExchange: vi.fn(),
    retryExchange: vi.fn(),
    ...overrides,
  };
  return chat.current;
}

const message = (id: string, role: "user" | "assistant", content: string): ChatMessage => ({
  id,
  role,
  content,
  createdAt: 0,
});

function mockReducedMotion(reduce: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: reduce && query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

const renderPage = () =>
  render(
    <MotionProvider>
      <ChatConversation variant="page" />
    </MotionProvider>,
  );

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ChatConversation page empty state", () => {
  it("centres heading, composer and suggestions on a settled new chat", () => {
    state();
    renderPage();
    const centred = screen.getByTestId("chat-new-centred");
    const heading = screen.getByRole("heading", { name: "Ask AskDIQ anything" });
    const composer = screen.getByRole("textbox", { name: "Message AskDIQ" });
    const chip = screen.getByRole("button", { name: "What is Zero Trust?" });
    for (const el of [heading, composer, chip]) expect(centred).toContainElement(el);
    // Order: heading, then composer, then suggestions.
    expect(heading.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(composer.compareDocumentPosition(chip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId("chat-conversation")).toHaveAttribute("data-composer", "centred");
  });

  it("never renders the centred stack while the conversation is unsettled or loading", () => {
    state({ settled: false });
    const { rerender } = renderPage();
    expect(screen.queryByTestId("chat-new-centred")).toBeNull();
    expect(screen.getByTestId("chat-conversation")).toHaveAttribute("data-composer", "bottom");
    expect(screen.getByRole("textbox", { name: "Message AskDIQ" })).toBeInTheDocument();

    state({ settled: false, loadingMessages: true });
    rerender(
      <MotionProvider>
        <ChatConversation variant="page" />
      </MotionProvider>,
    );
    expect(screen.queryByTestId("chat-new-centred")).toBeNull();
  });

  it("puts the composer at the bottom once there are messages", () => {
    state({ messages: [message("u1", "user", "Hi"), message("a1", "assistant", "Hello")] });
    renderPage();
    expect(screen.queryByTestId("chat-new-centred")).toBeNull();
    expect(screen.getByTestId("chat-conversation")).toHaveAttribute("data-composer", "bottom");
    expect(screen.getByText("Hello")).toBeInTheDocument();
  });

  it("a suggestion click sends it; the composer then moves to the bottom and keeps focus", () => {
    mockReducedMotion(true);
    const s = state();
    const { rerender } = renderPage();
    fireEvent.click(screen.getByRole("button", { name: "What is Zero Trust?" }));
    expect(s.sendQuery).toHaveBeenCalledWith("What is Zero Trust?");

    state({ messages: [message("u1", "user", "What is Zero Trust?"), { ...message("a1", "assistant", ""), pending: true }] });
    rerender(
      <MotionProvider>
        <ChatConversation variant="page" />
      </MotionProvider>,
    );
    expect(screen.getByTestId("chat-conversation")).toHaveAttribute("data-composer", "bottom");
    const composer = screen.getByRole("textbox", { name: "Message AskDIQ" });
    expect(composer).toHaveFocus();
    // Reduced motion: an instant swap, no transform left on the composer.
    expect((composer.closest("form") as HTMLElement).style.transform).toBe("");
  });

  it("the panel (drawer) variant keeps its own layout", () => {
    state();
    render(
      <MotionProvider>
        <ChatConversation variant="panel" />
      </MotionProvider>,
    );
    expect(screen.queryByTestId("chat-new-centred")).toBeNull();
    expect(screen.queryByTestId("chat-conversation")).toBeNull();
    expect(screen.getByRole("heading", { name: "Ask AskDIQ anything" })).toBeInTheDocument();
  });
});
