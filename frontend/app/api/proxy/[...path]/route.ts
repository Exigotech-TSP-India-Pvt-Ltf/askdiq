// Server-only proxy to the FastAPI backend. Keeps API_KEY out of the browser
// bundle — only this route handler (which runs on the server) ever reads it.
//
// Session contract (docs/deployment/docker-and-auth-contract.md "frontend contract", steps 1, 6 and 8): the
// httpOnly session cookie is read here and sent as `X-Session`; state-changing
// methods also carry `X-Requested-With: askdiq` and the browser's own
// `Origin` (CSRF check in the backend). Cookies are never passed to the
// client and backend `Set-Cookie` headers are dropped. A 401 or a sign-out
// deletes the cookie.
import { NextRequest, NextResponse } from "next/server";
import { backendHeaders, backendRetries, browserIp, backendTimeoutMs, backendUrl } from "@/lib/server/backend";
import {
  clearSessionCookie,
  sessionCookieName,
  setOidcStateCookie,
  stateFromAuthorizeUrl,
} from "@/lib/server/auth-cookies";

const MAX_BODY_BYTES = 15 * 1024 * 1024; // 15 MB
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const CSRF_HEADER_VALUE = "askdiq";
const HTTP_UNAUTHORIZED = 401;
const HTTP_NO_CONTENT = 204;
const HTTP_NOT_MODIFIED = 304;
/** Non-standard (nginx) status for a request the client abandoned. */
const HTTP_CLIENT_CLOSED_REQUEST = 499;
const HTTP_GATEWAY_TIMEOUT = 504;
const MS_PER_SECOND = 1000;
/** Upstream answers worth one more try (gateway / overload / timeout). */
const RETRYABLE_STATUSES = new Set([502, 503, 504]);
const RETRY_BASE_MS = 250;
/** Response headers passed back to the browser (never Set-Cookie). */
const PASS_THROUGH_HEADERS = ["content-type", "content-disposition", "etag", "cache-control", "vary", "retry-after"];
/** Returns the raw session id in its body: only the /auth/callback handler may call it. */
const BLOCKED_PATHS = new Set(["auth/sso/callback"]);
const SSO_LOGIN_PATH = "auth/sso/login";
const LOGOUT_PATH = "auth/logout";
const QUERY_PATH = "query";

/** Stamps the dashboard user's IP on a chat query so traces and logs show who asked. */
function withClientIp(body: Buffer, ip: string | null): Buffer {
  if (!ip) return body;
  try {
    const parsed = JSON.parse(body.toString("utf8")) as Record<string, unknown>;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed) && !parsed.ip_address) {
      return Buffer.from(JSON.stringify({ ...parsed, ip_address: ip }));
    }
  } catch {
    // not JSON: forward untouched
  }
  return body;
}

function proxyHeaders(req: NextRequest): Headers {
  const headers = backendHeaders(req);
  const incomingContentType = req.headers.get("content-type");
  if (incomingContentType) {
    headers.set("Content-Type", incomingContentType);
  } else if (req.method !== "GET" && req.method !== "HEAD") {
    headers.set("Content-Type", "application/json");
  }
  const clientId = req.headers.get("x-client-id");
  if (clientId) headers.set("X-Client-Id", clientId);
  const auth = req.headers.get("authorization");
  if (auth) headers.set("Authorization", auth);
  const ifNoneMatch = req.headers.get("if-none-match");
  if (ifNoneMatch) headers.set("If-None-Match", ifNoneMatch);

  const session = req.cookies.get(sessionCookieName())?.value;
  if (session) headers.set("X-Session", session);
  if (!SAFE_METHODS.has(req.method)) {
    headers.set("X-Requested-With", CSRF_HEADER_VALUE);
    // Forward the browser's Origin unchanged (never synthesised): the backend
    // rejects state changes whose Origin is not APP_BASE_URL's.
    const origin = req.headers.get("origin");
    if (origin) headers.set("Origin", origin);
  }
  return headers;
}

/** Step 2 for callers that start sign-in through the proxy: bind the state cookie. */
async function ssoLoginResponse(res: Response, headers: Headers): Promise<NextResponse> {
  const text = await res.text();
  const response = new NextResponse(text, { status: res.status, headers });
  if (res.ok) {
    try {
      const url = (JSON.parse(text) as { authorize_url?: unknown }).authorize_url;
      const state = typeof url === "string" ? stateFromAuthorizeUrl(url) : null;
      if (state) setOidcStateCookie(response, state);
    } catch {
      // not JSON: pass through without a state cookie (callback will reject)
    }
  }
  response.headers.set("Cache-Control", "no-store");
  return response;
}

/**
 * Abort signal for one backend call: fires when the backend has not sent its
 * response headers within `timeoutMs` (call `headersReceived()` once they
 * arrive, so a streamed body such as SSE is never cut off) or when the
 * browser goes away (so the backend sees the disconnect).
 *
 * `req.signal` may already be aborted by the time this runs (the route
 * handler awaits `params` — and, for a body, `req.arrayBuffer()` — before
 * calling this, so the browser can disconnect in that gap). `addEventListener`
 * never fires for an event that already happened, so that case is checked
 * explicitly instead of relying only on a future "abort" event.
 */
function upstreamSignal(req: NextRequest, timeoutMs: number) {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort(new DOMException(`No response headers within ${timeoutMs} ms`, "TimeoutError"));
  }, timeoutMs);
  if (req.signal?.aborted) {
    controller.abort(req.signal.reason);
  } else {
    req.signal?.addEventListener("abort", () => controller.abort(req.signal.reason), { once: true });
  }
  return {
    signal: controller.signal,
    headersReceived: () => clearTimeout(timer),
    timedOut: () => timedOut,
  };
}

/** Reads and sign-out are idempotent, so a transient failure may be retried. */
function isIdempotent(method: string, joined: string): boolean {
  return SAFE_METHODS.has(method) || (method === "POST" && joined === LOGOUT_PATH);
}

/** Exponential backoff with full jitter: attempt 1 -> 250-500 ms, 2 -> 500-1000 ms. */
function retryDelayMs(attempt: number): number {
  const base = RETRY_BASE_MS * 2 ** (attempt - 1);
  return base + Math.random() * base;
}

function sleep(ms: number, signal: AbortSignal | undefined): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

class UpstreamTimeout extends Error {}

/**
 * One backend call with a bounded wait for headers. Idempotent calls are
 * retried (jittered backoff) after a connection error or a 502 / 503 / 504,
 * so a blip or a restart does not reach the user. A timeout is not retried:
 * it is already the last-resort limit.
 */
async function fetchUpstream(
  req: NextRequest,
  targetUrl: string,
  init: RequestInit,
  timeoutMs: number,
  retries: number,
): Promise<Response> {
  for (let attempt = 0; ; attempt += 1) {
    const upstream = upstreamSignal(req, timeoutMs);
    try {
      const res = await fetch(targetUrl, { ...init, signal: upstream.signal });
      upstream.headersReceived();
      if (attempt >= retries || !RETRYABLE_STATUSES.has(res.status) || req.signal?.aborted) return res;
      await res.body?.cancel().catch(() => undefined);
    } catch (err) {
      upstream.headersReceived();
      if (upstream.timedOut()) throw new UpstreamTimeout();
      if (attempt >= retries || req.signal?.aborted) throw err;
    }
    console.warn(`[Proxy] Retrying ${init.method} ${targetUrl} (attempt ${attempt + 2} of ${retries + 1})`);
    await sleep(retryDelayMs(attempt + 1), req.signal);
  }
}

async function forward(req: NextRequest, path: string[]): Promise<Response> {
  // Path traversal & SSRF hygiene (OWASP A10)
  for (const segment of path) {
    if (segment === ".." || segment.includes("/") || segment.includes("\\")) {
      return NextResponse.json({ detail: "Invalid request path" }, { status: 400 });
    }
  }
  const joined = path.join("/");
  if (BLOCKED_PATHS.has(joined)) {
    return NextResponse.json({ detail: "Not Found" }, { status: 404 });
  }

  const BACKEND_URL = backendUrl();
  const targetUrl = `${BACKEND_URL}/${joined}${req.nextUrl.search}`;

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  let bodyBuffer: Buffer | undefined;

  if (hasBody) {
    const arrayBuf = await req.arrayBuffer();
    if (arrayBuf.byteLength > MAX_BODY_BYTES) {
      return NextResponse.json({ detail: "Payload exceeds maximum allowed size (15MB)" }, { status: 413 });
    }
    bodyBuffer = Buffer.from(arrayBuf);
    if (joined === QUERY_PATH && req.method === "POST") bodyBuffer = withClientIp(bodyBuffer, browserIp(req));
  }

  const timeoutMs = backendTimeoutMs(hasBody);
  const retries = isIdempotent(req.method, joined) ? backendRetries() : 0;
  try {
    const res = await fetchUpstream(
      req,
      targetUrl,
      { method: req.method, headers: proxyHeaders(req), body: bodyBuffer ? new Uint8Array(bodyBuffer) : undefined, cache: "no-store" },
      timeoutMs,
      retries,
    );

    const outHeaders = new Headers();
    for (const name of PASS_THROUGH_HEADERS) {
      const value = res.headers.get(name);
      if (value) outHeaders.set(name, value);
    }

    let response: NextResponse;
    if (joined === SSO_LOGIN_PATH && req.method === "GET") {
      response = await ssoLoginResponse(res, outHeaders);
    } else if (res.status === HTTP_NO_CONTENT || res.status === HTTP_NOT_MODIFIED) {
      response = new NextResponse(null, { status: res.status, headers: outHeaders });
    } else {
      response = new NextResponse(res.body, { status: res.status, headers: outHeaders });
    }

    const signedOut = joined === LOGOUT_PATH && res.ok;
    if ((res.status === HTTP_UNAUTHORIZED || signedOut) && req.cookies.has(sessionCookieName())) {
      clearSessionCookie(response);
    }
    return response;
  } catch (err: unknown) {
    if (err instanceof UpstreamTimeout) {
      const seconds = Math.round(timeoutMs / MS_PER_SECOND);
      console.error(`[Proxy] Backend at ${targetUrl} sent no response within ${seconds} s`);
      return NextResponse.json(
        { detail: `The backend did not respond within ${seconds} seconds. Please try again.` },
        { status: HTTP_GATEWAY_TIMEOUT, headers: { "Cache-Control": "no-store" } },
      );
    }
    if (req.signal.aborted) {
      // The browser went away (navigation, reload, unmount): the upstream call
      // was cancelled on purpose. Nothing failed and nobody reads this reply.
      return new NextResponse(null, { status: HTTP_CLIENT_CLOSED_REQUEST });
    }
    const cause = (err as { cause?: { code?: string }; code?: string; message?: string; name?: string } | null) ?? {};
    const code = cause.cause?.code || cause.code || cause.name || "ECONNREFUSED";
    console.error(`[Proxy] Failed to reach backend at ${targetUrl} (${code}): ${cause.message || String(err)}`);
    return NextResponse.json(
      {
        detail: `Backend service is unreachable at ${BACKEND_URL} (${code}). Please ensure the FastAPI backend server is running.`,
      },
      { status: 502 }
    );
  }
}

export async function GET(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await params).path);
}
export async function POST(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await params).path);
}
export async function PUT(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await params).path);
}
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await params).path);
}
export async function DELETE(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await params).path);
}
