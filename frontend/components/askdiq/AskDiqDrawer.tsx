"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { AnimatePresence, m } from "motion/react";
import { History, Maximize2, Minus, SquarePen } from "lucide-react";
import { useBrand } from "@/lib/brand-context";
import { useChat } from "@/lib/chat-context";
import { crossfade, drawer as drawerVariants, useMotionVariants } from "@/lib/motion";
import { useFocusTrap } from "@/lib/use-focus-trap";
import { SMALL_SCREEN_QUERY, useMediaQuery } from "@/lib/askdiq-widget";
import { iconButton, overlayBackdrop, overlayPanel } from "@/components/ui/styles";
import { ChatConversation } from "@/components/chat/ChatConversation";
import { ChatSessionList } from "@/components/ChatSessionList";
import { useReportedAction } from "@/components/chat/use-reported-action";
import { AskDiqMascot } from "@/components/askdiq/AskDiqMascot";

type View = "chat" | "history";

export interface AskDiqDrawerProps {
  id: string;
  open: boolean;
  onClose: () => void;
}

/**
 * Right-hand AskDIQ chat drawer, full window height (100dvh).
 *
 * - From 640 px up it is NON-modal: no scrim, the dashboard stays usable,
 *   focus moves to the composer on open and back to the launcher on close,
 *   Esc (with focus inside) minimises.
 * - Below 640 px it is full-width and modal: scrim, focus trap, Esc closes.
 *
 * The conversation is the same `ChatConversation` the /chat page renders,
 * fed by the layout-level ChatProvider, so the session carries over to
 * "Open full chat" and back.
 */
export function AskDiqDrawer({ id, open, onClose }: AskDiqDrawerProps) {
  const brand = useBrand();
  const chat = useChat();
  const reported = useReportedAction();
  const modal = useMediaQuery(SMALL_SCREEN_QUERY);
  const panelVariants = useMotionVariants(drawerVariants);
  const [view, setView] = useState<View>("chat");
  const panelRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const titleId = `${id}-title`;

  useFocusTrap(panelRef, open && modal, { onEscape: onClose, initialFocusRef: composerRef });

  useEffect(() => {
    if (open && !modal) (composerRef.current ?? panelRef.current)?.focus();
  }, [open, modal]);

  function onPanelKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    // Modal mode: the focus trap owns Esc.
    if (modal || event.key !== "Escape" || event.defaultPrevented) return;
    // Esc inside a field that uses it (renaming a chat) stays with the field.
    if (event.target instanceof HTMLInputElement) return;
    event.preventDefault();
    onClose();
  }

  const showChat = () => {
    setView("chat");
    requestAnimationFrame(() => composerRef.current?.focus());
  };
  const newChat = reported("Couldn't start a new chat", async () => {
    await chat.startNewSession();
    showChat();
  });
  const openSession = reported("Couldn't open that chat", async (sessionId: string) => {
    await chat.selectSession(sessionId);
    showChat();
  });

  const subtitle = chat.sending ? "Thinking…" : "Grounded in your knowledge base";

  return createPortal(
    <AnimatePresence>
      {open ? (
        <div key="askdiq" className="contents">
          {modal ? (
            <m.div
              aria-hidden="true"
              className={`${overlayBackdrop} z-[89]`}
              variants={crossfade}
              initial="hidden"
              animate="visible"
              exit="exit"
              onClick={onClose}
            />
          ) : null}
          <m.div
            ref={panelRef}
            id={id}
            role="dialog"
            aria-modal={modal || undefined}
            aria-labelledby={titleId}
            tabIndex={-1}
            onKeyDown={onPanelKeyDown}
            variants={panelVariants}
            initial="hidden"
            animate="visible"
            exit="exit"
            data-testid="askdiq-drawer"
            data-modal={modal ? "true" : "false"}
            className={`fixed inset-y-0 right-0 z-[90] flex h-[100dvh] w-full flex-col overflow-hidden border-y-0 border-r-0 sm:w-[420px] sm:rounded-l-2xl lg:w-[440px] ${overlayPanel}`}
          >
            <header className="flex shrink-0 items-center gap-3 border-b border-line-brand/70 bg-surface-studio/70 px-4 py-3 pt-[max(0.75rem,env(safe-area-inset-top))] dark:border-brand-500/20">
              <span className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-brand-200/80 bg-brand-50 dark:border-brand-500/30 dark:bg-brand-500/15">
                <AskDiqMascot size={32} />
                <span
                  className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-surface-studio bg-status-ok"
                  aria-hidden="true"
                />
              </span>
              <div className="min-w-0 flex-1">
                <h2 id={titleId} className="truncate text-sm font-extrabold tracking-tight text-fg">
                  {brand.assistantName}
                </h2>
                <p className="truncate text-[11px] font-medium text-fg-subtle" aria-live="polite">
                  {subtitle}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-0.5">
                <button
                  type="button"
                  onClick={() => (view === "history" ? showChat() : setView("history"))}
                  aria-pressed={view === "history"}
                  aria-label="Recent chats"
                  title="Recent chats"
                  className={`${iconButton} aria-pressed:bg-surface-muted aria-pressed:text-fg`}
                >
                  <History className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                </button>
                <button type="button" onClick={() => void newChat()} aria-label="New chat" title="New chat" className={iconButton}>
                  <SquarePen className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                </button>
                <Link href="/chat" aria-label="Open full chat" title="Open full chat" className={iconButton}>
                  <Maximize2 className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                </Link>
                <button type="button" onClick={onClose} aria-label="Minimize" title="Minimize (Esc)" className={iconButton}>
                  <Minus className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
                </button>
              </div>
            </header>

            <div className="flex min-h-0 flex-1 flex-col bg-surface-page">
              {view === "history" ? (
                <ChatSessionList
                  sessions={chat.sessions}
                  activeSessionId={chat.activeSessionId}
                  onSelect={(sessionId) => void openSession(sessionId)}
                  onNew={() => void newChat()}
                  onRename={reported("Couldn't rename the chat", chat.renameSession)}
                  onDelete={reported("Couldn't delete the chat", chat.removeSession)}
                  className="min-h-0 flex-1"
                />
              ) : (
                <ChatConversation
                  ref={composerRef}
                  variant="panel"
                  emptyIcon={
                    <span className="relative flex">
                      <span className="absolute inset-2 rounded-full bg-brand-400/30 blur-xl" aria-hidden="true" />
                      <AskDiqMascot size={72} className="relative" />
                    </span>
                  }
                />
              )}
            </div>
          </m.div>
        </div>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}
