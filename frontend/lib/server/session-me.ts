/**
 * Server-side `/auth/me` preload for the root layout. Handing the result to
 * `AuthProvider` as `initialMe` lets the first HTML response carry the whole
 * shell (sidebar, header, page skeletons) instead of a full-screen spinner,
 * and removes the client's `/auth/me` round trip that every page's data
 * requests used to wait behind.
 *
 * Server only: reads the httpOnly session cookie and the service key, which
 * never reach a client bundle. The response is per-user and the page is
 * rendered per request (`headers()`), so it is never cached or shared.
 */
import { parseMe, type AuthMe } from "@/lib/auth-api";
import { backendUrl } from "@/lib/server/backend";

/** Upper bound on the preload; past it the client fetches `/auth/me` itself. */
export const ME_PRELOAD_TIMEOUT_MS = 1500;

export interface MeRequestContext {
  /** Raw session cookie value; no preload without one. */
  session: string | undefined;
  forwardedFor?: string | null;
  userAgent?: string | null;
}

/**
 * The signed-in user (`AuthMe`), or `undefined` when there is none to hand
 * over (no cookie, 401, timeout, backend error). `undefined` keeps the
 * client-side fetch as the fallback, so a rejected session still goes through
 * the proxy, which clears the stale cookie.
 */
export async function preloadMe(
  ctx: MeRequestContext,
  fetchImpl: typeof fetch = fetch,
): Promise<AuthMe | undefined> {
  if (!ctx.session) return undefined;
  const headers = new Headers({ Accept: "application/json", "X-Session": ctx.session });
  const apiKey = process.env.API_KEY;
  if (apiKey) headers.set("X-API-Key", apiKey);
  if (ctx.forwardedFor) headers.set("X-Forwarded-For", ctx.forwardedFor);
  if (ctx.userAgent) headers.set("User-Agent", ctx.userAgent);
  try {
    const res = await fetchImpl(`${backendUrl()}/auth/me`, {
      headers,
      cache: "no-store",
      signal: AbortSignal.timeout(ME_PRELOAD_TIMEOUT_MS),
    });
    if (!res.ok) return undefined;
    return parseMe(await res.json()) ?? undefined;
  } catch {
    return undefined;
  }
}
