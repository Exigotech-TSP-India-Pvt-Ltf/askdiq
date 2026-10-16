"use client";

import { forwardRef, useEffect, useRef, type FormEvent, type ReactNode } from "react";
import { useChat } from "@/lib/chat-context";
import { useBrand } from "@/lib/brand-context";
import { MessageBubble } from "@/components/MessageBubble";
import { TraceLink } from "@/components/chat/TraceLink";

export type ChatConversationVariant = "page" | "panel";

const LAYOUT: Record<ChatConversationVariant, { scroll: string; column: string; prompts: string; composer: string }> = {
  page: {
    scroll: "px-6 py-6",
    column: "max-w-2xl",
    prompts: "sm:grid-cols-2",
    composer: "px-6 py-4",
  },
  panel: {
    scroll: "px-4 py-5",
    column: "max-w-none",
    prompts: "",
    composer: "px-4 py-3",
  },
};

function ChatBubbleIcon() {
  return (
    <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-brand-100 bg-brand-50 dark:border-brand-900/40 dark:bg-brand-950/40">
      <svg
        width="22"
        height="22"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        className="text-brand-600 dark:text-brand-400"
        aria-hidden="true"
      >
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      </svg>
    </div>
  );
}

/**
 * The conversation pane shared by the full /chat page and the AskDIQ drawer:
 * message list (answers, sources, feedback, retry), empty state with the
 * brand's suggested prompts, and the composer. All state comes from
 * `useChat()` (ChatProvider at the (app) layout), so both surfaces show the
 * same session and an in-flight answer survives switching between them.
 */
export const ChatConversation = forwardRef<HTMLTextAreaElement, { variant?: ChatConversationVariant; emptyIcon?: ReactNode }>(
  function ChatConversation({ variant = "page", emptyIcon }, composerRef) {
    const { messages, input, setInput, sending, loadingMessages, sendQuery, deleteExchange, retryExchange } = useChat();
    const brand = useBrand();
    const layout = LAYOUT[variant];
    const scrollRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    }, [messages]);

    function onSubmit(e: FormEvent) {
      e.preventDefault();
      void sendQuery(input);
    }

    return (
      <>
        <div
          ref={scrollRef}
          className={`scrollbar-thin min-h-0 flex-1 overflow-y-auto ${layout.scroll}`}
          aria-busy={loadingMessages || undefined}
        >
          {messages.length === 0 ? (
            <div className="mx-auto flex h-full max-w-lg flex-col items-center justify-center text-center">
              <div className="mb-4">{emptyIcon ?? <ChatBubbleIcon />}</div>
              <h2 className="text-base font-semibold text-ink dark:text-white">Ask {brand.assistantName} anything</h2>
              <p className="mt-1.5 text-sm text-ink/55 dark:text-slate-400">
                Try a question about Essential Eight, Zero Trust, governance, or how the platform works.
              </p>
              <div className={`mt-6 grid w-full gap-2 ${layout.prompts}`}>
                {brand.chatSampleQuestions.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => void sendQuery(prompt)}
                    className="rounded-lg border border-slate-200/90 bg-white px-3.5 py-2.5 text-left text-xs text-ink/70 shadow-xs transition hover:border-brand-300 hover:bg-brand-50/50 hover:text-brand-700 dark:border-dark-border dark:bg-dark-card dark:text-slate-200 dark:hover:border-brand-600 dark:hover:bg-brand-950/40 dark:hover:text-brand-300"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className={`mx-auto space-y-5 ${layout.column}`} aria-live="polite" aria-relevant="additions">
              {messages.map((message) => (
                <div key={message.id}>
                  <MessageBubble
                    message={message}
                    onDelete={() => deleteExchange(message.id)}
                    onRetry={message.error ? () => void retryExchange(message.id) : undefined}
                  />
                  {message.role === "assistant" && (
                    <div className="mt-1 pl-1">
                      <TraceLink requestId={message.response?.request_id ?? message.requestId} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <form
          onSubmit={onSubmit}
          className={`border-t border-ink/10 bg-white dark:border-white/10 dark:bg-dark-surface ${layout.composer}`}
        >
          <div className={`mx-auto flex items-end gap-2 ${layout.column}`}>
            <textarea
              ref={composerRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void sendQuery(input);
                }
              }}
              rows={1}
              aria-label={`Message ${brand.assistantName}`}
              placeholder="Ask about your security and compliance posture…"
              className="max-h-36 flex-1 resize-none rounded-xl border border-slate-200 bg-slate-50/50 px-3.5 py-2.5 text-sm text-ink outline-none transition placeholder:text-slate-400 focus:border-brand-500 focus:bg-white focus:ring-2 focus:ring-brand-100 dark:border-dark-border dark:bg-dark-card dark:text-white dark:placeholder:text-slate-500 dark:focus:bg-dark-card dark:focus:ring-brand-950"
            />
            <button
              type="submit"
              disabled={sending || !input.trim()}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-600 text-white shadow-brand-glow transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
              aria-label="Send"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
          </div>
        </form>
      </>
    );
  },
);
