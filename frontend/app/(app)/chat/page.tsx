"use client";

import { useChat } from "@/lib/chat-context";
import { ChatSessionList } from "@/components/ChatSessionList";
import { ChatHeader } from "@/components/chat/ChatHeader";
import { ChatConversation } from "@/components/chat/ChatConversation";
import { useReportedAction } from "@/components/chat/use-reported-action";

export default function ChatPage() {
  // Chat state (sessions, messages, sending, the send/delete functions)
  // lives in ChatProvider at the (app) layout level, not here — so an
  // in-flight query and its eventual answer survive switching to another
  // page and back (or to the AskDIQ drawer), instead of being dropped when
  // this page component unmounts.
  const { sessions, activeSessionId, selectSession, startNewSession, renameSession, removeSession } = useChat();
  const reported = useReportedAction();

  return (
    <div className="flex h-full">
      <ChatSessionList
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelect={reported("Couldn't open that chat", selectSession)}
        onNew={reported("Couldn't start a new chat", startNewSession)}
        onRename={reported("Couldn't rename the chat", renameSession)}
        onDelete={reported("Couldn't delete the chat", removeSession)}
      />

      <div className="flex h-full min-w-0 flex-1 flex-col bg-surface-page">
        <ChatHeader />
        <ChatConversation variant="page" />
      </div>
    </div>
  );
}
