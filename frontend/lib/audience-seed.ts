/**
 * Server-known starting audience (VA-54).
 *
 * The audience used to be read from `window.location` and localStorage in
 * the provider's first render, so the server HTML (no window: "overview")
 * and the first client render ("users") differed, which raised React
 * hydration errors (minified 418 and 422) and re-rendered the page after first paint.
 * Now the choice is also kept in a cookie, the middleware copies a valid
 * `?audience=` into a request header, and the root layout resolves one value
 * that both the server render and the first client render start from.
 *
 * Server-safe (no "use client"): imported by the middleware and the root layout.
 */
import type { Audience } from "./types";

export const AUDIENCE_COOKIE = "askdiq_audience";
/** Request header set (or removed) by the middleware from `?audience=`; never trusted from the client. */
export const AUDIENCE_HEADER = "x-askdiq-audience";
/** One year: a UI preference, not a credential. */
export const AUDIENCE_COOKIE_MAX_AGE_S = 60 * 60 * 24 * 365;

const AUDIENCES: ReadonlySet<string> = new Set<Audience>(["overview", "users", "testing"]);

export function isAudience(value: unknown): value is Audience {
  return typeof value === "string" && AUDIENCES.has(value);
}

/** The URL's audience wins, then the remembered one, else "overview". Unknown values are ignored. */
export function resolveInitialAudience(fromUrl: string | null | undefined, fromCookie: string | null | undefined): Audience {
  if (isAudience(fromUrl)) return fromUrl;
  if (isAudience(fromCookie)) return fromCookie;
  return "overview";
}

/** `Set-Cookie`-style string for `document.cookie` (validated value only). */
export function audienceCookie(audience: Audience, secure: boolean): string {
  return `${AUDIENCE_COOKIE}=${audience}; Path=/; Max-Age=${AUDIENCE_COOKIE_MAX_AGE_S}; SameSite=Lax${secure ? "; Secure" : ""}`;
}

/** Reads the audience cookie from a `document.cookie` string. */
export function audienceFromCookieString(cookie: string): Audience | null {
  for (const part of cookie.split(";")) {
    const [name, value] = part.trim().split("=");
    if (name === AUDIENCE_COOKIE && isAudience(value)) return value;
  }
  return null;
}
