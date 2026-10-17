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

/**
 * Marks requests from this proxy. The backend also accepts the service key
 * alone (no session) from the public website's server; with this marker an
 * anonymous browser behind the proxy can never take that path.
 */
export const PROXY_MARKER_HEADER = "X-AskDIQ-Proxy";

/**
 * Default wait for the backend's response headers on reads (GET / HEAD /
 * OPTIONS). A last-resort safety net, far above any healthy response time.
 */
export const DEFAULT_BACKEND_TIMEOUT_MS = 60_000;
/** Default for writes: chat answers (LLM) and synchronous file ingestion take longer. */
export const DEFAULT_BACKEND_WRITE_TIMEOUT_MS = 180_000;

function positiveMs(raw: string | undefined, fallback: number): number {
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

/** Default retries of an idempotent call after a transient failure. */
export const DEFAULT_BACKEND_RETRIES = 2;
const MAX_BACKEND_RETRIES = 5;

/**
 * Extra attempts for an idempotent backend call (reads, sign-out) after a
 * connection error or a 502 / 503 / 504 (`BACKEND_RETRIES`, 0 disables).
 */
export function backendRetries(): number {
  const raw = process.env.BACKEND_RETRIES?.trim();
  const value = raw ? Number(raw) : Number.NaN;
  return Number.isInteger(value) && value >= 0 ? Math.min(value, MAX_BACKEND_RETRIES) : DEFAULT_BACKEND_RETRIES;
}

/**
 * How long a server-side call waits for the backend's response headers before
 * giving up (`BACKEND_TIMEOUT_MS` / `BACKEND_WRITE_TIMEOUT_MS`). Bounds only
 * the wait for headers, so a streamed body (SSE) is never cut off.
 */
export function backendTimeoutMs(write: boolean): number {
  return write
    ? positiveMs(process.env.BACKEND_WRITE_TIMEOUT_MS, DEFAULT_BACKEND_WRITE_TIMEOUT_MS)
    : positiveMs(process.env.BACKEND_TIMEOUT_MS, DEFAULT_BACKEND_TIMEOUT_MS);
}

/** `X-API-Key` (service key), the proxy marker and the client IP for the backend's audit trail. */
const IP_RE = /^[0-9a-fA-F:.]{2,45}$/;

/**
 * The dashboard user's IP: first hop of X-Forwarded-For (set by nginx), else
 * X-Real-IP, else the socket address Next exposes. Null when none is a valid IP.
 */
export function browserIp(req: NextRequest): string | null {
  const candidates = [
    req.headers.get("x-forwarded-for")?.split(",")[0],
    req.headers.get("x-real-ip"),
    (req as NextRequest & { ip?: string }).ip,
  ];
  for (const raw of candidates) {
    const value = raw?.trim().replace(/^::ffff:/i, "");
    if (value && IP_RE.test(value)) return value;
  }
  return null;
}

export function backendHeaders(req: NextRequest): Headers {
  const headers = new Headers();
  const apiKey = process.env.API_KEY;
  if (apiKey) headers.set("X-API-Key", apiKey);
  headers.set(PROXY_MARKER_HEADER, "1");
  const forwardedFor = req.headers.get("x-forwarded-for");
  if (forwardedFor) headers.set("X-Forwarded-For", forwardedFor);
  else {
    const ip = browserIp(req);
    if (ip) headers.set("X-Forwarded-For", ip);
  }
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
