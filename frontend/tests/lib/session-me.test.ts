// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { ME_PRELOAD_TIMEOUT_MS, preloadMe } from "@/lib/server/session-me";

/** Server-side `/auth/me` preload for the root layout (perf: shell in the first HTML). */

const ME = {
  user: { id: "u-1", email: "admin@example.com", name: "Admin" },
  role: "superadmin",
  status: "active",
  permissions: ["dashboard:read"],
  version: 3,
  access_contact_email: null,
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("preloadMe", () => {
  it("returns the parsed user and calls the backend with the session, service key and no caching", async () => {
    vi.stubEnv("API_BASE_URL", "http://backend:8000/");
    vi.stubEnv("API_KEY", "svc-key");
    const fetchMock = vi.fn().mockResolvedValue(json(ME));

    const me = await preloadMe(
      { session: "raw-session", forwardedFor: "203.0.113.9", userAgent: "UA/1" },
      fetchMock as unknown as typeof fetch,
    );

    expect(me?.user.email).toBe("admin@example.com");
    expect(me?.role).toBe("superadmin");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://backend:8000/auth/me");
    expect(init.cache).toBe("no-store");
    expect(init.signal).toBeInstanceOf(AbortSignal);
    const headers = init.headers as Headers;
    expect(headers.get("X-Session")).toBe("raw-session");
    expect(headers.get("X-API-Key")).toBe("svc-key");
    expect(headers.get("X-Forwarded-For")).toBe("203.0.113.9");
    expect(headers.get("User-Agent")).toBe("UA/1");
  });

  it("does not call the backend without a session cookie", async () => {
    const fetchMock = vi.fn();
    expect(await preloadMe({ session: undefined }, fetchMock as unknown as typeof fetch)).toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["a rejected session (401, the proxy then clears the cookie)", () => json({ detail: "no" }, 401)],
    ["a backend error", () => json({ detail: "boom" }, 503)],
    ["an unexpected payload", () => json({ user: null })],
  ])("falls back to the client fetch on %s", async (_label, response) => {
    const fetchMock = vi.fn().mockResolvedValue(response());
    expect(await preloadMe({ session: "s" }, fetchMock as unknown as typeof fetch)).toBeUndefined();
  });

  it("falls back when the backend is unreachable or slower than the bound", async () => {
    const failing = vi.fn().mockRejectedValue(new DOMException("timed out", "TimeoutError"));
    expect(await preloadMe({ session: "s" }, failing as unknown as typeof fetch)).toBeUndefined();
    expect(ME_PRELOAD_TIMEOUT_MS).toBeLessThanOrEqual(2000);
  });
});
