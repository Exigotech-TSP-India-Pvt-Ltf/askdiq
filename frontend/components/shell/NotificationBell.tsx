"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { BellOff, CheckCheck } from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";
import { focusRing } from "@/components/ui/styles";

export interface NotificationItem {
  id: string;
  title: string;
  description?: string;
  href?: string;
  /** Pre-formatted relative time, e.g. "5 min ago". */
  time?: string;
  tone?: "info" | "warn" | "danger";
}

const TONE_DOT: Record<NonNullable<NotificationItem["tone"]>, string> = {
  info: "bg-status-info",
  warn: "bg-status-warn",
  danger: "bg-status-danger",
};

const READ_STORAGE_KEY = "askdiq.notifications.read";
/** Remembered "read" keys (oldest dropped first). */
const MAX_READ_KEYS = 200;

/** An item counts as new again when its text changes (e.g. "3 access requests" → "4"). */
function readKey(item: NotificationItem): string {
  return `${item.id}|${item.title}`;
}

function loadReadKeys(): string[] {
  try {
    const raw = window.localStorage.getItem(READ_STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string").slice(-MAX_READ_KEYS) : [];
  } catch {
    return [];
  }
}

/**
 * Per-viewer "read" state for the notification feed (VA-48 "Mark all read"),
 * kept in this browser only. `unread` is the badge count.
 */
export function useNotificationReadState(items: readonly NotificationItem[]) {
  const [read, setRead] = useState<readonly string[]>([]);
  useEffect(() => setRead(loadReadKeys()), []);
  const readSet = useMemo(() => new Set(read), [read]);
  const unread = useMemo(() => items.filter((item) => !readSet.has(readKey(item))), [items, readSet]);
  const markAllRead = useCallback(() => {
    const next = Array.from(new Set([...read, ...items.map(readKey)])).slice(-MAX_READ_KEYS);
    setRead(next);
    try {
      window.localStorage.setItem(READ_STORAGE_KEY, JSON.stringify(next));
    } catch {
      // Storage unavailable: read state lasts for this page view only.
    }
  }, [items, read]);
  return { unread: unread.length, isUnread: (item: NotificationItem) => !readSet.has(readKey(item)), markAllRead };
}

interface NotificationListProps {
  items: readonly NotificationItem[];
  isUnread?: (item: NotificationItem) => boolean;
  /** Runs after an item link is followed (the menu closes). */
  onNavigate?: () => void;
  /** Rendered as menu items inside the profile menu. */
  itemRole?: "menuitem";
}

/**
 * The notification feed (access requests, incidents, alerts). Titles and
 * descriptions wrap to two lines with the full text as a tooltip (VA-48);
 * every item links to the page that needs attention.
 */
export function NotificationList({ items, isUnread, onNavigate, itemRole }: NotificationListProps) {
  if (items.length === 0) {
    return <EmptyState compact icon={BellOff} title="You're all caught up" description="Access requests and incidents will appear here." />;
  }
  return (
    <ul role={itemRole ? "none" : undefined} className="max-h-80 overflow-y-auto py-1">
      {items.map((item) => {
        const unread = isUnread?.(item) ?? true;
        const body = (
          <>
            <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${TONE_DOT[item.tone ?? "info"]} ${unread ? "" : "opacity-40"}`} aria-hidden="true" />
            <span className="min-w-0 flex-1">
              <span title={item.title} className={`line-clamp-2 wrap-break-word text-xs text-fg ${unread ? "font-semibold" : "font-medium"}`}>
                {item.title}
              </span>
              {item.description ? (
                <span title={item.description} className="line-clamp-2 wrap-break-word text-[11px] text-fg-muted">
                  {item.description}
                </span>
              ) : null}
            </span>
            {item.time ? <span className="shrink-0 whitespace-nowrap text-[10px] text-fg-subtle">{item.time}</span> : null}
          </>
        );
        const rowClass = `flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-surface-muted ${focusRing}`;
        return (
          <li key={item.id} role={itemRole ? "none" : undefined}>
            {item.href ? (
              <Link href={item.href} role={itemRole} tabIndex={itemRole ? -1 : undefined} onClick={onNavigate} className={rowClass}>
                {body}
              </Link>
            ) : (
              <div role={itemRole} tabIndex={itemRole ? -1 : undefined} className={rowClass}>
                {body}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** "Mark all read" footer action of the feed. */
export function MarkAllReadButton({ onClick, disabled, role }: { onClick: () => void; disabled: boolean; role?: "menuitem" }) {
  return (
    <button
      type="button"
      role={role}
      tabIndex={role ? -1 : undefined}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium text-brand-700 transition-colors hover:bg-surface-muted disabled:cursor-default disabled:opacity-50 dark:text-brand-300 ${focusRing}`}
    >
      <CheckCheck className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
      Mark all read
    </button>
  );
}
