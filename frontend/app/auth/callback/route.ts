// IdP redirect target (DEPLOYMENT.md frontend contract, steps 3-5).
// 3. Reject unless `state` equals the httpOnly askdiq_oidc_state cookie, then
//    delete it. 4. POST {code, state} to the backend server-side (with the old
//    session in X-Session so it is rotated). 5. Store the new session in the
//    httpOnly session cookie and redirect by status. The session id is never
//    put in a URL or a response body.
import { NextResponse, type NextRequest } from "next/server";
import { appOrigin, appUrl, backendHeaders, backendTimeoutMs, backendUrl } from "@/lib/server/backend";
import {
  LOGIN_NEXT_COOKIE,
  OIDC_STATE_COOKIE,
  clearSessionCookie,
  clearSignInCookies,
  cookieSecureFromEnv,
  safeEqual,
  sessionCookieName,
  setSessionCookie,
} from "@/lib/server/auth-cookies";
import { loginErrorForStatus, type LoginErrorCode } from "@/lib/login-errors";
import { postLoginPath } from "@/lib/auth-gate";
import { parseMe } from "@/lib/auth-api";
import { safeNextPath } from "@/lib/safe-redirect";

export const dynamic = "force-dynamic";

const NO_STORE = "no-store";
const HTTP_FORBIDDEN = 403;
const MAX_CODE_LENGTH = 4096;
const DETAIL_SUSPENDED = "Account suspended.";
const SESSION_RE = /^[\x21-\x7e]{16,512}$/;

function redirect(req: NextRequest, path: string): NextResponse {
  const response = NextResponse.redirect(appUrl(req, path), 303);
  response.headers.set("Cache-Control", NO_STORE);
  clearSignInCookies(response);
  return response;
}

function toLogin(req: NextRequest, error: LoginErrorCode): NextResponse {
  return redirect(req, `/login?error=${error}`);
}

interface SessionIssued {
  session: string;
  cookieSecure: boolean;
  expiresAt: Date;
  status: string;
}

function parseIssued(raw: unknown): SessionIssued | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const me = parseMe(r.me);
  const expiresAt = typeof r.expires_at === "string" ? new Date(r.expires_at) : null;
  if (typeof r.session !== "string" || !SESSION_RE.test(r.session) || !me || !expiresAt) return null;
  if (Number.isNaN(expiresAt.getTime())) return null;
  return { session: r.session, cookieSecure: r.cookie_secure === true, expiresAt, status: me.status };
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const params = req.nextUrl.searchParams;
  const code = params.get("code");
  const state = params.get("state");
  const expected = req.cookies.get(OIDC_STATE_COOKIE)?.value;
  // The state check comes first: nothing else in the query is trusted until
  // it matches the cookie set when this sign-in started.
  if (!state || !expected || !safeEqual(state, expected)) {
    return toLogin(req, params.get("error") ? "idp_denied" : state ? "state_mismatch" : "expired");
  }
  // An IdP error reply carries no code, so it ends here too; the reason only
  // picks which message the login page shows.
  if (!code || code.length > MAX_CODE_LENGTH) {
    return toLogin(req, params.has("error") ? "idp_denied" : "expired");
  }

  const headers = backendHeaders(req);
  headers.set("Content-Type", "application/json");
  headers.set("X-Requested-With", "askdiq");
  headers.set("Origin", appOrigin(req));
  const previous = req.cookies.get(sessionCookieName())?.value;
  if (previous) headers.set("X-Session", previous);

  let res: Response;
  try {
    res = await fetch(`${backendUrl()}/auth/sso/callback`, {
      method: "POST",
      headers,
      body: JSON.stringify({ code, state }),
      cache: "no-store",
      signal: AbortSignal.timeout(backendTimeoutMs(true)),
    });
  } catch {
    return toLogin(req, "backend_unavailable");
  }

  if (!res.ok) {
    if (res.status === HTTP_FORBIDDEN) {
      const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
      if (body?.detail === DETAIL_SUSPENDED) {
        const response = redirect(req, "/suspended");
        clearSessionCookie(response);
        return response;
      }
    }
    return toLogin(req, loginErrorForStatus(res.status));
  }

  const issued = parseIssued(await res.json().catch(() => null));
  if (!issued) return toLogin(req, "signin_failed");

  const next = safeNextPath(req.cookies.get(LOGIN_NEXT_COOKIE)?.value);
  const response = redirect(req, postLoginPath(issued.status, next));
  setSessionCookie(response, issued.session, issued.expiresAt, issued.cookieSecure || cookieSecureFromEnv());
  return response;
}
