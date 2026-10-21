/**
 * Recency labels for the chat history list: when a conversation was last
 * active ("5m ago", "Yesterday 14:05", "Mon 09:30", "12 Oct") and which
 * group it belongs to (Today / Yesterday / Previous 7 days / Older).
 * Calendar days are the viewer's local days.
 */

export type SessionGroup = "Today" | "Yesterday" | "Previous 7 days" | "Older";

export const SESSION_GROUPS: readonly SessionGroup[] = ["Today", "Yesterday", "Previous 7 days", "Older"];

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const RECENT_DAYS = 7;

const clockFormat = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
const weekdayFormat = new Intl.DateTimeFormat("en-GB", { weekday: "short" });
const dayMonthFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const dayMonthYearFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });

function startOfDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** Whole local calendar days between `date` and `now` (0 = today). */
function daysAgo(date: Date, now: number): number {
  const a = new Date(startOfDay(date.getTime()));
  const b = new Date(startOfDay(now));
  // Calendar arithmetic (not ms / 24 h) so DST changes never shift a day.
  const utcA = Date.UTC(a.getFullYear(), a.getMonth(), a.getDate());
  const utcB = Date.UTC(b.getFullYear(), b.getMonth(), b.getDate());
  return Math.round((utcB - utcA) / (24 * HOUR_MS));
}

export function sessionGroup(date: Date, now: number = Date.now()): SessionGroup {
  const days = daysAgo(date, now);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < RECENT_DAYS) return "Previous 7 days";
  return "Older";
}

/** Last-active label: relative today, then clock / weekday / calendar date. */
export function formatLastActive(date: Date, now: number = Date.now()): string {
  const days = daysAgo(date, now);
  if (days <= 0) {
    const diff = Math.max(0, now - date.getTime());
    if (diff < MINUTE_MS) return "just now";
    if (diff < HOUR_MS) return `${Math.floor(diff / MINUTE_MS)}m ago`;
    return `${Math.floor(diff / HOUR_MS)}h ago`;
  }
  if (days === 1) return `Yesterday ${clockFormat.format(date)}`;
  if (days < RECENT_DAYS) return `${weekdayFormat.format(date)} ${clockFormat.format(date)}`;
  const sameYear = date.getFullYear() === new Date(now).getFullYear();
  return (sameYear ? dayMonthFormat : dayMonthYearFormat).format(date);
}
