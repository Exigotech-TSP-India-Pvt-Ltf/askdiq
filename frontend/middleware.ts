// Per-request CSP nonce (planchanges4 F0.1.2) and the signed-out redirect
// (F1.1.3). The redirect is UX only: the API is the security boundary and
// every backend route checks the session itself.
import { NextResponse, type NextRequest } from "next/server";
import { sessionCookieName } from "@/lib/server/auth-cookies";

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
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      // Pages only: not API / auth route handlers, the dev mock IdP, public
      // brand assets (logos on /login, PR-26), Next internals or static files.
      source: "/((?!api/|auth/callback|auth/login|mock-idp/|branding/assets/|_next/static|_next/image|favicon\\.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|txt|xml|webmanifest|woff2?)$).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
