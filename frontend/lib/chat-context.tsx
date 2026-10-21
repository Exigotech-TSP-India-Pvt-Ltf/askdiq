"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, ApiError } from "./api";
import { useAuth } from "./auth-context";
import { useBrand } from "./brand-context";
import { submitMessageFeedback, type FeedbackDetails } from "./feedback-api";
import { queryCache, useApiQuery } from "./query-cache";
import { parseServerDate } from "./time";
import type { ChatMessage, ChatMessageOut, ChatSessionDetail, ChatSessionSummary, ClarificationAnswer } from "./types";

/**
 * Query-cache keys (memory only: "chat/" is never persisted to storage). The
 * list is refreshed by the console live stream (new chats are `data`).
 */
export const CHAT_SESSIONS_KEY = "chat/sessions";
export const chatSessionKey = (sessionId: string) => `chat/session/${sessionId}`;
const SESSIONS_STALE_MS = 30_000;
const SESSION_STALE_MS = 60_000;
const NO_SESSIONS: ChatSessionSummary[] = [];

function newId() {
  return Math.random().toString(36).slice(2);
}

function toChatMessage(m: ChatMessageOut, sessionId: string): ChatMessage {
  return {
    sessionId,
    id: m.id,
    role: m.role,
    content: m.content,
    createdAt: parseServerDate(m.created_at).getTime(),
    feedbackRating: m.feedback_rating,
    requestId: m.request_id ?? null,
    clarification: m.clarification ?? null,
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
  /**
   * The open conversation is decided: the session list has loaded, the
   * first-load resume (if any) has started, and no conversation is loading.
   * Until then an empty `messages` must not be read as "new chat".
   */
  settled: boolean;
  /** `answers`: the text is the reply to the last clarification (all its answers at once). */
  sendQuery: (text: string, answers?: ClarificationAnswer[]) => Promise<void>;
  /** Upserts the rating; `details` carries thumbs-down reason chips and the corrected answer. */
  submitFeedback: (messageId: string, rating: number, comment?: string, details?: FeedbackDetails) => Promise<void>;
  /** Removes a question/answer pair; restores it and rejects if the server delete fails. */
  deleteExchange: (messageId: string) => Promise<void>;
  /** Re-sends the question behind a failed answer (the failed pair is dropped first). */
  retryExchange: (assistantMessageId: string) => Promise<void>;
  selectSession: (sessionId: string) => Promise<void>;
  /** Warms a past conversation (hover / focus on its row) so opening it is instant. */
  prefetchSession: (sessionId: string) => void;
  /** Local only: clears the conversation; the session is created with its first message. */
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
  const { assistantName } = useBrand();

  // Session list: cached (instant on every visit), revalidated in the
  // background and pushed by the live stream.
  const listQuery = useApiQuery<ChatSessionSummary[]>(
    token ? CHAT_SESSIONS_KEY : null,
    () => (token ? api.listSessions(token) : Promise.resolve([])),
    { staleTime: SESSIONS_STALE_MS },
  );
  const sessions = token ? listQuery.data ?? NO_SESSIONS : NO_SESSIONS;
  const setSessions = listQuery.setData;
  // What each conversation last showed here (opening it again is instant).
  const shown = useRef(new Map<string, ChatMessage[]>());
  const activeRef = useRef<string | null>(null);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  // "New chat" was chosen: the next send starts a fresh server session and
  // the first-load resume must not reopen the previous conversation.
  const freshRef = useRef(false);
  const [initialResolved, setInitialResolved] = useState(false);

  useEffect(() => {
    activeRef.current = activeSessionId;
  }, [activeSessionId]);
  // Remember what the open conversation shows (for an instant return to it).
  useEffect(() => {
    if (activeSessionId && !messages.some((m) => m.pending)) shown.current.set(activeSessionId, messages);
  }, [activeSessionId, messages]);

  const prefetchSession = useCallback(
    (sessionId: string) => {
      if (!token || shown.current.has(sessionId)) return;
      void queryCache.prefetch<ChatSessionDetail>(
        chatSessionKey(sessionId),
        () => api.getSession(token, sessionId),
        SESSION_STALE_MS,
      );
    },
    [token]
  );

  const loadSession = useCallback(
    async (sessionId: string) => {
      if (!token) return;
      // Cached conversation: shown at once, then revalidated in place.
      const cachedDetail = queryCache.getData<ChatSessionDetail>(chatSessionKey(sessionId));
      const cached =
        shown.current.get(sessionId) ?? cachedDetail?.messages.map((m) => toChatMessage(m, sessionId));
      activeRef.current = sessionId;
      if (cached) {
        setActiveSessionId(sessionId);
        setMessages(cached);
      } else {
        setLoadingMessages(true);
      }
      try {
        const detail = await api.getSession(token, sessionId);
        queryCache.setData(chatSessionKey(sessionId), detail);
        if (activeRef.current !== sessionId) return;
        setActiveSessionId(detail.id);
        setMessages((prev) =>
          prev.some((m) => m.pending) ? prev : detail.messages.map((m) => toChatMessage(m, detail.id))
        );
      } catch {
        if (activeRef.current !== sessionId) return;
        // Session may have been deleted elsewhere — fall back to a blank slate
        // that starts a NEW session (never the last same-IP one).
        shown.current.delete(sessionId);
        freshRef.current = true;
        setActiveSessionId(null);
        setMessages([]);
      } finally {
        if (activeRef.current === sessionId) setLoadingMessages(false);
      }
    },
    [token]
  );

  // Signed out: forget everything shown.
  useEffect(() => {
    if (token) return;
    shown.current.clear();
    freshRef.current = false;
    setActiveSessionId(null);
    setMessages([]);
  }, [token]);

  // First list for this sign-in: resume the most recent conversation.
  const resumed = useRef(false);
  useEffect(() => {
    if (!token) {
      resumed.current = false;
      setInitialResolved(false);
      return;
    }
    if (resumed.current) return;
    if (listQuery.data === undefined) {
      // The list failed: nothing to resume, show the new-chat state.
      if (listQuery.error) {
        resumed.current = true;
        setInitialResolved(true);
      }
      return;
    }
    resumed.current = true;
    const latest = listQuery.data.find((s) => s.message_count !== 0);
    if (latest && activeRef.current === null && !freshRef.current) void loadSession(latest.id);
    setInitialResolved(true);
  }, [token, listQuery.data, listQuery.error, loadSession]);
  const settled = Boolean(token) && initialResolved && !loadingMessages;

  async function refreshSessions() {
    if (!token) return;
    try {
      await listQuery.refetch();
    } catch {
      // Non-fatal — the sidebar just won't refresh this time.
    }
  }

  async function sendQuery(text: string, answers?: ClarificationAnswer[]) {
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
        // An opened chat (however old) resumes in place by its id; a blank
        // conversation always starts a fresh session.
        session_id: activeSessionId,
        new_session: activeSessionId === null,
        ...(answers?.length ? { clarification_answers: answers } : {}),
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
              clarification: response.clarification ?? null,
              sessionId: response.session_id || null,
            };
          }
          if (message.id === userMessage.id) {
            return {
              ...message,
              id: response.user_message_id || message.id,
              sessionId: response.session_id || null,
            };
          }
          return message;
        })
      );

      // The session was just created (new_session), or an unknown id was
      // replaced by a fresh one: sync the pointer + sidebar list.
      if (response.session_id) freshRef.current = false;
      if (response.session_id && response.session_id !== activeSessionId) {
        activeRef.current = response.session_id;
        setActiveSessionId(response.session_id);
      }
      if (response.session_id) queryCache.invalidate(chatSessionKey(response.session_id));
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
                    : `${assistantName} couldn't answer that just now. Please try again.`,
              }
            : message
        )
      );
    } finally {
      setSending(false);
    }
  }

  async function deleteExchange(messageId: string) {
    const target = messages.find((m) => m.id === messageId);
    const sessionId = target?.sessionId ?? activeSessionId;
    if (!token || !sessionId) return;

    const before = messages;
    setMessages((prev) => {
      const index = prev.findIndex((m) => m.id === messageId);
      if (index === -1) return prev;

      const message = prev[index];
      const pairIndex = message.role === "user" ? index + 1 : index - 1;
      const pairId =
        pairIndex >= 0 && pairIndex < prev.length ? prev[pairIndex].id : null;

      return prev.filter((m) => m.id !== messageId && m.id !== pairId);
    });

    // The pair is matched server-side by the shared turn_index, so any one of
    // the two message ids is enough. A failure puts the pair back so the
    // screen never disagrees with what a reload would show.
    try {
      await api.deleteExchange(token, sessionId, messageId);
    } catch (err) {
      setMessages(before);
      throw err;
    }
  }

  async function retryExchange(assistantMessageId: string) {
    const index = messages.findIndex((m) => m.id === assistantMessageId);
    const question = index > 0 ? messages[index - 1] : undefined;
    if (!question || question.role !== "user" || !messages[index].error) return;
    // A failed turn has no server ids to delete, so it is only dropped locally.
    setMessages((prev) => prev.filter((m) => m.id !== assistantMessageId && m.id !== question.id));
    await sendQuery(question.content);
  }

  async function selectSession(sessionId: string) {
    if (sessionId === activeSessionId) return;
    await loadSession(sessionId);
  }

  // No request: the session (and its list entry) is created by the first
  // message, so an unused "New chat" never leaves an empty row behind.
  async function startNewSession() {
    if (!token) return;
    freshRef.current = true;
    activeRef.current = null;
    setActiveSessionId(null);
    setMessages([]);
    setLoadingMessages(false);
  }

  async function renameSession(sessionId: string, title: string) {
    if (!token || !title.trim()) return;
    const updated = await api.renameSession(token, sessionId, title.trim());
    setSessions((prev) => (prev ?? []).map((s) => (s.id === sessionId ? updated : s)));
  }

  async function removeSession(sessionId: string) {
    if (!token) return;
    await api.deleteSession(token, sessionId);
    setSessions((prev) => (prev ?? []).filter((s) => s.id !== sessionId));
    shown.current.delete(sessionId);
    queryCache.setData(chatSessionKey(sessionId), undefined);

    if (sessionId === activeSessionId) {
      const remaining = sessions.filter((s) => s.id !== sessionId);
      if (remaining.length > 0) {
        await loadSession(remaining[0].id);
      } else {
        activeRef.current = null;
        setActiveSessionId(null);
        setMessages([]);
      }
    }
  }

  const submitFeedback = useCallback(
    async (messageId: string, rating: number, comment?: string, details?: FeedbackDetails) => {
      const target = messages.find((m) => m.id === messageId);
      // Rate in the message's own session (a reply may have landed in a
      // fresh session when the open one was deleted elsewhere).
      const sessionId = target?.sessionId ?? activeSessionId;
      // Never report "thanks" for a rating that was not sent.
      if (!token || !sessionId) throw new Error("Open a saved chat to rate its answers.");
      const prevRating = target?.feedbackRating;
      // Optimistic update
      setMessages((prev) =>
        prev.map((m) => (m.id === messageId ? { ...m, feedbackRating: rating } : m))
      );
      try {
        await submitMessageFeedback(token, sessionId, messageId, { rating, comment, ...details });
      } catch (err) {
        // Rollback on failure
        setMessages((prev) =>
          prev.map((m) => (m.id === messageId ? { ...m, feedbackRating: prevRating } : m))
        );
        throw err;
      }
    },
    [token, activeSessionId, messages]
  );

  const value = useMemo<ChatContextValue>(
    () => ({
      sessions,
      activeSessionId,
      messages,
      input,
      setInput,
      sending,
      loadingMessages,
      settled,
      sendQuery,
      submitFeedback,
      deleteExchange,
      retryExchange,
      selectSession,
      prefetchSession,
      startNewSession,
      renameSession,
      removeSession,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sessions, activeSessionId, messages, input, sending, loadingMessages, settled, token, submitFeedback, prefetchSession]
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat() {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat must be used within ChatProvider");
  return ctx;
}
