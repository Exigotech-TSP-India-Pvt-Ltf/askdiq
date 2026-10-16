import type { FeedbackItem, TriageStatus } from "@/lib/feedback-api";

/** Allowed transitions (backend `service.triage`). Promoted is final. */
const TRANSITIONS: Record<TriageStatus, readonly TriageStatus[]> = {
  new: ["reviewed", "promoted", "dismissed"],
  reviewed: ["promoted", "dismissed"],
  dismissed: ["new"],
  promoted: [],
};

export function canTransition(from: TriageStatus, to: TriageStatus): boolean {
  return TRANSITIONS[from].includes(to);
}

export type TriageAction = "reviewed" | "dismissed" | "promoted";

export const ACTION_LABELS: Record<TriageAction, { verb: string; done: string; key: string }> = {
  reviewed: { verb: "Mark reviewed", done: "Marked reviewed", key: "r" },
  dismissed: { verb: "Dismiss", done: "Dismissed", key: "d" },
  promoted: { verb: "Promote to test", done: "Promoted to golden cases", key: "p" },
};

export const STATUS_BADGE: Record<TriageStatus, string> = {
  new: "bg-status-info-bg text-status-info-fg",
  reviewed: "bg-surface-muted text-fg-muted",
  promoted: "bg-status-ok-bg text-status-ok-fg",
  dismissed: "bg-surface-muted text-fg-subtle line-through",
};

export function ratingTone(rating: number): string {
  if (rating >= 4) return "bg-status-ok-bg text-status-ok-fg";
  if (rating === 3) return "bg-status-warn-bg text-status-warn-fg";
  return "bg-status-danger-bg text-status-danger-fg";
}

/** Replaces one item in a cached list page (by id). */
export function replaceItem<T extends { items: FeedbackItem[] }>(
  page: T | undefined,
  id: string,
  update: (item: FeedbackItem) => FeedbackItem,
): T | undefined {
  if (!page) return page;
  return { ...page, items: page.items.map((it) => (it.id === id ? update(it) : it)) };
}
