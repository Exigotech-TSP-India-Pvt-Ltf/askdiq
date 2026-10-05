"use client";

import { useEffect, useRef, type FormEvent } from "react";
import { useChat } from "@/lib/chat-context";
import { MessageBubble } from "@/components/MessageBubble";
import { ChatSessionList } from "@/components/ChatSessionList";

const SUGGESTED_PROMPTS = [
  "What is the Essential Eight and how does DeployIQ implement it?",
  "How does DeployIQ keep humans in the loop over AI actions?",
  "What does the Zero Trust Professional plan include?",
  "How does DeployIQ handle customer data?",
];

export default function ChatPage() {
  // Chat state (sessions, messages, sending, the send/delete functions)
  // lives in ChatProvider at the (app) layout level, not here — so an
  // in-flight query and its eventual answer survive switching to another
  // page and back, instead of being dropped when this page component
  // unmounts.
  const {
    sessions,
    activeSessionId,
    messages,
    input,
    setInput,
    sending,
    sendQuery,
    deleteExchange,
    selectSession,
    startNewSession,
    renameSession,
    removeSession,
  } = useChat();

  const scrollRef = useRef<HTMLDivElement>(null);

  /*
   * ---------------------------------------------------------
   * AUTO SCROLL
   * ---------------------------------------------------------
   */
  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  /*
   * ---------------------------------------------------------
   * FORM SUBMIT
   * ---------------------------------------------------------
   */
  function onSubmit(e: FormEvent) {
    e.preventDefault();
    sendQuery(input);
  }

  return (
    <div className="flex h-full">
      <ChatSessionList
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelect={selectSession}
        onNew={startNewSession}
        onRename={renameSession}
        onDelete={removeSession}
      />

      <div className="flex h-full flex-1 flex-col">
      {/* =====================================================
          HEADER
      ====================================================== */}
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-ink/10 px-6">
        <div>
          <h1 className="text-sm font-semibold text-ink">
            Deploy IQ Assistant
          </h1>

          <p className="text-xs text-ink/50">
            Answers are grounded in your knowledge base and checked
            before delivery.
          </p>
        </div>
      </header>

      {/* =====================================================
          CHAT AREA
      ====================================================== */}
      <div
        ref={scrollRef}
        className="scrollbar-thin flex-1 overflow-y-auto px-6 py-6"
      >
        {messages.length === 0 ? (
          <div className="mx-auto flex h-full max-w-lg flex-col items-center justify-center text-center">
            {/* Chat icon */}
            <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-50">
              <svg
                width="22"
                height="22"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                className="text-brand-600"
              >
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
            </div>

            <h2 className="text-base font-semibold text-ink">
              Ask Deploy IQ anything
            </h2>

            <p className="mt-1.5 text-sm text-ink/55">
              Try a question about Essential Eight, Zero Trust,
              governance, or how the platform works.
            </p>

            {/* Suggested prompts */}
            <div className="mt-6 grid w-full gap-2 sm:grid-cols-2">
              {SUGGESTED_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  onClick={() => sendQuery(prompt)}
                  className="rounded-lg border border-ink/10 px-3.5 py-2.5 text-left text-xs text-ink/70 transition hover:border-brand-300 hover:bg-brand-50 hover:text-brand-700"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-2xl space-y-5">
            {messages.map((message) => (
              <MessageBubble
                key={message.id}
                message={message}
                onDelete={() => deleteExchange(message.id)}
              />
            ))}
          </div>
        )}
      </div>

      {/* =====================================================
          INPUT
      ====================================================== */}
      <form
        onSubmit={onSubmit}
        className="border-t border-ink/10 px-6 py-4"
      >
        <div className="mx-auto flex max-w-2xl items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendQuery(input);
              }
            }}
            rows={1}
            placeholder="Ask about your security and compliance posture…"
            className="max-h-36 flex-1 resize-none rounded-xl border border-ink/10 bg-ink/[0.03] px-3.5 py-2.5 text-sm text-ink outline-none transition focus:border-brand-500 focus:bg-white focus:ring-2 focus:ring-brand-100"
          />

          <button
            type="submit"
            disabled={sending || !input.trim()}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-600 text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
            aria-label="Send"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </button>
        </div>
      </form>
      </div>
    </div>
  );
}