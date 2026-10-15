// Server-only proxy to the FastAPI backend. Keeps API_KEY out of the browser
// bundle — only this route handler (which runs on the server) ever reads it.
//
// Session contract (DEPLOYMENT.md "frontend contract", steps 1, 6 and 8): the
// httpOnly session cookie is read here and sent as `X-Session`; state-changing
// methods also carry `X-Requested-With: askdiq` and the browser's own
// `Origin` (CSRF check in the backend). Cookies are never passed to the
// client and backend `Set-Cookie` headers are dropped. A 401 or a sign-out
// deletes the cookie.
import { NextRequest, NextResponse } from "next/server";
import { backendHeaders, backendUrl } from "@/lib/server/backend";
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
/** Response headers passed back to the browser (never Set-Cookie). */
const PASS_THROUGH_HEADERS = ["content-type", "content-disposition", "etag", "cache-control", "vary", "retry-after"];
/** Returns the raw session id in its body: only the /auth/callback handler may call it. */
const BLOCKED_PATHS = new Set(["auth/sso/callback"]);
const SSO_LOGIN_PATH = "auth/sso/login";
const LOGOUT_PATH = "auth/logout";

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
  }

  try {
    const res = await fetch(targetUrl, {
      method: req.method,
      headers: proxyHeaders(req),
      body: bodyBuffer,
      cache: "no-store",
    });

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
    const cause = (err as { cause?: { code?: string }; code?: string; message?: string } | null) ?? {};
    const code = cause.cause?.code || cause.code || "ECONNREFUSED";
    console.error(`[Proxy] Failed to reach backend at ${targetUrl}: ${cause.message ?? String(err)}`);
    return NextResponse.json(
      {
        detail: `Backend service is unreachable at ${BACKEND_URL} (${code}). Please ensure the FastAPI backend server is running.`,
      },
      { status: 502 }
    );
  }
}

export async function GET(req: NextRequest, { params }: { params: { path: string[] } }) {
  return forward(req, params.path);
}
export async function POST(req: NextRequest, { params }: { params: { path: string[] } }) {
  return forward(req, params.path);
}
export async function PUT(req: NextRequest, { params }: { params: { path: string[] } }) {
  return forward(req, params.path);
}
export async function PATCH(req: NextRequest, { params }: { params: { path: string[] } }) {
  return forward(req, params.path);
}
export async function DELETE(req: NextRequest, { params }: { params: { path: string[] } }) {
  return forward(req, params.path);
}
