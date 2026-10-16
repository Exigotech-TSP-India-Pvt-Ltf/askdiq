import type { TourId } from "./types";

/** Pathname → tour id. Longest prefix wins; detail pages reuse their list page's tour. */
const ROUTES: [string, TourId][] = [
  ["/admin/user-management", "user-management"],
  ["/admin/evaluation-live", "evaluation"],
  ["/evaluation-live", "evaluation"],
  ["/admin/conversations", "conversations"],
  ["/admin/governance", "governance"],
  ["/admin/analytics", "analytics"],
  ["/admin/settings", "settings"],
  ["/admin/feedback", "feedback"],
  ["/admin/usage", "usage"],
  ["/admin/users", "visitors"],
  ["/documents", "documents"],
  ["/chat", "chat"],
  ["/admin", "dashboard"],
];

export function tourIdForPath(pathname: string): TourId | null {
  for (const [prefix, id] of ROUTES) {
    if (pathname === prefix || pathname.startsWith(`${prefix}/`)) return id;
  }
  return null;
}
