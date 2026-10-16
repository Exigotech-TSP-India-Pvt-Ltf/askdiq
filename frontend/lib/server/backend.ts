/**
 * Server-only access to the FastAPI backend: base URL, service key and the
 * public origin of this app. Never imported by client components, so the
 * API key never reaches a browser bundle.
 */
import type { NextRequest } from "next/server";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
/** Upper bound on waiting for the backend to revoke a session at sign-out (app/auth/logout). */
export const LOGOUT_BACKEND_TIMEOUT_MS = 4000;

export function backendUrl(): string {
  return (process.env.API_BASE_URL ?? DEFAULT_BACKEND_URL).replace(/\/+$/, "");
}

/** `X-API-Key` (service key) and the client IP for the backend's audit trail. */
export function backendHeaders(req: NextRequest): Headers {
  const headers = new Headers();
  const apiKey = process.env.API_KEY;
  if (apiKey) headers.set("X-API-Key", apiKey);
  const forwardedFor = req.headers.get("x-forwarded-for");
  if (forwardedFor) headers.set("X-Forwarded-For", forwardedFor);
  const userAgent = req.headers.get("user-agent");
  if (userAgent) headers.set("User-Agent", userAgent);
  return headers;
}

/**
 * Public origin of this app (`APP_BASE_URL`), used for redirects and as the
 * `Origin` of server-originated state changes. Falls back to the request.
 */
export function appOrigin(req: NextRequest): string {
  const configured = process.env.APP_BASE_URL;
  if (configured) {
    try {
      return new URL(configured).origin;
    } catch {
      // fall through to the request origin
    }
  }
  return req.nextUrl.origin;
}

/** Absolute URL on this app for a same-origin path. */
export function appUrl(req: NextRequest, path: string): URL {
  return new URL(path, appOrigin(req));
}
