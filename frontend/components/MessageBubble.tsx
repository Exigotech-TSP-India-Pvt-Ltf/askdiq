"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence } from "motion/react";
import type { ChatMessage, ClarificationOption } from "@/lib/types";
import { renderFormattedText } from "@/lib/format-text";
import { useChat } from "@/lib/chat-context";
import type { FeedbackDetails, ReasonTag } from "@/lib/feedback-api";
import { ClarificationChips } from "@/components/chat/ClarificationChips";
import { FeedbackReasonPanel } from "@/components/chat/FeedbackReasonPanel";
import { SourceChips, citedSources } from "@/components/chat/SourceChips";
import { ActionTooltip } from "@/components/ui/ActionTooltip";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/components/ui/ToastProvider";
import { focusRing } from "@/components/ui/styles";

type FeedbackFn = (messageId: string, rating: number, comment?: string, details?: FeedbackDetails) => Promise<void>;

const THUMBS_UP = 5;
const THUMBS_DOWN = 1;
/** Every message action icon is the same size (px). */
const ICON_SIZE = 14;

/** Icon-only message action: a 32 px touch target (28 px from `sm`), visible focus ring. */
const actionButton = `inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors disabled:cursor-not-allowed disabled:opacity-50 sm:h-7 sm:w-7 ${focusRing}`;
const actionIdle =
  "text-slate-500 hover:bg-surface-muted hover:text-slate-800 dark:text-slate-400 dark:hover:bg-white/[0.06] dark:hover:text-slate-200";

function CopyIcon() {
  return (
    <svg width={ICON_SIZE} height={ICON_SIZE} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width={ICON_SIZE} height={ICON_SIZE} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width={ICON_SIZE} height={ICON_SIZE} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

function ThumbsUpIcon({ filled }: { filled?: boolean }) {
  return (
    <svg
      width={ICON_SIZE}
      height={ICON_SIZE}
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M7 10v12" />
      <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2h0a3.13 3.13 0 0 1 3 3.88Z" />
    </svg>
  );
}

function ThumbsDownIcon({ filled }: { filled?: boolean }) {
  return (
    <svg
      width={ICON_SIZE}
      height={ICON_SIZE}
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M17 14V2" />
      <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22h0a3.13 3.13 0 0 1-3-3.88Z" />
    </svg>
  );
}

function MessageSquareIcon() {
  return (
    <svg width={ICON_SIZE} height={ICON_SIZE} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}

/** Local time of a message ("14:05"), with the full date as the tooltip. */
function MessageTime({ at }: { at: number }) {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return null;
  return (
    <time dateTime={date.toISOString()} title={date.toLocaleString()} className="tabular-nums">
      {date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
    </time>
  );
}

export function MessageBubble({
  message,
  onDelete,
  onFeedback,
  onRetry,
  onClarify,
  onSomethingElse,
  clarificationChosenId = null,
  clarificationDisabled = false,
  extraActions,
}: {
  message: ChatMessage;
  /** Deletes the question/answer pair; a rejection is shown under the bubble. */
  onDelete?: () => void | Promise<void>;
  onFeedback?: FeedbackFn;
  /** Shown on a failed answer: re-sends the question. */
  onRetry?: () => void;
  /** Clarifying question: the user picked an option. */
  onClarify?: (option: ClarificationOption) => void;
  /** Clarifying question: "Something else…" (type a free-text answer). */
  onSomethingElse?: () => void;
  clarificationChosenId?: string | null;
  /** The clarification was answered (or a reply is in flight). */
  clarificationDisabled?: boolean;
  /** More icon-only actions for the right-hand group (e.g. the admin trace link). */
  extraActions?: ReactNode;
}) {
  const isUser = message.role === "user";
  const clarification = message.clarification ?? message.response?.clarification ?? null;
  const [copied, setCopied] = useState(false);
  const [activeRating, setActiveRating] = useState<number | null>(
    message.feedbackRating ?? null
  );
  const [commentOpen, setCommentOpen] = useState(false);
  const [commentText, setCommentText] = useState("");
  const [feedbackError, setFeedbackError] = useState<string | null>(null);
  const [savingFeedback, setSavingFeedback] = useState(false);
  const [reasonsOpen, setReasonsOpen] = useState(false);
  const [reasons, setReasons] = useState<ReasonTag[]>([]);
  const [correctedAnswer, setCorrectedAnswer] = useState("");
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const toast = useToast();

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
  let chatFeedback: FeedbackFn | undefined;
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

  /** Thumbs-down detail, sent only when there is some (keeps the one-click call minimal). */
  const currentDetails = (): FeedbackDetails | undefined => {
    const corrected = correctedAnswer.trim();
    if (reasons.length === 0 && !corrected) return undefined;
    return { reason_tags: reasons, corrected_answer: corrected || null };
  };

  /** Upserts the rating; a resubmission updates the existing feedback row. */
  const submit = async (rating: number, comment: string | undefined, details: FeedbackDetails | undefined) => {
    if (!submitFn) return true;
    setSavingFeedback(true);
    setFeedbackError(null);
    try {
      if (details) await submitFn(message.id, rating, comment, details);
      else await submitFn(message.id, rating, comment);
      return true;
    } catch (err: unknown) {
      setFeedbackError(err instanceof Error && err.message ? err.message : "Failed to submit rating. Please try again.");
      return false;
    } finally {
      setSavingFeedback(false);
    }
  };

  const handleRatingClick = async (ratingVal: number) => {
    if (savingFeedback) return;
    if (ratingVal === THUMBS_DOWN && activeRating === THUMBS_DOWN) {
      setReasonsOpen((open) => !open);
      return;
    }
    const prevRating = activeRating;
    // Optimistic: the thumb lights up before the request settles.
    setActiveRating(ratingVal);
    setReasonsOpen(ratingVal === THUMBS_DOWN);
    const details = ratingVal === THUMBS_DOWN ? currentDetails() : undefined;
    const ok = await submit(ratingVal, commentText || undefined, details);
    if (!ok) {
      setActiveRating(prevRating);
      setReasonsOpen(false);
      return;
    }
    if (ratingVal === THUMBS_UP) {
      toast.show({ title: "Thanks for the feedback", tone: "success" });
    }
  };

  const toggleReason = (tag: ReasonTag) => {
    setReasons((list) => (list.includes(tag) ? list.filter((t) => t !== tag) : [...list, tag]));
  };

  const handleReasonsSubmit = async () => {
    if (savingFeedback) return;
    const ok = await submit(THUMBS_DOWN, commentText.trim() || undefined, currentDetails());
    if (ok) {
      setReasonsOpen(false);
      toast.show({ title: "Feedback sent", description: "Thanks, this helps us improve the answers.", tone: "success" });
    }
  };

  const handleCommentSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeRating || savingFeedback) return;
    const details = activeRating === THUMBS_DOWN ? currentDetails() : undefined;
    const ok = await submit(activeRating, commentText.trim(), details);
    if (ok) {
      setCommentOpen(false);
      if (submitFn) toast.show({ title: "Note saved", tone: "success" });
    }
  };

  const confirmDelete = async () => {
    if (!onDelete) return;
    setDeleteError(null);
    try {
      await onDelete();
    } catch (err: unknown) {
      setDeleteError(
        err instanceof Error && err.message
          ? `Couldn't delete this exchange: ${err.message}`
          : "Couldn't delete this exchange. Please try again.",
      );
    } finally {
      setConfirmingDelete(false);
    }
  };

  const deleteDialog = onDelete ? (
    <ConfirmDialog
      open={confirmingDelete}
      title="Delete this exchange?"
      description="The question and its answer are removed from this chat. This cannot be undone."
      confirmLabel="Delete"
      tone="danger"
      onConfirm={confirmDelete}
      onCancel={() => setConfirmingDelete(false)}
    />
  ) : null;

  const deleteErrorNotice = deleteError ? (
    <p className="text-[11px] text-rose-600 dark:text-rose-400" role="alert">
      {deleteError}
    </p>
  ) : null;

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%]">
          <div className="rounded-2xl rounded-tr-sm bg-brand-600 px-4 py-2.5 text-sm text-white">
            {message.content}
          </div>
          <div className="mt-1 flex items-center justify-end gap-0.5 text-ink/35 dark:text-slate-500">
            <span className="mr-1 text-[11px]">
              <MessageTime at={message.createdAt} />
            </span>
            <ActionTooltip label={copied ? "Copied" : "Copy"}>
              <button type="button" onClick={handleCopy} className={`${actionButton} ${actionIdle}`} aria-label="Copy message">
                {copied ? <CheckIcon /> : <CopyIcon />}
              </button>
            </ActionTooltip>
            {onDelete && (
              <ActionTooltip label="Delete" align="end">
                <button
                  type="button"
                  onClick={() => setConfirmingDelete(true)}
                  className={`${actionButton} ${actionIdle}`}
                  aria-label="Delete this exchange"
                >
                  <TrashIcon />
                </button>
              </ActionTooltip>
            )}
          </div>
          {deleteErrorNotice}
          {deleteDialog}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="relative max-w-[80%] space-y-2">
        <div data-tour="chat-answer" className="rounded-2xl rounded-tl-sm border border-slate-200/90 dark:border-dark-border bg-white dark:bg-dark-card px-4 py-3 text-sm text-slate-900 dark:text-slate-100 shadow-sm">
          {message.pending ? (
            <span className="inline-flex items-center gap-1.5 text-slate-500 dark:text-slate-400" role="status" aria-label="Thinking">
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 dark:bg-slate-500 [animation-delay:-0.3s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 dark:bg-slate-500 [animation-delay:-0.15s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 dark:bg-slate-500" />
            </span>
          ) : message.error ? (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5" role="alert">
              <span className="text-red-600 dark:text-red-400">{message.error}</span>
              {onRetry ? (
                <button
                  type="button"
                  data-tour="chat-retry"
                  onClick={onRetry}
                  className="rounded-md border border-line px-2 py-0.5 text-xs font-medium text-fg-muted transition hover:border-brand-300 hover:text-fg focus:outline-none focus:ring-1 focus:ring-brand-500"
                >
                  Retry
                </button>
              ) : null}
            </div>
          ) : clarification ? (
            <p>{clarification.question}</p>
          ) : (
            renderFormattedText(message.content)
          )}
        </div>

        {!message.pending && !message.error && clarification ? (
          <ClarificationChips
            clarification={clarification}
            onSelect={onClarify}
            onSomethingElse={onSomethingElse}
            chosenId={clarificationChosenId}
            disabled={clarificationDisabled || !onClarify}
          />
        ) : null}

        {!message.pending && !message.error && <SourceChips sources={citedSources(message.response)} />}

        {!message.pending && !message.error && (
          <div className="mt-1 flex flex-wrap items-center gap-0.5 text-[11px]" data-tour="chat-feedback">
            {/* Feedback: Thumbs Up */}
            <ActionTooltip label="Helpful">
              <button
                type="button"
                onClick={() => handleRatingClick(THUMBS_UP)}
                aria-label="Helpful response"
                aria-pressed={activeRating === 5}
                disabled={savingFeedback}
                className={`${actionButton} ${
                  activeRating === 5
                    ? "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-300 dark:bg-emerald-950/40 dark:text-emerald-300 dark:ring-emerald-700"
                    : actionIdle
                }`}
              >
                <ThumbsUpIcon filled={activeRating === 5} />
              </button>
            </ActionTooltip>

            {/* Feedback: Thumbs Down */}
            <ActionTooltip label="Unhelpful">
              <button
                type="button"
                onClick={() => handleRatingClick(THUMBS_DOWN)}
                aria-label="Unhelpful response"
                aria-pressed={activeRating === 1}
                disabled={savingFeedback}
                className={`${actionButton} ${
                  activeRating === 1
                    ? "bg-rose-50 text-rose-700 ring-1 ring-rose-300 dark:bg-rose-950/40 dark:text-rose-300 dark:ring-rose-700"
                    : actionIdle
                }`}
              >
                <ThumbsDownIcon filled={activeRating === 1} />
              </button>
            </ActionTooltip>

            {/* Add Note trigger if rating has been selected */}
            {activeRating !== null && (
              <ActionTooltip label={commentText ? "Edit note" : "Add note"}>
                <button
                  type="button"
                  onClick={() => setCommentOpen(!commentOpen)}
                  aria-label="Add feedback note"
                  aria-expanded={commentOpen}
                  className={`${actionButton} ${actionIdle}`}
                >
                  <MessageSquareIcon />
                </button>
              </ActionTooltip>
            )}

            <div className="ml-auto flex items-center gap-0.5 text-slate-400 dark:text-slate-500" data-tour="chat-message-actions">
              <span className="mr-1">
                <MessageTime at={message.createdAt} />
              </span>
              {/* Copy */}
              <ActionTooltip label={copied ? "Copied" : "Copy"}>
                <button type="button" onClick={handleCopy} className={`${actionButton} ${actionIdle}`} aria-label="Copy message">
                  {copied ? <CheckIcon /> : <CopyIcon />}
                </button>
              </ActionTooltip>

              {/* Delete */}
              {onDelete && (
                <ActionTooltip label="Delete" align="end">
                  <button
                    type="button"
                    onClick={() => setConfirmingDelete(true)}
                    className={`${actionButton} ${actionIdle}`}
                    aria-label="Delete this exchange"
                  >
                    <TrashIcon />
                  </button>
                </ActionTooltip>
              )}
              {extraActions}
            </div>
          </div>
        )}

        {deleteErrorNotice}
        {deleteDialog}

        {/* Feedback Error Notice */}
        {feedbackError && (
          <p className="text-[11px] text-rose-600 dark:text-rose-400" role="alert">
            {feedbackError}
          </p>
        )}

        {/* Thumbs-down reason chips (pop preset) */}
        <AnimatePresence>
          {reasonsOpen && activeRating === THUMBS_DOWN ? (
            <FeedbackReasonPanel
              key="reasons"
              reasons={reasons}
              onToggleReason={toggleReason}
              correctedAnswer={correctedAnswer}
              onCorrectedAnswerChange={setCorrectedAnswer}
              onSubmit={handleReasonsSubmit}
              onCancel={() => setReasonsOpen(false)}
              saving={savingFeedback}
            />
          ) : null}
        </AnimatePresence>

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
