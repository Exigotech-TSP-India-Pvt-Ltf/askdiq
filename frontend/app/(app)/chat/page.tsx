"use client";

import { useState } from "react";
import { useChat } from "@/lib/chat-context";
import { ChatSessionList } from "@/components/ChatSessionList";
import { ChatHeader } from "@/components/chat/ChatHeader";
import { ChatConversation } from "@/components/chat/ChatConversation";
import { useReportedAction } from "@/components/chat/use-reported-action";

const LIST_SURFACE = "border-r border-ink/10 bg-ink/[0.015] dark:border-white/10 dark:bg-dark-surface/60";

export default function ChatPage() {
  // Chat state (sessions, messages, sending, the send/delete functions)
  // lives in ChatProvider at the (app) layout level, not here — so an
  // in-flight query and its eventual answer survive switching to another
  // page and back (or to the AskDIQ drawer), instead of being dropped when
  // this page component unmounts.
  const { sessions, activeSessionId, selectSession, startNewSession, renameSession, removeSession } = useChat();
  const reported = useReportedAction();
  // Phones: the session list is a drawer over the conversation, closed by
  // default, so the 256 px column no longer takes most of the width.
  const [historyOpen, setHistoryOpen] = useState(false);
  const closeAfter =
    <A extends unknown[]>(fn: (...args: A) => unknown) =>
    (...args: A) => {
      setHistoryOpen(false);
      return fn(...args);
    };

  return (
    <div className="relative flex h-full">
      {historyOpen ? (
        <button
          type="button"
          aria-label="Close chat history"
          className="absolute inset-0 z-10 bg-black/30 md:hidden"
          onClick={() => setHistoryOpen(false)}
        />
      ) : null}
      <ChatSessionList
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelect={closeAfter(reported("Couldn't open that chat", selectSession))}
        onNew={closeAfter(reported("Couldn't start a new chat", startNewSession))}
        onRename={reported("Couldn't rename the chat", renameSession)}
        onDelete={reported("Couldn't delete the chat", removeSession)}
        className={`${historyOpen ? "" : "hidden"} absolute inset-y-0 left-0 z-20 w-72 max-w-[85vw] bg-surface-page shadow-xl md:static md:z-auto md:flex md:w-64 md:max-w-none md:shrink-0 md:shadow-none ${LIST_SURFACE}`}
      />

      <div className="flex h-full min-w-0 flex-1 flex-col bg-surface-page">
        <div className="flex items-center border-b border-ink/10 px-3 py-2 dark:border-white/10 md:hidden">
          <button
            type="button"
            onClick={() => setHistoryOpen((open) => !open)}
            aria-expanded={historyOpen}
            className="rounded-lg border border-ink/10 px-2.5 py-1 text-xs font-medium text-fg-muted transition hover:text-fg dark:border-white/10"
          >
            Chats ({sessions.length})
          </button>
        </div>
        <ChatHeader />
        <ChatConversation variant="page" />
      </div>
    </div>
  );
}
