/**
 * Session and sign-in cookies (DEPLOYMENT.md "frontend contract", steps 2, 3,
 * 5 and 8). Server-only: used by middleware.ts, the proxy and the /auth route
 * handlers. Every cookie here is httpOnly, so client JavaScript never sees a
 * session id or OIDC state. Edge-safe (no Node APIs).
 */
import type { NextResponse } from "next/server";

export const DEFAULT_SESSION_COOKIE_NAME = "askdiq_session";
/** Login-CSRF binding: holds the `state` of the authorize URL we redirected to. */
export const OIDC_STATE_COOKIE = "askdiq_oidc_state";
/** Validated post-login target (`next`), carried across the IdP round trip. */
export const LOGIN_NEXT_COOKIE = "askdiq_login_next";
/** Both sign-in cookies live 10 minutes (the backend's state TTL). */
export const SIGN_IN_COOKIE_MAX_AGE_SECONDS = 600;

const COOKIE_NAME_RE = /^[A-Za-z0-9_-]{1,64}$/;
const STATE_RE = /^[A-Za-z0-9_-]{16,256}$/;

/** `SESSION_COOKIE_NAME` (must match the backend), validated. */
export function sessionCookieName(): string {
  const configured = process.env.SESSION_COOKIE_NAME;
  return configured && COOKIE_NAME_RE.test(configured) ? configured : DEFAULT_SESSION_COOKIE_NAME;
}

/** `SESSION_COOKIE_SECURE=true` over HTTPS. */
export function cookieSecureFromEnv(): boolean {
  return process.env.SESSION_COOKIE_SECURE === "true";
}

const baseOptions = (secure: boolean) => ({ httpOnly: true, sameSite: "lax" as const, path: "/", secure });

export function setSessionCookie(response: NextResponse, session: string, expires: Date, secure: boolean): void {
  response.cookies.set(sessionCookieName(), session, { ...baseOptions(secure), expires });
}

/** `secure` must match how the cookie was set, or HTTPS browsers may keep the old one. */
export function clearSessionCookie(response: NextResponse, secure: boolean = cookieSecureFromEnv()): void {
  response.cookies.set(sessionCookieName(), "", { ...baseOptions(secure), maxAge: 0 });
}

/** The `state` query parameter of an authorize URL, when well formed. */
export function stateFromAuthorizeUrl(authorizeUrl: string): string | null {
  try {
    const url = new URL(authorizeUrl);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    const state = url.searchParams.get("state");
    return state && STATE_RE.test(state) ? state : null;
  } catch {
    return null;
  }
}

/** Step 2: bind the browser to the `state` it is about to send to the IdP. */
export function setOidcStateCookie(response: NextResponse, state: string): void {
  response.cookies.set(OIDC_STATE_COOKIE, state, {
    ...baseOptions(cookieSecureFromEnv()),
    maxAge: SIGN_IN_COOKIE_MAX_AGE_SECONDS,
  });
}

export function setLoginNextCookie(response: NextResponse, next: string): void {
  response.cookies.set(LOGIN_NEXT_COOKIE, next, {
    ...baseOptions(cookieSecureFromEnv()),
    maxAge: SIGN_IN_COOKIE_MAX_AGE_SECONDS,
  });
}

/** Step 3: the sign-in cookies are single use. */
export function clearSignInCookies(response: NextResponse): void {
  for (const name of [OIDC_STATE_COOKIE, LOGIN_NEXT_COOKIE]) {
    response.cookies.set(name, "", { ...baseOptions(cookieSecureFromEnv()), maxAge: 0 });
  }
}

/** Constant-time string comparison (state check). */
export function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
