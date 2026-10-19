// Sign out (docs/deployment/docker-and-auth-contract.md "frontend contract", step 8). Revokes the server
// session when the backend answers in time, and ALWAYS clears the session
// cookie, so signing out never depends on a slow or unreachable backend.
// Kept apart from /api/proxy so the sign-out bound below is independent of
// the proxy's own timeouts.
import { NextResponse, type NextRequest } from "next/server";
import { LOGOUT_BACKEND_TIMEOUT_MS, appOrigin, backendHeaders, backendUrl } from "@/lib/server/backend";
import { clearSessionCookie, cookieSecureFromEnv, sessionCookieName } from "@/lib/server/auth-cookies";

export const dynamic = "force-dynamic";

const CSRF_HEADER = "x-requested-with";
const CSRF_HEADER_VALUE = "askdiq";

/**
 * Same-origin guard (OWASP A01): the custom header cannot be sent cross-site
 * without a CORS preflight, and a present Origin must be this app's.
 */
function isSameOriginRequest(req: NextRequest): boolean {
  if (req.headers.get(CSRF_HEADER) !== CSRF_HEADER_VALUE) return false;
  const origin = req.headers.get("origin");
  if (!origin) return true;
  const allowed = new Set([appOrigin(req), req.nextUrl.origin].map((o) => o.toLowerCase()));
  return allowed.has(origin.replace(/\/+$/, "").toLowerCase());
}

/** True when the browser reached us over HTTPS (directly or via the reverse proxy). */
function isHttps(req: NextRequest): boolean {
  const forwarded = req.headers.get("x-forwarded-proto")?.split(",")[0]?.trim();
  return (forwarded ?? req.nextUrl.protocol.replace(":", "")) === "https";
}

/** Best effort: false when the backend is down, slow or refuses. */
async function revokeOnBackend(req: NextRequest, session: string): Promise<boolean> {
  const headers = backendHeaders(req);
  headers.set("X-Session", session);
  headers.set("X-Requested-With", CSRF_HEADER_VALUE);
  const origin = req.headers.get("origin") ?? appOrigin(req);
  headers.set("Origin", origin);
  try {
    const res = await fetch(`${backendUrl()}/auth/logout`, {
      method: "POST",
      headers,
      cache: "no-store",
      signal: AbortSignal.timeout(LOGOUT_BACKEND_TIMEOUT_MS),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  if (!isSameOriginRequest(req)) {
    return NextResponse.json({ detail: "Cross-site request rejected." }, { status: 403 });
  }
  const session = req.cookies.get(sessionCookieName())?.value;
  const revoked = session ? await revokeOnBackend(req, session) : true;

  const response = new NextResponse(null, { status: 204 });
  response.headers.set("Cache-Control", "no-store");
  // Drops cached authenticated pages (incl. the back/forward cache) for this origin.
  response.headers.set("Clear-Site-Data", '"cache"');
  // Tells the client whether the server session was revoked (it expires on its own otherwise).
  response.headers.set("X-Session-Revoked", revoked ? "1" : "0");
  clearSessionCookie(response, cookieSecureFromEnv() || isHttps(req));
  return response;
}
