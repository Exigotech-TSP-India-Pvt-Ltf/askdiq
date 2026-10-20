"use client";

import { useState, type KeyboardEvent } from "react";
import type { ChatSessionSummary } from "@/lib/types";
import { RelativeTime } from "@/components/ui/RelativeTime";

function PencilIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

export function ChatSessionList({
  sessions,
  activeSessionId,
  onSelect,
  onNew,
  onRename,
  onDelete,
  onIntent,
  className = "w-64 shrink-0 border-r border-ink/10 bg-ink/[0.015] dark:border-white/10 dark:bg-dark-surface/60",
}: {
  sessions: ChatSessionSummary[];
  activeSessionId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  /** Hover / focus on a row: warm that conversation so it opens instantly. */
  onIntent?: (id: string) => void;
  /** Container sizing / surface (the /chat sidebar by default; the AskDIQ drawer passes its own). */
  className?: string;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");

  function startRename(id: string, currentTitle: string) {
    setEditingId(id);
    setDraftTitle(currentTitle);
  }

  function commitRename(id: string) {
    if (draftTitle.trim() && draftTitle.trim() !== sessions.find((s) => s.id === id)?.title) {
      onRename(id, draftTitle.trim());
    }
    setEditingId(null);
  }

  function onRenameKeyDown(e: KeyboardEvent<HTMLInputElement>, id: string) {
    if (e.key === "Enter") commitRename(id);
    if (e.key === "Escape") setEditingId(null);
  }

  return (
    <div className={`flex flex-col ${className}`} data-tour="chat-sessions">
      <div className="p-3">
        <button
          data-tour="chat-new"
          onClick={onNew}
          className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-xs font-medium text-white shadow-brand-glow transition hover:bg-brand-700"
        >
          <PlusIcon />
          New chat
        </button>
      </div>

      <div className="scrollbar-thin flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
        {sessions.length === 0 && (
          <p className="px-2 py-3 text-center text-xs text-ink/40 dark:text-slate-400">
            No chats yet — start one above.
          </p>
        )}

        {sessions.map((session) => {
          const active = session.id === activeSessionId;
          return (
            <div
              key={session.id}
              onClick={() => editingId !== session.id && onSelect(session.id)}
              onMouseEnter={onIntent ? () => onIntent(session.id) : undefined}
              onFocus={onIntent ? () => onIntent(session.id) : undefined}
              className={`group flex cursor-pointer items-start gap-1.5 rounded-lg px-2.5 py-2 text-xs transition ${
                active
                  ? "bg-brand-50 dark:bg-brand-950/40 text-brand-700 dark:text-brand-300 font-semibold"
                  : "text-ink/70 dark:text-slate-300 hover:bg-ink/5 dark:hover:bg-white/5"
              }`}
            >
              <div className="min-w-0 flex-1">
                {editingId === session.id ? (
                  <input
                    autoFocus
                    value={draftTitle}
                    onChange={(e) => setDraftTitle(e.target.value)}
                    onBlur={() => commitRename(session.id)}
                    onKeyDown={(e) => onRenameKeyDown(e, session.id)}
                    onClick={(e) => e.stopPropagation()}
                    className="w-full rounded border border-brand-300 dark:border-brand-600 bg-white dark:bg-dark-card text-ink dark:text-white px-1 py-0.5 text-xs outline-none"
                  />
                ) : (
                  <p className="truncate font-medium">{session.title}</p>
                )}
                <p className="mt-0.5 truncate text-[10px] text-ink/40 dark:text-slate-400">
                  <RelativeTime value={session.last_activity_at} />
                  {session.is_expired ? " · expired" : ""}
                </p>
              </div>

              <div className="flex shrink-0 items-center gap-1 opacity-0 transition focus-within:opacity-100 group-hover:opacity-100">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    startRename(session.id, session.title);
                  }}
                  title="Rename"
                  className="rounded p-1 text-ink/40 dark:text-slate-400 hover:bg-ink/10 dark:hover:bg-white/10 hover:text-ink dark:hover:text-white"
                >
                  <PencilIcon />
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    if (window.confirm("Delete this chat? This can't be undone.")) {
                      onDelete(session.id);
                    }
                  }}
                  title="Delete"
                  className="rounded p-1 text-ink/40 dark:text-slate-400 hover:bg-red-50 dark:hover:bg-red-950/40 hover:text-red-600 dark:hover:text-red-400"
                >
                  <TrashIcon />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
