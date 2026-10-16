/**
 * Route gate decision (newplan §7.7, planchanges4 F1.2.3). Pure, so every
 * status x route pair is covered by a table-driven test.
 *
 * - no session            -> "login"     (redirect to /login?next=)
 * - suspended             -> "suspended" (suspended page, no shell)
 * - pending / rejected, or active without a role:
 *     /chat               -> "app"       (minimal sidebar: Chat only)
 *     any dashboard route -> "access-required"
 * - active with a role    -> "app"       (full shell, permission-filtered)
 */
import type { AuthMe } from "@/lib/auth-api";

export type GateDecision = "loading" | "login" | "suspended" | "access-required" | "app";

/** Routes open to every signed-in, non-suspended user. */
export const CHAT_ROUTES = ["/chat"];

export function isChatRoute(pathname: string | null | undefined): boolean {
  if (!pathname) return false;
  return CHAT_ROUTES.some((route) => pathname === route || pathname.startsWith(`${route}/`));
}

export function hasDashboardAccess(me: AuthMe | null | undefined): boolean {
  return Boolean(me && me.status === "active" && me.role);
}

export function gateDecision(
  me: AuthMe | null | undefined,
  pathname: string | null | undefined,
  isLoading = false,
): GateDecision {
  if (isLoading && !me) return "loading";
  if (!me) return "login";
  if (me.status === "suspended") return "suspended";
  if (hasDashboardAccess(me)) return "app";
  return isChatRoute(pathname) ? "app" : "access-required";
}

/** Where a fresh session lands: `next` for active users, Chat for everyone else. */
export function postLoginPath(status: string, next: string | null): string {
  if (status === "active") return next ?? "/";
  return next && isChatRoute(next.split(/[?#]/)[0]) ? next : "/chat";
}
