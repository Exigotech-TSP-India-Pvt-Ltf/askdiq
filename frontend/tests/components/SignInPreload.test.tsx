import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import type { AuthMe } from "@/lib/auth-api";

/**
 * Preloading around sign-in: the login screen warms post-login code only
 * (never data); once /auth/me resolves, the landing page and the first
 * permitted sidebar pages are warmed at once.
 */

const auth = vi.hoisted(() => ({ value: null as unknown }));
const preloadConsole = vi.hoisted(() => vi.fn(() => () => undefined));
const prefetchShellData = vi.hoisted(() => vi.fn());
const chunk = vi.hoisted(() => vi.fn(() => Promise.resolve({})));

vi.mock("next/navigation", () => ({ usePathname: () => "/admin", useRouter: () => ({ replace: vi.fn() }) }));
vi.mock("@/lib/auth-context", () => ({ useAuth: () => auth.value }));
vi.mock("@/lib/audience-context", () => ({ useAudience: () => ({ audience: "external" }) }));
vi.mock("@/lib/live-updates", () => ({ useConsoleLive: vi.fn() }));
vi.mock("@/lib/route-prefetch", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/route-prefetch")>()),
  preloadConsole,
  prefetchShellData,
}));
vi.mock("@/lib/post-login-chunks", () => ({ POST_LOGIN_CHUNKS: [chunk, chunk] }));

const { ConsoleRuntime } = await import("@/components/shell/ConsoleRuntime");
const { priorityHrefs } = await import("@/lib/route-prefetch");
const { codePreloadDisabled } = await import("@/lib/login-preload");
const { MotionProvider } = await import("@/components/providers/MotionProvider");
const { BrandProvider } = await import("@/lib/brand-context");
const { DEFAULT_BRAND } = await import("@/lib/brand");
const { LoginView } = await import("@/components/auth/LoginView");

function me(role: AuthMe["role"], permissions: AuthMe["permissions"]): AuthMe {
  return {
    user: { id: "u1", email: "a@x.test", name: "A" },
    role,
    status: "active",
    permissions,
    version: 1,
    access_contact_email: null,
  };
}

function signedIn(user: AuthMe) {
  auth.value = { me: user, token: "session", permissions: user.permissions, refresh: vi.fn(async () => user) };
}

function setConnection(connection: { saveData?: boolean; effectiveType?: string } | undefined) {
  Object.defineProperty(window.navigator, "connection", { value: connection, configurable: true });
}

beforeEach(() => {
  vi.useFakeTimers();
  preloadConsole.mockClear();
  prefetchShellData.mockClear();
  chunk.mockClear();
  window.sessionStorage.clear();
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
    })),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  setConnection(undefined);
});

describe("warm right after sign-in", () => {
  it("starts the priority preload on auth resolution, before the idle delay", () => {
    signedIn(me("admin", ["dashboard:read", "documents:write"]));
    render(<ConsoleRuntime />);
    // No timers advanced: started in the same commit as the shell.
    expect(prefetchShellData).toHaveBeenCalledWith("session");
    expect(preloadConsole).toHaveBeenCalledTimes(1);
    const [hrefs, token, audience, step] = preloadConsole.mock.calls[0] as unknown as [string[], string, string, number];
    expect(hrefs[0]).toBe("/admin");
    expect(hrefs).toEqual(priorityHrefs("/admin", hrefs));
    expect(hrefs.length).toBeGreaterThan(1);
    expect([token, audience, step]).toEqual(["session", "external", 0]);
  });

  it("warms only pages the user is permitted to open", () => {
    signedIn(me(null, []));
    render(<ConsoleRuntime />);
    const [hrefs] = preloadConsole.mock.calls[0] as unknown as [string[]];
    expect(hrefs).toEqual([]);

    // A landing page the user may not open is not warmed; neither is Chat (no data to warm).
    expect(priorityHrefs("/admin/user-management", ["/admin", "/chat", "/documents"])).toEqual([
      "/admin",
      "/documents",
    ]);
  });

  it("never warms User Management or Settings for an operator", () => {
    signedIn(me("operator", ["dashboard:read"]));
    render(<ConsoleRuntime />);
    act(() => vi.advanceTimersByTime(10_000));
    for (const call of preloadConsole.mock.calls as unknown as Array<[string[]]>) {
      expect(call[0]).not.toContain("/admin/user-management");
      expect(call[0]).not.toContain("/admin/settings");
    }
  });
});

describe("login screen code preload", () => {
  function renderLogin() {
    return render(
      <BrandProvider brand={DEFAULT_BRAND}>
        <MotionProvider>
          <LoginView errorCode={null} next={null} contactEmail={null} year={2026} />
        </MotionProvider>
      </BrandProvider>,
    );
  }

  it("loads the post-login code once idle and makes no /api data request", () => {
    const fetchSpy = vi.fn(() => Promise.resolve(new Response("{}")));
    vi.stubGlobal("fetch", fetchSpy);
    renderLogin();
    expect(chunk).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(100));
    expect(chunk).toHaveBeenCalledTimes(2);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(preloadConsole).not.toHaveBeenCalled();
    expect(prefetchShellData).not.toHaveBeenCalled();
  });

  it.each([
    [{ saveData: true }],
    [{ effectiveType: "2g" }],
    [{ effectiveType: "slow-2g" }],
  ])("is skipped on reduced data / slow connections (%o)", (connection) => {
    setConnection(connection);
    expect(codePreloadDisabled()).toBe(true);
    renderLogin();
    act(() => vi.advanceTimersByTime(100));
    expect(chunk).not.toHaveBeenCalled();
  });

  it("runs on a normal connection", () => {
    setConnection({ effectiveType: "4g", saveData: false });
    expect(codePreloadDisabled()).toBe(false);
  });
});
