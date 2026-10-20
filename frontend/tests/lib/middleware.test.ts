// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { buildCsp, config, isPublicPath, middleware } from "@/middleware";

const req = (path: string, cookie?: string) =>
  new NextRequest(`http://localhost:3000${path}`, { headers: cookie ? { cookie } : {} });

afterEach(() => vi.unstubAllEnvs());

describe("middleware: signed-out redirect (F1.1.3, UX only)", () => {
  it("redirects a page request with no session cookie to /login?next=", () => {
    const res = middleware(req("/admin/feedback?tab=queue"));
    expect(res.status).toBe(307);
    const location = new URL(res.headers.get("location") ?? "");
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("next")).toBe("/admin/feedback?tab=queue");
  });

  it("redirects / without a next", () => {
    const location = new URL(middleware(req("/")).headers.get("location") ?? "");
    expect(location.pathname).toBe("/login");
    expect(location.search).toBe("");
  });

  it("lets /login and /suspended through without a cookie", () => {
    for (const path of ["/login", "/login?error=expired", "/suspended"]) {
      const res = middleware(req(path));
      expect(res.headers.get("location")).toBeNull();
      expect(res.headers.get("content-security-policy")).toContain("nonce-");
    }
    expect(isPublicPath("/loginx")).toBe(false);
  });

  it("lets a request with a session cookie through (the API still checks it)", () => {
    const res = middleware(req("/admin", "askdiq_session=abc"));
    expect(res.headers.get("location")).toBeNull();
  });

  it("honours SESSION_COOKIE_NAME", () => {
    vi.stubEnv("SESSION_COOKIE_NAME", "custom_session");
    expect(middleware(req("/admin", "askdiq_session=abc")).headers.get("location")).toContain("/login");
    expect(middleware(req("/admin", "custom_session=abc")).headers.get("location")).toBeNull();
  });
});

describe("middleware: nonce CSP", () => {
  const nonceOf = (csp: string | null) => /'nonce-([^']+)'/.exec(csp ?? "")?.[1];

  it("issues a fresh nonce per request and passes it to the app via x-nonce", () => {
    const a = middleware(req("/login"));
    const b = middleware(req("/login"));
    const na = nonceOf(a.headers.get("content-security-policy"));
    const nb = nonceOf(b.headers.get("content-security-policy"));
    expect(na).toMatch(/^[A-Za-z0-9+/]{22}==$/);
    expect(na).not.toBe(nb);
    // NextResponse.next({ request: { headers } }) exposes overridden request headers.
    expect(a.headers.get("x-middleware-request-x-nonce")).toBe(na);
    expect(a.headers.get("x-middleware-request-content-security-policy")).toContain(`'nonce-${na}'`);
  });

  it("builds the planned directives (no unsafe-eval outside development)", () => {
    const csp = buildCsp("abc", false);
    expect(csp).toContain("script-src 'self' 'nonce-abc' 'strict-dynamic'");
    expect(csp).toContain("img-src 'self' data: blob:");
    expect(csp).toContain("connect-src 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).not.toContain("unsafe-eval");
    expect(buildCsp("abc", true)).toContain("'unsafe-eval'");
  });

  it("excludes API, auth handlers, the dev mock IdP, Next internals and static files", () => {
    const source = new RegExp(`^${config.matcher[0]}$`);
    for (const path of ["/admin", "/login", "/chat", "/documents/d1", "/suspended"]) expect(source.test(path)).toBe(true);
    for (const path of [
      "/api/proxy/auth/me",
      "/auth/callback",
      "/auth/login",
      "/auth/logout",
      "/mock-idp/t/oauth2/v2.0/authorize",
      "/branding/assets/0123abcd",
      "/_next/static/chunks/main.js",
      "/_next/image",
      "/favicon.ico",
      "/exigotech-logo.svg",
    ]) {
      expect(source.test(path)).toBe(false);
    }
  });
});
