"use client";

import { useState, type KeyboardEvent } from "react";
import type { ChatSessionSummary } from "@/lib/types";
import { parseServerDate } from "@/lib/time";

function relativeTime(iso: string): string {
  const diffMs = Date.now() - parseServerDate(iso).getTime();
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

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
}: {
  sessions: ChatSessionSummary[];
  activeSessionId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
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
    <div className="flex w-64 shrink-0 flex-col border-r border-ink/10 bg-ink/[0.015]">
      <div className="p-3">
        <button
          onClick={onNew}
          className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-brand-700"
        >
          <PlusIcon />
          New chat
        </button>
      </div>

      <div className="scrollbar-thin flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
        {sessions.length === 0 && (
          <p className="px-2 py-3 text-center text-xs text-ink/40">
            No chats yet — start one above.
          </p>
        )}

        {sessions.map((session) => {
          const active = session.id === activeSessionId;
          return (
            <div
              key={session.id}
              onClick={() => editingId !== session.id && onSelect(session.id)}
              className={`group flex cursor-pointer items-start gap-1.5 rounded-lg px-2.5 py-2 text-xs transition ${
                active ? "bg-brand-50 text-brand-700" : "text-ink/70 hover:bg-ink/5"
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
                    className="w-full rounded border border-brand-300 bg-white px-1 py-0.5 text-xs outline-none"
                  />
                ) : (
                  <p className="truncate font-medium">{session.title}</p>
                )}
                <p className="mt-0.5 truncate text-[10px] text-ink/40">
                  {relativeTime(session.last_activity_at)}
                  {session.is_expired ? " · expired" : ""}
                </p>
              </div>

              <div className="flex shrink-0 items-center gap-1 opacity-0 transition group-hover:opacity-100">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    startRename(session.id, session.title);
                  }}
                  title="Rename"
                  className="rounded p-1 text-ink/40 hover:bg-ink/10 hover:text-ink"
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
                  className="rounded p-1 text-ink/40 hover:bg-red-50 hover:text-red-600"
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
