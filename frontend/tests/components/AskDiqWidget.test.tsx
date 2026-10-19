import React from "react";
import fs from "fs";
import path from "path";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import type { ChatMessage, SourceChunk } from "@/lib/types";

const nav = vi.hoisted(() => ({ pathname: "/admin/dashboard" }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const chatState = vi.hoisted(() => ({
  current: {} as Record<string, unknown>,
}));
const useChatSpy = vi.hoisted(() => vi.fn(() => chatState.current));
vi.mock("@/lib/chat-context", () => ({ useChat: useChatSpy }));

// Imported after the mocks are registered.
import { AskDiqLauncher } from "@/components/askdiq/AskDiqLauncher";
import { AskDiqDrawer } from "@/components/askdiq/AskDiqDrawer";
import { ChatHeader } from "@/components/chat/ChatHeader";
import { citedSources, sourceChips } from "@/components/chat/SourceChips";
import { MessageBubble } from "@/components/MessageBubble";
import ChatPage from "@/app/(app)/chat/page";
import { GREETING_DISMISSED_KEY, GREETING_SEEN_KEY } from "@/lib/askdiq-widget";
import { isAskShortcut } from "@/lib/shortcuts";

function baseChat(overrides: Record<string, unknown> = {}) {
  return {
    sessions: [],
    activeSessionId: null,
    messages: [] as ChatMessage[],
    input: "",
    setInput: vi.fn(),
    sending: false,
    loadingMessages: false,
    sendQuery: vi.fn().mockResolvedValue(undefined),
    submitFeedback: vi.fn(),
    deleteExchange: vi.fn(),
    retryExchange: vi.fn().mockResolvedValue(undefined),
    selectSession: vi.fn().mockResolvedValue(undefined),
    startNewSession: vi.fn().mockResolvedValue(undefined),
    renameSession: vi.fn(),
    removeSession: vi.fn(),
    ...overrides,
  };
}

function stubMatchMedia({ reduce = false, small = false } = {}) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: (reduce && query.includes("prefers-reduced-motion")) || (small && query.includes("max-width")),
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

function renderLauncher() {
  return render(
    <MotionProvider>
      <AskDiqLauncher />
    </MotionProvider>,
  );
}

const launcherButton = () => screen.getByRole("button", { name: /^Ask AskDIQ/ });

beforeAll(() => {
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo;
});

beforeEach(() => {
  nav.pathname = "/admin/dashboard";
  chatState.current = baseChat();
  // Greeting already seen: tests that need it clear this.
  window.sessionStorage.setItem(GREETING_SEEN_KEY, "1");
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("AskDIQ launcher", () => {
  it("renders the mascot launcher and toggles the lazy drawer open and closed", async () => {
    renderLauncher();
    const button = launcherButton();
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(button);
    const dialog = await screen.findByRole("dialog", { name: "AskDIQ" });
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(dialog).toHaveAttribute("data-modal", "false");
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Message AskDIQ" })).toHaveFocus());

    fireEvent.click(within(dialog).getByRole("button", { name: "Minimize" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveFocus();
  });

  it("closes on Esc", async () => {
    renderLauncher();
    fireEvent.click(launcherButton());
    const composer = await screen.findByRole("textbox", { name: "Message AskDIQ" });
    fireEvent.keyDown(composer, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("toggles with Ctrl+/ and Cmd+/", async () => {
    renderLauncher();
    fireEvent.keyDown(window, { key: "/", ctrlKey: true });
    expect(await screen.findByRole("dialog", { name: "AskDIQ" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "/", metaKey: true });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    expect(isAskShortcut(new KeyboardEvent("keydown", { key: "/", ctrlKey: true }))).toBe(true);
    expect(isAskShortcut(new KeyboardEvent("keydown", { key: "/" }))).toBe(false);
    expect(isAskShortcut(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }))).toBe(false);
  });

  it("is hidden on the full /chat page (no duplicate conversation)", () => {
    nav.pathname = "/chat";
    renderLauncher();
    expect(screen.queryByTestId("askdiq-launcher")).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: "/", ctrlKey: true });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders static (no float, no pulse ring) when the user prefers reduced motion", () => {
    stubMatchMedia({ reduce: true });
    const { container } = renderLauncher();
    expect(launcherButton()).toHaveAttribute("data-motion", "static");
    expect(container.querySelector("span.bg-brand-400\\/50")).toBeNull();
  });

  it("animates when reduced motion is off", () => {
    stubMatchMedia({ reduce: false });
    const { container } = renderLauncher();
    expect(launcherButton()).toHaveAttribute("data-motion", "animated");
    expect(container.querySelector("span.bg-brand-400\\/50")).not.toBeNull();
  });

  it("greets once per session and remembers a dismissal", async () => {
    window.sessionStorage.clear();
    renderLauncher();
    const greeting = await screen.findByRole("status", {}, { timeout: 3000 });
    expect(greeting).toHaveTextContent("Hi, I'm AskDIQ — ask me anything");
    expect(window.sessionStorage.getItem(GREETING_SEEN_KEY)).toBe("1");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss greeting" }));
    await waitFor(() => expect(screen.queryByText(/ask me anything/)).not.toBeInTheDocument());
    expect(window.localStorage.getItem(GREETING_DISMISSED_KEY)).toBe("1");
  });

  it("does not greet again after a dismissal, even in a new session", async () => {
    window.sessionStorage.clear();
    window.localStorage.setItem(GREETING_DISMISSED_KEY, "1");
    renderLauncher();
    await new Promise((resolve) => setTimeout(resolve, 1500));
    expect(screen.queryByText(/ask me anything/)).not.toBeInTheDocument();
  });

  it("shows typing while answering and an unread dot when the answer lands while closed", () => {
    chatState.current = baseChat({ sending: true });
    const { rerender } = renderLauncher();
    expect(screen.getByTestId("askdiq-typing")).toBeInTheDocument();
    expect(launcherButton()).toHaveAccessibleName("Ask AskDIQ (answering)");

    chatState.current = baseChat({ sending: false });
    rerender(
      <MotionProvider>
        <AskDiqLauncher />
      </MotionProvider>,
    );
    expect(screen.getByTestId("askdiq-unread")).toBeInTheDocument();
    expect(launcherButton()).toHaveAccessibleName("Ask AskDIQ (new answer)");

    fireEvent.click(launcherButton());
    expect(screen.queryByTestId("askdiq-unread")).not.toBeInTheDocument();
  });
});

describe("AskDIQ drawer", () => {
  const answer: ChatMessage = {
    id: "a1",
    role: "assistant",
    content: "Essential Eight is a set of mitigations.",
    createdAt: Date.UTC(2026, 9, 16, 9, 30),
    response: {
      answer: "Essential Eight is a set of mitigations.",
      sources: [
        { chunk_id: "c1", document_id: "d1", content: "E8 overview", score: 0.9, metadata: { source_name: "e8-guide.md" } },
      ],
      grounded: true,
      confidence: 0.9,
      request_id: "r1",
      latency_ms: 10,
      guardrail_flags: [],
      session_id: "s1",
      session_title: "E8",
      user_message_id: "u1",
      assistant_message_id: "a1",
    },
  };
  const question: ChatMessage = { id: "u1", role: "user", content: "What is E8?", createdAt: answer.createdAt };

  function renderDrawer(onClose = vi.fn()) {
    render(
      <MotionProvider>
        <AskDiqDrawer id="askdiq-test" open onClose={onClose} />
      </MotionProvider>,
    );
    return onClose;
  }

  it("renders the shared conversation from useChat (messages, sources, timestamps, feedback)", () => {
    chatState.current = baseChat({ messages: [question, answer], activeSessionId: "s1" });
    renderDrawer();
    expect(useChatSpy).toHaveBeenCalled();
    const dialog = screen.getByRole("dialog", { name: "AskDIQ" });
    expect(within(dialog).getByText("What is E8?")).toBeInTheDocument();
    expect(within(dialog).getByText("Essential Eight is a set of mitigations.")).toBeInTheDocument();
    expect(within(dialog).getByRole("list", { name: "Sources" })).toHaveTextContent("e8-guide.md");
    expect(dialog.querySelectorAll("time").length).toBeGreaterThanOrEqual(2);
    expect(within(dialog).getByRole("button", { name: /^helpful response$/i })).toBeInTheDocument();
    expect(within(dialog).getAllByRole("button", { name: "Copy message" }).length).toBeGreaterThan(0);
  });

  const pricingClarification = {
    preamble: "DeployIQ publishes a few kinds of pricing.",
    original_query: "pricing",
    questions: [
      {
        id: "Q1",
        header: "Topic",
        question: "Which pricing do you mean?",
        options: [
          { id: "O1", label: "Platform plans", description: "Seat-based plans", sources: ["pricing.md"] },
          { id: "O2", label: "Support tiers", description: "Support add-ons" },
        ],
      },
    ],
  };

  it("renders the clarification card in the drawer; picking sends all answers in one follow-up", () => {
    const sendQuery = vi.fn().mockResolvedValue(undefined);
    const clarify: ChatMessage = {
      id: "a3",
      role: "assistant",
      content: "Which pricing do you mean?\n\n1. Platform plans\n2. Support tiers",
      createdAt: answer.createdAt,
      clarification: pricingClarification,
    };
    chatState.current = baseChat({
      sendQuery,
      messages: [{ id: "u3", role: "user", content: "pricing", createdAt: answer.createdAt }, clarify],
    });
    renderDrawer();
    const card = screen.getByRole("region", { name: "Clarifying questions" });
    expect(within(card).getByText("DeployIQ publishes a few kinds of pricing.")).toBeInTheDocument();
    fireEvent.click(within(card).getByRole("radio", { name: /Support tiers/ }));
    fireEvent.click(within(card).getByRole("button", { name: "Send" }));
    expect(sendQuery).toHaveBeenCalledWith("Topic: Support tiers", [{ question_id: "Q1", option_ids: ["O2"] }]);
  });

  it("Esc skips the card and focuses the composer", () => {
    chatState.current = baseChat({
      messages: [{ id: "a5", role: "assistant", content: "x", createdAt: answer.createdAt, clarification: pricingClarification }],
    });
    renderDrawer();
    const radio = screen.getByRole("radio", { name: /Platform plans/ });
    fireEvent.keyDown(radio, { key: "Escape" });
    expect(screen.getByRole("textbox", { name: "Message AskDIQ" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Show questions" })).toBeInTheDocument();
  });

  it("shows an answered clarification as a read-only summary", () => {
    chatState.current = baseChat({
      messages: [
        { id: "a4", role: "assistant", content: "x", createdAt: answer.createdAt, clarification: pricingClarification },
        { id: "u5", role: "user", content: "Topic: Support tiers", createdAt: answer.createdAt },
      ],
    });
    renderDrawer();
    expect(screen.getByRole("note", { name: "Your answers" })).toHaveTextContent("Topic: Support tiers");
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });

  it("shows the brand's suggested prompts on an empty chat and sends one", () => {
    const sendQuery = vi.fn().mockResolvedValue(undefined);
    chatState.current = baseChat({ sendQuery });
    renderDrawer();
    expect(screen.getByRole("heading", { name: "Ask AskDIQ anything" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Essential Eight and how does DeployIQ/ }));
    expect(sendQuery).toHaveBeenCalledWith("What is the Essential Eight and how does DeployIQ implement it?");
  });

  it("offers retry on a failed answer", () => {
    const retryExchange = vi.fn().mockResolvedValue(undefined);
    chatState.current = baseChat({
      retryExchange,
      messages: [question, { id: "a2", role: "assistant", content: "", createdAt: 1, error: "Service unavailable" }],
    });
    renderDrawer();
    expect(screen.getByRole("alert")).toHaveTextContent("Service unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retryExchange).toHaveBeenCalledWith("a2");
  });

  it("switches to recent chats, opens one and starts a new chat", async () => {
    const selectSession = vi.fn().mockResolvedValue(undefined);
    const startNewSession = vi.fn().mockResolvedValue(undefined);
    chatState.current = baseChat({
      selectSession,
      startNewSession,
      sessions: [
        {
          id: "s9",
          title: "Zero Trust rollout",
          created_at: "2026-10-16T00:00:00Z",
          last_activity_at: "2026-10-16T00:00:00Z",
          expires_at: "2026-10-17T00:00:00Z",
          is_expired: false,
          message_count: 2,
        },
      ],
    });
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: "Recent chats" }));
    fireEvent.click(screen.getByText("Zero Trust rollout"));
    await waitFor(() => expect(selectSession).toHaveBeenCalledWith("s9"));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Message AskDIQ" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));
    await waitFor(() => expect(startNewSession).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("link", { name: "Open full chat" })).toHaveAttribute("href", "/chat");
  });

  it("is a full-width modal with a focus trap on small screens; Esc closes", async () => {
    stubMatchMedia({ small: true });
    const onClose = renderDrawer();
    const dialog = screen.getByRole("dialog", { name: "AskDIQ" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("data-modal", "true");
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Message AskDIQ" })).toHaveFocus());
    act(() => {
      fireEvent.keyDown(document, { key: "Escape" });
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("assistant naming", () => {
  const BANNED = [/DeployIQ Assistant/i, /Deploy IQ Assistant/i, /Ask DeployIQ anything/i];

  it("uses the brand assistant name (AskDIQ) in the chat page, header and drawer", () => {
    chatState.current = baseChat();
    const { container } = render(
      <MotionProvider>
        <ChatPage />
        <ChatHeader />
        <AskDiqDrawer id="naming" open onClose={vi.fn()} />
      </MotionProvider>,
    );
    const text = `${container.textContent ?? ""} ${document.body.textContent ?? ""}`;
    expect(text).toContain("AskDIQ");
    expect(screen.getAllByRole("heading", { level: 1 })[0]).toHaveTextContent("AskDIQ");
    for (const pattern of BANNED) expect(text).not.toMatch(pattern);
  });

  it("leaves no legacy assistant name in the UI source", () => {
    const root = path.resolve(__dirname, "../..");
    const walk = (dir: string): string[] =>
      fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
        const p = path.join(dir, e.name);
        if (e.isDirectory()) return walk(p);
        return /\.(ts|tsx)$/.test(e.name) ? [p] : [];
      });
    const files = ["app", "components", "lib"].flatMap((d) => walk(path.join(root, d)));
    for (const file of files) {
      const src = fs.readFileSync(file, "utf-8");
      for (const pattern of [...BANNED, /productName\}\s*Assistant/]) expect(src, file).not.toMatch(pattern);
    }
  });

  it("the drawer and the /chat page share one conversation component and chat hook", () => {
    const root = path.resolve(__dirname, "../..");
    const drawer = fs.readFileSync(path.join(root, "components/askdiq/AskDiqDrawer.tsx"), "utf-8");
    const page = fs.readFileSync(path.join(root, "app/(app)/chat/page.tsx"), "utf-8");
    for (const src of [drawer, page]) {
      expect(src).toMatch(/import \{ ChatConversation \} from "@\/components\/chat\/ChatConversation"/);
      expect(src).toMatch(/useChat\(\)/);
    }
    // No second data layer: the drawer never calls the API directly.
    expect(drawer).not.toMatch(/@\/lib\/api"/);
  });
});

describe("source chips", () => {
  it("dedupes by document, best score first, and falls back to a generic name", () => {
    const src = (id: string, doc: string, score: number, name?: string): SourceChunk => ({
      chunk_id: id,
      document_id: doc,
      content: "x".repeat(300),
      score,
      metadata: name ? { source_name: name } : {},
    });
    const chips = sourceChips([src("1", "a", 0.2, "a.md"), src("2", "b", 0.9), src("3", "a", 0.8, "a.md")]);
    expect(chips.map((c) => c.name)).toEqual(["Knowledge base", "a.md"]);
    expect(chips[0].excerpt.length).toBeLessThanOrEqual(161);
  });

  it("shows no chips under fallback / no-answer replies", () => {
    const base = {
      answer: "I couldn't find that in the knowledge base.",
      sources: [{ chunk_id: "1", document_id: "d", content: "c", score: 0.3, metadata: { source_name: "weak.md" } }],
      grounded: true,
      confidence: 0.2,
      request_id: "r",
      latency_ms: 1,
      guardrail_flags: [],
      session_id: "s",
      session_title: "t",
      user_message_id: "u",
      assistant_message_id: "m",
    };
    const cases = [
      { ...base, grounded: false },
      { ...base, outcome: "ungrounded" },
      { ...base, outcome: "llm_failed" },
      { ...base, outcome: "blocked" },
      { ...base, outcome: "conversational" },
    ];
    for (const response of cases) {
      expect(citedSources(response)).toEqual([]);
      const { unmount } = render(
        <MessageBubble message={{ id: "m", role: "assistant", content: response.answer, createdAt: 0, response }} />,
      );
      expect(screen.queryByRole("list", { name: "Sources" })).not.toBeInTheDocument();
      unmount();
    }
    expect(citedSources({ ...base, outcome: "answered" })).toHaveLength(1);
  });

  it("renders a hostile source name as text, not markup", () => {
    const message: ChatMessage = {
      id: "m",
      role: "assistant",
      content: "ok",
      createdAt: 0,
      response: {
        answer: "ok",
        sources: [
          {
            chunk_id: "1",
            document_id: "d",
            content: "c",
            score: 1,
            metadata: { source_name: "<img src=x onerror=alert(1)>" },
          },
        ],
        grounded: true,
        confidence: 1,
        request_id: "r",
        latency_ms: 1,
        guardrail_flags: [],
        session_id: "s",
        session_title: "t",
        user_message_id: "u",
        assistant_message_id: "m",
      },
    };
    const { container } = render(<MessageBubble message={message} />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
  });
});
