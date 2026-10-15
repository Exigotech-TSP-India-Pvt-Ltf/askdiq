"use client";

import { useEffect, useRef, useState } from "react";
import type { ChatMessage } from "@/lib/types";
import { renderFormattedText } from "@/lib/format-text";
import { useChat } from "@/lib/chat-context";

function CopyIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

function ThumbsUpIcon({ filled }: { filled?: boolean }) {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M7 10v12" />
      <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2h0a3.13 3.13 0 0 1 3 3.88Z" />
    </svg>
  );
}

function ThumbsDownIcon({ filled }: { filled?: boolean }) {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M17 14V2" />
      <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22h0a3.13 3.13 0 0 1-3-3.88Z" />
    </svg>
  );
}

function MessageSquareIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}

export function MessageBubble({
  message,
  onDelete,
  onFeedback,
}: {
  message: ChatMessage;
  onDelete?: () => void;
  onFeedback?: (messageId: string, rating: number, comment?: string) => Promise<void>;
}) {
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);
  const [activeRating, setActiveRating] = useState<number | null>(
    message.feedbackRating ?? null
  );
  const [commentOpen, setCommentOpen] = useState(false);
  const [commentText, setCommentText] = useState("");
  const [feedbackError, setFeedbackError] = useState<string | null>(null);
  const [savingFeedback, setSavingFeedback] = useState(false);

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  // Sync rehydrated rating from server message
  useEffect(() => {
    if (message.feedbackRating !== undefined && message.feedbackRating !== null) {
      setActiveRating(message.feedbackRating);
    }
  }, [message.feedbackRating]);

  // Handle focus when comment popover opens
  useEffect(() => {
    if (commentOpen) {
      textareaRef.current?.focus();
    }
  }, [commentOpen]);

  // Handle escape key closing comment popover
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && commentOpen) {
        setCommentOpen(false);
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [commentOpen]);

  // Obtain feedback submission handler
  let chatFeedback: ((messageId: string, rating: number, comment?: string) => Promise<void>) | undefined;
  try {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const chat = useChat();
    chatFeedback = chat?.submitFeedback;
  } catch {
    // Context unavailable (e.g. isolated component test)
  }

  const submitFn = onFeedback || chatFeedback;

  const handleCopy = () => {
    navigator.clipboard?.writeText(message.content).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const handleRatingClick = async (ratingVal: number) => {
    if (savingFeedback) return;
    const prevRating = activeRating;
    setActiveRating(ratingVal);
    setFeedbackError(null);

    if (submitFn) {
      setSavingFeedback(true);
      try {
        await submitFn(message.id, ratingVal, commentText || undefined);
        // Prompt optional comment if not provided
        if (!commentText) {
          setCommentOpen(true);
        }
      } catch (err: any) {
        setActiveRating(prevRating);
        setFeedbackError(err?.message || "Failed to submit rating. Please try again.");
      } finally {
        setSavingFeedback(false);
      }
    }
  };

  const handleCommentSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeRating || savingFeedback) return;

    if (submitFn) {
      setSavingFeedback(true);
      setFeedbackError(null);
      try {
        await submitFn(message.id, activeRating, commentText.trim());
        setCommentOpen(false);
      } catch (err: any) {
        setFeedbackError(err?.message || "Failed to save comment.");
      } finally {
        setSavingFeedback(false);
      }
    } else {
      setCommentOpen(false);
    }
  };

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%]">
          <div className="rounded-2xl rounded-tr-sm bg-brand-600 px-4 py-2.5 text-sm text-white">
            {message.content}
          </div>
          <div className="mt-1 flex items-center justify-end gap-2 text-ink/35">
            <button
              type="button"
              onClick={handleCopy}
              className="inline-flex items-center gap-1 text-[11px] transition hover:text-ink/70"
              aria-label="Copy message"
            >
              {copied ? <CheckIcon /> : <CopyIcon />}
              {copied ? "Copied" : "Copy"}
            </button>
            {onDelete && (
              <button
                type="button"
                onClick={onDelete}
                className="inline-flex items-center gap-1 text-[11px] transition hover:text-ink/70"
                aria-label="Delete this exchange"
              >
                <TrashIcon />
                Delete
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="relative max-w-[80%] space-y-2">
        <div className="rounded-2xl rounded-tl-sm border border-slate-200/90 dark:border-dark-border bg-white dark:bg-dark-card px-4 py-3 text-sm text-slate-900 dark:text-slate-100 shadow-sm">
          {message.pending ? (
            <span className="inline-flex items-center gap-1.5 text-slate-500 dark:text-slate-400" role="status" aria-label="Thinking">
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 dark:bg-slate-500 [animation-delay:-0.3s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 dark:bg-slate-500 [animation-delay:-0.15s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 dark:bg-slate-500" />
            </span>
          ) : message.error ? (
            <span className="text-red-600 dark:text-red-400">{message.error}</span>
          ) : (
            renderFormattedText(message.content)
          )}
        </div>

        {!message.pending && !message.error && (
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
            {/* Feedback: Thumbs Up */}
            <button
              type="button"
              onClick={() => handleRatingClick(5)}
              aria-label="Helpful response"
              aria-pressed={activeRating === 5}
              disabled={savingFeedback}
              className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 transition focus:outline-none focus:ring-1 focus:ring-brand-500 ${
                activeRating === 5
                  ? "bg-emerald-50 dark:bg-emerald-950/40 font-medium text-emerald-700 dark:text-emerald-300 ring-1 ring-emerald-300 dark:ring-emerald-700"
                  : "text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
              }`}
            >
              <ThumbsUpIcon filled={activeRating === 5} />
              <span>Helpful</span>
            </button>

            {/* Feedback: Thumbs Down */}
            <button
              type="button"
              onClick={() => handleRatingClick(1)}
              aria-label="Unhelpful response"
              aria-pressed={activeRating === 1}
              disabled={savingFeedback}
              className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 transition focus:outline-none focus:ring-1 focus:ring-brand-500 ${
                activeRating === 1
                  ? "bg-rose-50 dark:bg-rose-950/40 font-medium text-rose-700 dark:text-rose-300 ring-1 ring-rose-300 dark:ring-rose-700"
                  : "text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
              }`}
            >
              <ThumbsDownIcon filled={activeRating === 1} />
              <span>Unhelpful</span>
            </button>

            {/* Add Note trigger if rating has been selected */}
            {activeRating !== null && (
              <button
                type="button"
                onClick={() => setCommentOpen(!commentOpen)}
                aria-label="Add feedback note"
                aria-expanded={commentOpen}
                className="inline-flex items-center gap-1 text-slate-500 dark:text-slate-400 transition hover:text-slate-800 dark:hover:text-slate-200 focus:outline-none focus:ring-1 focus:ring-brand-500"
              >
                <MessageSquareIcon />
                <span>{commentText ? "Edit note" : "Add note"}</span>
              </button>
            )}

            <div className="ml-auto flex items-center gap-2 text-slate-400 dark:text-slate-500">
              {/* Copy */}
              <button
                type="button"
                onClick={handleCopy}
                className="inline-flex items-center gap-1 transition hover:text-slate-700 dark:hover:text-slate-300 focus:outline-none focus:ring-1 focus:ring-brand-500"
                aria-label="Copy message"
              >
                {copied ? <CheckIcon /> : <CopyIcon />}
                <span>{copied ? "Copied" : "Copy"}</span>
              </button>

              {/* Delete */}
              {onDelete && (
                <button
                  type="button"
                  onClick={onDelete}
                  className="inline-flex items-center gap-1 transition hover:text-slate-700 dark:hover:text-slate-300 focus:outline-none focus:ring-1 focus:ring-brand-500"
                  aria-label="Delete this exchange"
                >
                  <TrashIcon />
                  <span>Delete</span>
                </button>
              )}
            </div>
          </div>
        )}

        {/* Feedback Error Notice */}
        {feedbackError && (
          <p className="text-[11px] text-rose-600 dark:text-rose-400" role="alert">
            {feedbackError}
          </p>
        )}

        {/* Comment Popover / Expansion */}
        {commentOpen && (
          <div
            ref={popoverRef}
            role="dialog"
            aria-label="Feedback comment dialog"
            className="mt-2 w-full rounded-xl border border-slate-200/90 dark:border-dark-border bg-white dark:bg-dark-card p-3 shadow-md"
          >
            <form onSubmit={handleCommentSubmit} className="space-y-2">
              <div className="flex items-center justify-between text-xs text-slate-600 dark:text-slate-300">
                <span>Why was this answer {activeRating === 5 ? "helpful" : "unhelpful"}?</span>
                <span className="text-[10px] text-slate-400">{commentText.length}/1000</span>
              </div>
              <textarea
                ref={textareaRef}
                value={commentText}
                onChange={(e) => setCommentText(e.target.value.slice(0, 1000))}
                rows={2}
                maxLength={1000}
                placeholder="Optional: Tell us what went well or what could be improved. Do not include personal information."
                className="w-full resize-none rounded-lg border border-slate-200 dark:border-dark-border bg-slate-50/50 dark:bg-dark-surface p-2 text-xs text-slate-900 dark:text-slate-100 placeholder-slate-400 dark:placeholder-slate-500 outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500"
              />
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setCommentOpen(false)}
                  className="rounded px-2.5 py-1 text-xs text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={savingFeedback}
                  className="rounded bg-brand-600 px-3 py-1 text-xs font-medium text-white transition hover:bg-brand-700 disabled:opacity-50"
                >
                  {savingFeedback ? "Saving…" : "Save note"}
                </button>
              </div>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}
