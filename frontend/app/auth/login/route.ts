// "Sign in with Microsoft" hand-off (docs/deployment/docker-and-auth-contract.md frontend contract, steps 1-2).
// Asks the backend for the authorize URL, binds the browser to its `state`
// with an httpOnly cookie (login-CSRF defence), remembers a validated `next`
// and redirects to the identity provider.
import { NextResponse, type NextRequest } from "next/server";
import { appUrl, backendHeaders, backendTimeoutMs, backendUrl } from "@/lib/server/backend";
import { setLoginNextCookie, setOidcStateCookie, stateFromAuthorizeUrl } from "@/lib/server/auth-cookies";
import { loginErrorForStatus, type LoginErrorCode } from "@/lib/login-errors";
import { safeNextPath } from "@/lib/safe-redirect";

export const dynamic = "force-dynamic";

const NO_STORE = "no-store";

function toLogin(req: NextRequest, error: LoginErrorCode): NextResponse {
  const url = appUrl(req, "/login");
  url.searchParams.set("error", error);
  const response = NextResponse.redirect(url, 303);
  response.headers.set("Cache-Control", NO_STORE);
  return response;
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  let res: Response;
  try {
    res = await fetch(`${backendUrl()}/auth/sso/login`, {
      headers: backendHeaders(req),
      cache: "no-store",
      signal: AbortSignal.timeout(backendTimeoutMs(false)),
    });
  } catch {
    return toLogin(req, "backend_unavailable");
  }
  if (!res.ok) return toLogin(req, loginErrorForStatus(res.status));

  let authorizeUrl: unknown;
  try {
    authorizeUrl = ((await res.json()) as { authorize_url?: unknown }).authorize_url;
  } catch {
    return toLogin(req, "idp_unavailable");
  }
  const state = typeof authorizeUrl === "string" ? stateFromAuthorizeUrl(authorizeUrl) : null;
  if (typeof authorizeUrl !== "string" || !state) return toLogin(req, "idp_unavailable");

  const response = NextResponse.redirect(authorizeUrl, 303);
  response.headers.set("Cache-Control", NO_STORE);
  setOidcStateCookie(response, state);
  const next = safeNextPath(req.nextUrl.searchParams.get("next"));
  // A stale `next` from an earlier, abandoned attempt must not survive.
  setLoginNextCookie(response, next ?? "");
  return response;
}
