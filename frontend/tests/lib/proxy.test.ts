// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { DELETE, GET, POST } from "@/app/api/proxy/[...path]/route";

const BACKEND = "http://backend.test:8000";

function request(path: string, init: { method?: string; cookie?: string; origin?: string; body?: string } = {}) {
  const headers: Record<string, string> = {};
  if (init.cookie) headers.cookie = init.cookie;
  if (init.origin) headers.origin = init.origin;
  return new NextRequest(`http://localhost:3000/api/proxy/${path}`, {
    method: init.method ?? "GET",
    headers,
    body: init.body,
  });
}

const params = (path: string) => ({ params: { path: path.split("?")[0].split("/") } });

let fetchMock: ReturnType<typeof vi.fn>;

function backendReturns(status: number, body: unknown = {}, headers: Record<string, string> = {}) {
  fetchMock.mockResolvedValue(
    new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json", ...headers },
    }),
  );
}

function sentHeaders(): Headers {
  return fetchMock.mock.calls[0][1].headers as Headers;
}

beforeEach(() => {
  vi.stubEnv("API_BASE_URL", BACKEND);
  vi.stubEnv("API_KEY", "service-key");
  vi.stubEnv("SESSION_COOKIE_NAME", "askdiq_session");
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("proxy forwards the session (F1.1.2)", () => {
  it("sends X-Session from the httpOnly cookie and the service key on reads, with no CSRF headers", async () => {
    backendReturns(200, { ok: true });
    await GET(request("auth/me", { cookie: "askdiq_session=sess-abc-123" }), params("auth/me"));
    expect(fetchMock.mock.calls[0][0]).toBe(`${BACKEND}/auth/me`);
    const headers = sentHeaders();
    expect(headers.get("X-Session")).toBe("sess-abc-123");
    expect(headers.get("X-API-Key")).toBe("service-key");
    expect(headers.get("X-AskDIQ-Proxy")).toBe("1");
    expect(headers.get("X-Requested-With")).toBeNull();
  });

  it("adds X-Requested-With: askdiq and forwards the browser Origin on state changes", async () => {
    backendReturns(201, { id: "r1" });
    await POST(
      request("auth/access-request", {
        method: "POST",
        cookie: "askdiq_session=sess-abc-123",
        origin: "http://localhost:3000",
        body: "{}",
      }),
      params("auth/access-request"),
    );
    const headers = sentHeaders();
    expect(headers.get("X-Requested-With")).toBe("askdiq");
    expect(headers.get("Origin")).toBe("http://localhost:3000");
    expect(headers.get("X-Session")).toBe("sess-abc-123");
  });

  it("never synthesises an Origin the browser did not send", async () => {
    backendReturns(204);
    await DELETE(request("sessions/s1", { method: "DELETE", cookie: "askdiq_session=s" }), params("sessions/s1"));
    expect(sentHeaders().get("Origin")).toBeNull();
  });

  it("does not leak backend Set-Cookie headers or the cookie to the client", async () => {
    backendReturns(200, { ok: true }, { "set-cookie": "tracking=1" });
    const res = await GET(request("health", { cookie: "askdiq_session=s" }), params("health"));
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(await res.text()).not.toContain("askdiq_session");
  });

  it("clears the session cookie when the backend answers 401", async () => {
    backendReturns(401, { detail: "Not authenticated." });
    const res = await GET(request("auth/me", { cookie: "askdiq_session=expired" }), params("auth/me"));
    expect(res.status).toBe(401);
    const cookie = res.headers.get("set-cookie") ?? "";
    expect(cookie).toMatch(/askdiq_session=;/);
    expect(cookie).toMatch(/Max-Age=0/i);
    expect(cookie).toMatch(/HttpOnly/i);
  });

  it("clears the session cookie after a successful sign-out (204)", async () => {
    backendReturns(204);
    const res = await POST(
      request("auth/logout", { method: "POST", cookie: "askdiq_session=s", origin: "http://localhost:3000" }),
      params("auth/logout"),
    );
    expect(res.status).toBe(204);
    expect(res.headers.get("set-cookie")).toMatch(/askdiq_session=;.*Max-Age=0/i);
  });

  it("refuses to proxy /auth/sso/callback (its body carries the raw session id)", async () => {
    const res = await POST(request("auth/sso/callback", { method: "POST", body: "{}" }), params("auth/sso/callback"));
    expect(res.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("binds the OIDC state cookie when sign-in starts through the proxy", async () => {
    const state = "s".repeat(32);
    backendReturns(200, { authorize_url: `https://login.example/authorize?client_id=c&state=${state}` });
    const res = await GET(request("auth/sso/login"), params("auth/sso/login"));
    expect(res.status).toBe(200);
    const cookie = res.headers.get("set-cookie") ?? "";
    expect(cookie).toContain(`askdiq_oidc_state=${state}`);
    expect(cookie).toMatch(/HttpOnly/i);
    expect(cookie).toMatch(/SameSite=lax/i);
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("passes ETag / Cache-Control through and forwards If-None-Match (weak ETag on /auth/me)", async () => {
    backendReturns(200, {}, { etag: 'W/"v1"', "cache-control": "private, max-age=0" });
    const req = new NextRequest("http://localhost:3000/api/proxy/auth/me", { headers: { "if-none-match": 'W/"v0"' } });
    const res = await GET(req, params("auth/me"));
    expect(sentHeaders().get("If-None-Match")).toBe('W/"v0"');
    expect(res.headers.get("etag")).toBe('W/"v1"');
    expect(res.headers.get("cache-control")).toBe("private, max-age=0");
  });
});

describe("proxy path hygiene (existing behaviour kept)", () => {
  it.each([["..", "admin"], ["a\\b"], ["a/b"]])("rejects traversal segments %j", async (...segments) => {
    const res = await GET(request("x"), { params: { path: segments } });
    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
