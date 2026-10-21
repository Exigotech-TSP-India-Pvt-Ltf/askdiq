// Per-request CSP nonce and the signed-out redirect.
// The redirect is UX only: the API is the security boundary and
// every backend route checks the session itself.
import { NextResponse, type NextRequest } from "next/server";
import { sessionCookieName } from "@/lib/server/auth-cookies";
import { AUDIENCE_HEADER, isAudience } from "@/lib/audience-seed";

/** Pages reachable without a session cookie. */
export const PUBLIC_PATHS = ["/login", "/suspended"];

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`));
}

/** Fresh, unguessable nonce (128 bits, base64). */
export function createNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/**
 * `script-src` allows only scripts carrying this request's nonce (and what
 * they load, via 'strict-dynamic'). Development adds 'unsafe-eval' and
 * websockets for the Next dev server / HMR only.
 */
export function buildCsp(nonce: string, isDev: boolean): string {
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self'${isDev ? " ws: wss:" : ""}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  return directives.join("; ");
}

export function middleware(req: NextRequest): NextResponse {
  const { pathname, search } = req.nextUrl;

  if (!isPublicPath(pathname) && !req.cookies.get(sessionCookieName())?.value) {
    const login = req.nextUrl.clone();
    login.pathname = "/login";
    login.search = "";
    if (pathname !== "/") login.searchParams.set("next", `${pathname}${search}`);
    return NextResponse.redirect(login);
  }

  const nonce = createNonce();
  const csp = buildCsp(nonce, process.env.NODE_ENV === "development");
  // Next.js reads the nonce from the request's CSP header and stamps it on its
  // own scripts; the root layout reads `x-nonce` for the theme pre-paint script.
  const requestHeaders = new Headers(req.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  // The page's ?audience= for the server render (root layout), so the first
  // HTML matches the client's first render (VA-54). Only an allow-listed
  // value is forwarded; a client-sent header of the same name is dropped.
  requestHeaders.delete(AUDIENCE_HEADER);
  const audience = req.nextUrl.searchParams.get("audience");
  if (isAudience(audience)) requestHeaders.set(AUDIENCE_HEADER, audience);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  // Pages only: not API / auth route handlers, the dev mock IdP, public brand assets,
  // Next internals or static files. Kept as a plain string array so Turbopack can parse it.
  matcher: [
    "/((?!api/|auth/callback|auth/login|auth/logout|mock-idp/|branding/assets/|_next/static|_next/image|favicon\\.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|txt|xml|webmanifest|woff2?)$).*)",
  ],
};
