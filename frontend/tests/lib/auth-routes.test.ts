// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET as callback } from "@/app/auth/callback/route";
import { GET as startLogin } from "@/app/auth/login/route";
import { LOGOUT_BACKEND_TIMEOUT_MS, POST as logout } from "@/app/auth/logout/route";
import { postLoginPath } from "@/lib/auth-gate";

const BACKEND = "http://backend.test:8000";
const STATE = "state_0123456789abcdef";
const SESSION = "sess_0123456789abcdefghijklmnop";

let fetchMock: ReturnType<typeof vi.fn>;

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function issued(status: string, cookieSecure = false) {
  return json(200, {
    session: SESSION,
    cookie_name: "askdiq_session",
    cookie_secure: cookieSecure,
    expires_at: "2030-01-01T08:00:00Z",
    me: {
      user: { id: "u-1", email: "jane@contoso.example", name: "Jane" },
      role: status === "active" ? "admin" : null,
      status,
      permissions: [],
      version: 1,
      access_contact_email: null,
    },
  });
}

function callbackRequest(query: string, cookies: Record<string, string> = { askdiq_oidc_state: STATE }) {
  const cookie = Object.entries(cookies)
    .map(([k, v]) => `${k}=${v}`)
    .join("; ");
  return new NextRequest(`http://localhost:3000/auth/callback?${query}`, { headers: cookie ? { cookie } : {} });
}

const location = (res: Response) => new URL(res.headers.get("location") ?? "");
const setCookies = (res: Response) => res.headers.getSetCookie();
const cookieNamed = (res: Response, name: string) => setCookies(res).find((c) => c.startsWith(`${name}=`)) ?? "";

beforeEach(() => {
  vi.stubEnv("API_BASE_URL", BACKEND);
  vi.stubEnv("API_KEY", "service-key");
  vi.stubEnv("APP_BASE_URL", "http://localhost:3000");
  vi.stubEnv("SESSION_COOKIE_SECURE", "false");
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("/auth/callback (F1.1.1, contract steps 3-5)", () => {
  it("rejects a state that does not match the askdiq_oidc_state cookie (login CSRF)", async () => {
    const res = await callback(callbackRequest(`code=c1&state=${"x".repeat(22)}`));
    expect(location(res).pathname).toBe("/login");
    expect(location(res).searchParams.get("error")).toBe("state_mismatch");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookieNamed(res, "askdiq_oidc_state")).toMatch(/Max-Age=0/i);
  });

  it("rejects a callback with no state cookie at all", async () => {
    const res = await callback(callbackRequest(`code=c1&state=${STATE}`, {}));
    expect(location(res).searchParams.get("error")).toBe("state_mismatch");
  });

  it("posts code and state to the backend with the service key, CSRF marker and rotation header", async () => {
    fetchMock.mockResolvedValue(issued("active"));
    await callback(callbackRequest(`code=c1&state=${STATE}`, { askdiq_oidc_state: STATE, askdiq_session: "old-session" }));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BACKEND}/auth/sso/callback`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ code: "c1", state: STATE });
    const headers = init.headers as Headers;
    expect(headers.get("X-API-Key")).toBe("service-key");
    expect(headers.get("X-Session")).toBe("old-session");
    expect(headers.get("X-Requested-With")).toBe("askdiq");
    expect(headers.get("Origin")).toBe("http://localhost:3000");
  });

  it("sets the session cookie HttpOnly, SameSite=Lax, Path=/, with the expiry, and never in the URL", async () => {
    fetchMock.mockResolvedValue(issued("active"));
    const res = await callback(callbackRequest(`code=c1&state=${STATE}`));
    const cookie = cookieNamed(res, "askdiq_session");
    expect(cookie).toContain(`askdiq_session=${SESSION}`);
    expect(cookie).toMatch(/HttpOnly/i);
    expect(cookie).toMatch(/SameSite=lax/i);
    expect(cookie).toMatch(/Path=\//);
    expect(cookie).toMatch(/Expires=Tue, 01 Jan 2030 08:00:00 GMT/);
    expect(cookie).not.toMatch(/Secure/);
    expect(res.headers.get("location")).not.toContain(SESSION);
    expect(res.headers.get("cache-control")).toBe("no-store");
    // The sign-in cookies are single use.
    expect(cookieNamed(res, "askdiq_oidc_state")).toMatch(/Max-Age=0/i);
  });

  it("marks the cookie Secure when the backend says cookie_secure", async () => {
    fetchMock.mockResolvedValue(issued("active", true));
    const res = await callback(callbackRequest(`code=c1&state=${STATE}`));
    expect(cookieNamed(res, "askdiq_session")).toMatch(/Secure/);
  });

  it("redirects an active user to a safe next, and ignores an external next", async () => {
    fetchMock.mockResolvedValue(issued("active"));
    const ok = await callback(
      callbackRequest(`code=c1&state=${STATE}`, { askdiq_oidc_state: STATE, askdiq_login_next: "/admin/feedback" }),
    );
    expect(location(ok).pathname).toBe("/admin/feedback");

    fetchMock.mockResolvedValue(issued("active"));
    const evil = await callback(
      callbackRequest(`code=c1&state=${STATE}`, {
        askdiq_oidc_state: STATE,
        askdiq_login_next: encodeURIComponent("https://evil.example/x"),
      }),
    );
    expect(location(evil).origin).toBe("http://localhost:3000");
    expect(location(evil).pathname).toBe("/");
  });

  it("sends pending and rejected users to Chat", async () => {
    for (const status of ["pending", "rejected"]) {
      fetchMock.mockResolvedValue(issued(status));
      const res = await callback(
        callbackRequest(`code=c1&state=${STATE}`, { askdiq_oidc_state: STATE, askdiq_login_next: "/admin" }),
      );
      expect(location(res).pathname).toBe("/chat");
      expect(cookieNamed(res, "askdiq_session")).toContain(SESSION);
    }
  });

  it("sends a suspended account to the suspended page without a session", async () => {
    fetchMock.mockResolvedValue(json(403, { detail: "Account suspended.", access_contact_email: "it@contoso.example" }));
    const res = await callback(callbackRequest(`code=c1&state=${STATE}`));
    expect(location(res).pathname).toBe("/suspended");
    expect(location(res).search).toBe("");
    expect(cookieNamed(res, "askdiq_session")).toMatch(/Max-Age=0/i);
  });

  it.each([
    [400, "expired"],
    [401, "signin_failed"],
    [403, "identity_conflict"],
    [429, "rate_limited"],
    [502, "idp_unavailable"],
  ])("maps backend %i to the allow-listed error %s", async (status, code) => {
    fetchMock.mockResolvedValue(json(status, { detail: "<script>alert(1)</script>" }));
    const res = await callback(callbackRequest(`code=c1&state=${STATE}`));
    expect(location(res).pathname).toBe("/login");
    expect(location(res).searchParams.get("error")).toBe(code);
    expect(res.headers.get("location")).not.toContain("script");
  });

  it("maps an IdP error redirect and an unreachable backend", async () => {
    const denied = await callback(callbackRequest("error=access_denied&error_description=%3Cb%3E"));
    expect(location(denied).searchParams.get("error")).toBe("idp_denied");

    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    const down = await callback(callbackRequest(`code=c1&state=${STATE}`));
    expect(location(down).searchParams.get("error")).toBe("backend_unavailable");
  });

  it("rejects a malformed success body instead of setting a cookie", async () => {
    fetchMock.mockResolvedValue(json(200, { session: "x", me: null }));
    const res = await callback(callbackRequest(`code=c1&state=${STATE}`));
    expect(location(res).searchParams.get("error")).toBe("signin_failed");
    expect(cookieNamed(res, "askdiq_session")).toBe("");
  });
});

describe("/auth/login (contract steps 1-2)", () => {
  const authorize = `http://localhost:3000/mock-idp/t/oauth2/v2.0/authorize?client_id=c&state=${STATE}&nonce=n`;

  it("redirects to the authorize URL and binds the state and a validated next in httpOnly cookies", async () => {
    fetchMock.mockResolvedValue(json(200, { authorize_url: authorize }));
    const res = await startLogin(new NextRequest("http://localhost:3000/auth/login?next=%2Fadmin%2Fusage"));
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe(authorize);
    const state = cookieNamed(res, "askdiq_oidc_state");
    expect(state).toContain(`askdiq_oidc_state=${STATE}`);
    expect(state).toMatch(/HttpOnly/i);
    expect(state).toMatch(/Max-Age=600/);
    expect(decodeURIComponent(cookieNamed(res, "askdiq_login_next"))).toContain("askdiq_login_next=/admin/usage");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BACKEND}/auth/sso/login`);
    expect((init.headers as Headers).get("X-API-Key")).toBe("service-key");
  });

  it("drops an external next", async () => {
    fetchMock.mockResolvedValue(json(200, { authorize_url: authorize }));
    const res = await startLogin(new NextRequest("http://localhost:3000/auth/login?next=https%3A%2F%2Fevil.example"));
    expect(cookieNamed(res, "askdiq_login_next")).toMatch(/^askdiq_login_next=;/);
  });

  it("maps 429 to rate_limited and a missing state to idp_unavailable", async () => {
    fetchMock.mockResolvedValue(json(429, { detail: "slow down" }));
    const limited = await startLogin(new NextRequest("http://localhost:3000/auth/login"));
    expect(location(limited).searchParams.get("error")).toBe("rate_limited");

    fetchMock.mockResolvedValue(json(200, { authorize_url: "https://login.example/authorize?client_id=c" }));
    const bad = await startLogin(new NextRequest("http://localhost:3000/auth/login"));
    expect(location(bad).searchParams.get("error")).toBe("idp_unavailable");
    expect(cookieNamed(bad, "askdiq_oidc_state")).toBe("");
  });
});

describe("postLoginPath", () => {
  it("routes by status", () => {
    expect(postLoginPath("active", null)).toBe("/");
    expect(postLoginPath("active", "/admin")).toBe("/admin");
    expect(postLoginPath("pending", "/admin")).toBe("/chat");
    expect(postLoginPath("pending", "/chat?session=s1")).toBe("/chat?session=s1");
  });
});

describe("/auth/logout (sign out never depends on the backend)", () => {
  function logoutRequest(headers: Record<string, string> = {}, url = "http://localhost:3000/auth/logout") {
    return new NextRequest(url, {
      method: "POST",
      headers: {
        cookie: `askdiq_session=${SESSION}`,
        origin: "http://localhost:3000",
        "x-requested-with": "askdiq",
        ...headers,
      },
    });
  }

  it("revokes the backend session with the service key, CSRF marker and Origin, then clears the cookie", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const res = await logout(logoutRequest());
    expect(res.status).toBe(204);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BACKEND}/auth/logout`);
    expect(init.method).toBe("POST");
    const sent = new Headers(init.headers);
    expect(sent.get("X-Session")).toBe(SESSION);
    expect(sent.get("X-API-Key")).toBe("service-key");
    expect(sent.get("X-Requested-With")).toBe("askdiq");
    expect(sent.get("Origin")).toBe("http://localhost:3000");
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(cookieNamed(res, "askdiq_session")).toMatch(/Max-Age=0/i);
    expect(cookieNamed(res, "askdiq_session")).toMatch(/HttpOnly/i);
    expect(cookieNamed(res, "askdiq_session")).toMatch(/Path=\//i);
    expect(res.headers.get("x-session-revoked")).toBe("1");
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(res.headers.get("clear-site-data")).toBe('"cache"');
  });

  it.each([
    ["unreachable", () => fetchMock.mockRejectedValue(new TypeError("fetch failed"))],
    ["erroring", () => fetchMock.mockResolvedValue(new Response("{}", { status: 500 }))],
    ["timing out", () => fetchMock.mockRejectedValue(new DOMException("timeout", "TimeoutError"))],
  ])("still clears the cookie when the backend is %s", async (_label, arrange) => {
    arrange();
    const res = await logout(logoutRequest());
    expect(res.status).toBe(204);
    expect(cookieNamed(res, "askdiq_session")).toMatch(/Max-Age=0/i);
    expect(res.headers.get("x-session-revoked")).toBe("0");
  });

  it("bounds the backend call", () => {
    expect(LOGOUT_BACKEND_TIMEOUT_MS).toBeGreaterThan(0);
    expect(LOGOUT_BACKEND_TIMEOUT_MS).toBeLessThanOrEqual(5000);
  });

  it("clears without calling the backend when there is no session cookie", async () => {
    const res = await logout(logoutRequest({ cookie: "" }));
    expect(res.status).toBe(204);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["no CSRF marker", { "x-requested-with": "" }],
    ["a foreign Origin", { origin: "https://evil.example" }],
  ])("rejects a request with %s and keeps the cookie (forced sign-out CSRF)", async (_label, headers) => {
    const res = await logout(logoutRequest(headers));
    expect(res.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(cookieNamed(res, "askdiq_session")).toBe("");
  });

  it("clears a Secure cookie with Secure behind an HTTPS reverse proxy", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const res = await logout(logoutRequest({ "x-forwarded-proto": "https" }));
    expect(cookieNamed(res, "askdiq_session")).toMatch(/Secure/i);
  });
});
