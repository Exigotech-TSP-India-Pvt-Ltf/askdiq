"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, ApiError } from "./api";
import { useAuth } from "./auth-context";
import { parseServerDate } from "./time";
import type { ChatMessage, ChatMessageOut, ChatSessionSummary } from "./types";

function newId() {
  return Math.random().toString(36).slice(2);
}

function toChatMessage(m: ChatMessageOut): ChatMessage {
  return {
    id: m.id,
    role: m.role,
    content: m.content,
    createdAt: parseServerDate(m.created_at).getTime(),
  };
}

interface ChatContextValue {
  sessions: ChatSessionSummary[];
  activeSessionId: string | null;
  messages: ChatMessage[];
  input: string;
  setInput: (value: string) => void;
  sending: boolean;
  loadingMessages: boolean;
  sendQuery: (text: string) => Promise<void>;
  deleteExchange: (messageId: string) => void;
  selectSession: (sessionId: string) => Promise<void>;
  startNewSession: () => Promise<void>;
  renameSession: (sessionId: string, title: string) => Promise<void>;
  removeSession: (sessionId: string) => Promise<void>;
}

const ChatContext = createContext<ChatContextValue | undefined>(undefined);

/**
 * Owns chat state at the (app) layout level — NOT inside the chat page
 * component — so an in-flight /query call (and its eventual answer) survives
 * switching to Documents/Metrics and back, instead of being dropped when the
 * chat page unmounts.
 *
 * History is server-authoritative (see backend app.core.sessions): each
 * session's messages live in the database, scoped by session_id, so
 * switching sessions never leaks one conversation's context into another.
 */
export function ChatProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth();

  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);

  const loadSession = useCallback(
    async (sessionId: string) => {
      if (!token) return;
      setLoadingMessages(true);
      try {
        const detail = await api.getSession(token, sessionId);
        setActiveSessionId(detail.id);
        setMessages(detail.messages.map(toChatMessage));
      } catch {
        // Session may have been deleted elsewhere — fall back to a blank slate.
        setActiveSessionId(null);
        setMessages([]);
      } finally {
        setLoadingMessages(false);
      }
    },
    [token]
  );

  // Initial load: fetch the session list and resume the most recent one.
  useEffect(() => {
    if (!token) {
      setSessions([]);
      setActiveSessionId(null);
      setMessages([]);
      return;
    }

    api
      .listSessions(token)
      .then((list) => {
        setSessions(list);
        if (list.length > 0) {
          void loadSession(list[0].id);
        }
      })
      .catch(() => {
        // No sessions yet, or a transient error — either way, start blank;
        // the first sendQuery will create a session automatically.
        setSessions([]);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function refreshSessions() {
    if (!token) return;
    try {
      setSessions(await api.listSessions(token));
    } catch {
      // Non-fatal — the sidebar just won't refresh this time.
    }
  }

  async function sendQuery(text: string) {
    if (!token || !text.trim() || sending) return;

    const trimmedText = text.trim();

    const userMessage: ChatMessage = {
      id: newId(),
      role: "user",
      content: trimmedText,
      createdAt: Date.now(),
    };

    const assistantId = newId();

    const pendingMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      createdAt: Date.now(),
      pending: true,
    };

    setMessages((prev) => [...prev, userMessage, pendingMessage]);
    setInput("");
    setSending(true);

    try {
      const response = await api.query(token, {
        query: trimmedText,
        session_id: activeSessionId,
      });

      setMessages((prev) =>
        prev.map((message) => {
          if (message.id === assistantId) {
            return {
              ...message,
              id: response.assistant_message_id || message.id,
              pending: false,
              content: response.answer,
              response,
            };
          }
          if (message.id === userMessage.id) {
            return { ...message, id: response.user_message_id || message.id };
          }
          return message;
        })
      );

      // The session may have just been created, or an expired one silently
      // rotated to a new one — either way, sync the pointer + sidebar list.
      if (response.session_id && response.session_id !== activeSessionId) {
        setActiveSessionId(response.session_id);
      }
      await refreshSessions();
    } catch (err) {
      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                pending: false,
                error:
                  err instanceof ApiError
                    ? err.message
                    : "DeployIQ couldn't answer that just now. Please try again.",
              }
            : message
        )
      );
    } finally {
      setSending(false);
    }
  }

  function deleteExchange(messageId: string) {
    if (!token || !activeSessionId) return;

    setMessages((prev) => {
      const index = prev.findIndex((m) => m.id === messageId);
      if (index === -1) return prev;

      const message = prev[index];
      const pairIndex = message.role === "user" ? index + 1 : index - 1;
      const pairId =
        pairIndex >= 0 && pairIndex < prev.length ? prev[pairIndex].id : null;

      return prev.filter((m) => m.id !== messageId && m.id !== pairId);
    });

    // Best-effort backend delete — the pair is matched server-side by the
    // shared turn_index, so any one of the two message ids is enough.
    api.deleteExchange(token, activeSessionId, messageId).catch(() => {
      // Already removed locally; a stale row surviving server-side isn't
      // worth surfacing an error for here.
    });
  }

  async function selectSession(sessionId: string) {
    if (sessionId === activeSessionId) return;
    await loadSession(sessionId);
  }

  async function startNewSession() {
    if (!token) return;
    const created = await api.createSession(token);
    setSessions((prev) => [created, ...prev]);
    setActiveSessionId(created.id);
    setMessages([]);
  }

  async function renameSession(sessionId: string, title: string) {
    if (!token || !title.trim()) return;
    const updated = await api.renameSession(token, sessionId, title.trim());
    setSessions((prev) =>
      prev.map((s) => (s.id === sessionId ? updated : s))
    );
  }

  async function removeSession(sessionId: string) {
    if (!token) return;
    await api.deleteSession(token, sessionId);
    setSessions((prev) => prev.filter((s) => s.id !== sessionId));

    if (sessionId === activeSessionId) {
      const remaining = sessions.filter((s) => s.id !== sessionId);
      if (remaining.length > 0) {
        await loadSession(remaining[0].id);
      } else {
        setActiveSessionId(null);
        setMessages([]);
      }
    }
  }

  const value = useMemo<ChatContextValue>(
    () => ({
      sessions,
      activeSessionId,
      messages,
      input,
      setInput,
      sending,
      loadingMessages,
      sendQuery,
      deleteExchange,
      selectSession,
      startNewSession,
      renameSession,
      removeSession,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sessions, activeSessionId, messages, input, sending, loadingMessages, token]
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat() {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat must be used within ChatProvider");
  return ctx;
}
