/**
 * Open-redirect protection for the `next` parameter (planchanges4 F1.1.1).
 * Edge-safe (no Node APIs): used by middleware.ts, the auth route handlers
 * and the login page.
 */

/** Paths that must never be a post-login target (they would loop). */
const BLOCKED_PREFIXES = ["/login", "/auth/", "/api/", "/mock-idp", "/suspended"];
const MAX_NEXT_LENGTH = 2048;
const PLACEHOLDER_ORIGIN = "http://same-origin.invalid";
// Any ASCII control character or backslash (browsers treat "\" like "/").
const UNSAFE_CHARS = /[\u0000-\u001f\u007f\\]/;

/**
 * Returns `raw` as a same-origin path (`/path?query#hash`) or `null`.
 * Rejects absolute URLs, protocol-relative `//host`, backslash tricks,
 * control characters, over-long values and the auth pages themselves.
 */
export function safeNextPath(raw: string | null | undefined): string | null {
  if (!raw || raw.length > MAX_NEXT_LENGTH) return null;
  if (!raw.startsWith("/") || raw.startsWith("//") || UNSAFE_CHARS.test(raw)) return null;
  let url: URL;
  try {
    url = new URL(raw, PLACEHOLDER_ORIGIN);
  } catch {
    return null;
  }
  if (url.origin !== PLACEHOLDER_ORIGIN) return null;
  const path = `${url.pathname}${url.search}${url.hash}`;
  if (path === "/") return path;
  if (BLOCKED_PREFIXES.some((prefix) => url.pathname === prefix || url.pathname.startsWith(prefix.endsWith("/") ? prefix : `${prefix}/`))) {
    return null;
  }
  return path;
}

/** `safeNextPath` with a fallback. */
export function nextPathOr(raw: string | null | undefined, fallback: string): string {
  return safeNextPath(raw) ?? fallback;
}
