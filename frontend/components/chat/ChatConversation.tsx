"use client";

import { forwardRef, useCallback, useEffect, useRef, type FormEvent, type ReactNode } from "react";
import { m } from "motion/react";
import { useChat } from "@/lib/chat-context";
import { useBrand } from "@/lib/brand-context";
import { composerMove } from "@/lib/motion";
import type { ChatMessage } from "@/lib/types";
import { MessageBubble } from "@/components/MessageBubble";
import { TraceLink } from "@/components/chat/TraceLink";

/** The user's reply to the clarification at `index` (shown as the card's read-only summary). */
function replyTo(messages: ChatMessage[], index: number): string | null {
  const reply = messages[index + 1];
  return reply && reply.role === "user" ? reply.content : null;
}

export type ChatConversationVariant = "page" | "panel";

const LAYOUT: Record<ChatConversationVariant, { scroll: string; column: string; prompts: string; composer: string }> = {
  page: {
    scroll: "px-4 py-6 sm:px-6",
    column: "max-w-2xl",
    prompts: "sm:grid-cols-2",
    composer: "px-4 pb-4 pt-2 sm:px-6 sm:pb-5",
  },
  panel: {
    scroll: "px-4 py-5",
    column: "max-w-none",
    prompts: "",
    composer: "px-4 py-3",
  },
};

/** Page variant: the composer is a floating themed card (no full-width strip). */
const PAGE_COMPOSER_CARD =
  "flex items-end gap-2 rounded-2xl border border-line bg-surface-card p-2 shadow-xs transition-[border-color,box-shadow] focus-within:border-brand-500/60 focus-within:ring-2 focus-within:ring-brand-500/20 dark:shadow-none";
const PAGE_TEXTAREA =
  "max-h-36 flex-1 resize-none bg-transparent px-2.5 py-2 text-sm text-fg outline-hidden placeholder:text-fg-subtle";
const PANEL_TEXTAREA =
  "max-h-36 flex-1 resize-none rounded-xl border border-slate-200 bg-slate-50/50 px-3.5 py-2.5 text-sm text-ink outline-hidden transition placeholder:text-slate-400 focus:border-brand-500 focus:bg-white focus:ring-2 focus:ring-brand-100 dark:border-dark-border dark:bg-dark-card dark:text-white dark:placeholder:text-slate-500 dark:focus:bg-dark-card dark:focus:ring-brand-950";
const PAGE_PROMPT =
  "rounded-xl border border-line bg-surface-card px-3.5 py-2.5 text-left text-xs text-fg-muted shadow-2xs transition hover:border-brand-300 hover:text-brand-700 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-500/40 dark:hover:border-brand-600 dark:hover:text-brand-300";
const PANEL_PROMPT =
  "rounded-lg border border-slate-200/90 bg-white px-3.5 py-2.5 text-left text-xs text-ink/70 shadow-2xs transition hover:border-brand-300 hover:bg-brand-50/50 hover:text-brand-700 dark:border-dark-border dark:bg-dark-card dark:text-slate-200 dark:hover:border-brand-600 dark:hover:bg-brand-950/40 dark:hover:text-brand-300";
/** Subtle brand glow over the page background (both themes; follows the brand). */
const PAGE_BACKDROP =
  "bg-[radial-gradient(90%_60%_at_50%_0%,rgb(var(--brand-400)/var(--studio-glow-alpha))_0%,transparent_70%)]";

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

function SendIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  );
}

/**
 * The conversation pane shared by the full /chat page and the AskDIQ drawer:
 * message list (answers, sources, feedback, retry), empty state with the
 * brand's suggested prompts, and the composer. All state comes from
 * `useChat()` (ChatProvider at the (app) layout), so both surfaces show the
 * same session and an in-flight answer survives switching between them.
 *
 * Page variant: a new chat shows one centred stack (heading, composer,
 * suggestions); the first send moves the composer to the bottom through a
 * shared `layoutId` (instant under reduced motion, via MotionConfig). The
 * centred stack waits for `settled`, so reopening the last chat on load never
 * flashes it.
 */
export const ChatConversation = forwardRef<HTMLTextAreaElement, { variant?: ChatConversationVariant; emptyIcon?: ReactNode }>(
  function ChatConversation({ variant = "page", emptyIcon }, composerRef) {
    const { messages, input, setInput, sending, loadingMessages, settled, sendQuery, deleteExchange, retryExchange } =
      useChat();
    const brand = useBrand();
    const layout = LAYOUT[variant];
    const page = variant === "page";
    const centred = page && settled && messages.length === 0;
    const scrollRef = useRef<HTMLDivElement>(null);
    const composerEl = useRef<HTMLTextAreaElement | null>(null);
    const tour = (id: string) => (page ? `chat-${id}` : undefined);

    // The composer is also reachable by the parent (forwarded ref) and takes
    // focus when a clarification card is dismissed with Esc.
    const setComposer = useCallback(
      (el: HTMLTextAreaElement | null) => {
        composerEl.current = el;
        if (typeof composerRef === "function") composerRef(el);
        else if (composerRef) composerRef.current = el;
      },
      [composerRef],
    );

    useEffect(() => {
      const el = scrollRef.current;
      // jsdom (and some older embedded browsers) lack Element.scrollTo.
      if (el && typeof el.scrollTo === "function") el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    }, [messages]);

    // Sending from the centred stack remounts the composer at the bottom:
    // keep keyboard focus in it.
    const refocus = useRef(false);
    useEffect(() => {
      if (!centred && refocus.current) {
        refocus.current = false;
        composerEl.current?.focus({ preventScroll: true });
      }
    }, [centred]);

    function send(text: string) {
      if (centred) refocus.current = true;
      void sendQuery(text);
    }

    function onSubmit(e: FormEvent) {
      e.preventDefault();
      send(input);
    }

    const suggestions = (
      <div className={`grid w-full gap-2 ${layout.prompts}`} data-tour={tour("prompts")}>
        {brand.chatSampleQuestions.map((prompt) => (
          <button key={prompt} type="button" onClick={() => send(prompt)} className={page ? PAGE_PROMPT : PANEL_PROMPT}>
            {prompt}
          </button>
        ))}
      </div>
    );

    const composerFields = (
      <>
        <textarea
          ref={setComposer}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(input);
            }
          }}
          rows={centred ? 2 : 1}
          aria-label={`Message ${brand.assistantName}`}
          placeholder="Ask about your security and compliance posture…"
          className={page ? PAGE_TEXTAREA : PANEL_TEXTAREA}
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-600 text-white shadow-brand-glow transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50"
          aria-label="Send"
        >
          <SendIcon />
        </button>
      </>
    );

    const messageList = (
      <div className={`mx-auto space-y-5 ${layout.column}`} aria-live="polite" aria-relevant="additions">
        {messages.map((message, index) => {
          const latest = index === messages.length - 1;
          return (
            <div key={message.id}>
              <MessageBubble
                message={message}
                onDelete={() => deleteExchange(message.id)}
                onRetry={message.error ? () => void retryExchange(message.id) : undefined}
                onClarificationSubmit={(answers, summary) => void sendQuery(summary, answers)}
                onClarificationDismiss={() => composerEl.current?.focus()}
                clarificationAnswered={replyTo(messages, index)}
                clarificationDisabled={sending}
                clarificationAutoFocus={latest}
                extraActions={
                  message.role === "assistant" ? (
                    <TraceLink requestId={message.response?.request_id ?? message.requestId} />
                  ) : undefined
                }
              />
            </div>
          );
        })}
      </div>
    );

    if (!page) {
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
                <div className="mt-6 w-full">{suggestions}</div>
              </div>
            ) : (
              messageList
            )}
          </div>
          <form
            onSubmit={onSubmit}
            className={`border-t border-ink/10 bg-white dark:border-white/10 dark:bg-dark-surface ${layout.composer}`}
          >
            <div className={`mx-auto flex items-end gap-2 ${layout.column}`}>{composerFields}</div>
          </form>
        </>
      );
    }

    return (
      <div
        className={`relative flex min-h-0 flex-1 flex-col ${PAGE_BACKDROP}`}
        data-testid="chat-conversation"
        data-composer={centred ? "centred" : "bottom"}
      >
        <div
          ref={scrollRef}
          className={`scrollbar-thin min-h-0 flex-1 overflow-y-auto ${layout.scroll}`}
          aria-busy={loadingMessages || !settled || undefined}
          data-tour={tour("messages")}
        >
          {centred ? (
            <div
              className="mx-auto flex min-h-full w-full max-w-2xl flex-col items-center justify-start pb-8 pt-[10vh] text-center md:justify-center md:pt-0"
              data-testid="chat-new-centred"
            >
              <div className="mb-4">{emptyIcon ?? <ChatBubbleIcon />}</div>
              <h2 className="text-lg font-semibold text-fg">Ask {brand.assistantName} anything</h2>
              <p className="mt-1.5 text-sm text-fg-muted">
                Try a question about Essential Eight, Zero Trust, governance, or how the platform works.
              </p>
              <m.form
                layoutId="chat-composer"
                transition={composerMove}
                onSubmit={onSubmit}
                data-tour={tour("composer")}
                className={`mt-6 w-full text-left ${PAGE_COMPOSER_CARD}`}
              >
                {composerFields}
              </m.form>
              <div className="mt-4 w-full">{suggestions}</div>
            </div>
          ) : messages.length > 0 ? (
            messageList
          ) : null}
        </div>

        {centred ? null : (
          <div className={layout.composer}>
            <m.form
              layoutId="chat-composer"
              transition={composerMove}
              onSubmit={onSubmit}
              data-tour={tour("composer")}
              className={`mx-auto w-full ${layout.column} ${PAGE_COMPOSER_CARD}`}
            >
              {composerFields}
            </m.form>
          </div>
        )}
      </div>
    );
  },
);
