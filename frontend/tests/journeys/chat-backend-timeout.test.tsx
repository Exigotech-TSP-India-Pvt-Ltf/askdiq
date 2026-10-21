/**
 * /chat with a slow or hung backend: the proxy's 504 (BACKEND_TIMEOUT_MS) is
 * shown as an error state, and the shell never re-navigates, refreshes the
 * route or re-polls `/auth/me` in a loop (the dev-server log showed
 * `GET /chat?_rsc=...` about every second).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { ToastProvider } from "@/components/ui/ToastProvider";
import { AuthProvider } from "@/lib/auth-context";
import { queryCache } from "@/lib/query-cache";
import AppLayout from "@/app/(app)/layout";
import ChatPage from "@/app/(app)/chat/page";
import { makeMe } from "../helpers/auth";

const nav = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/chat",
  useRouter: () => nav,
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/navigation", () => ({ redirectTo: vi.fn() }));

const QUIET_PERIOD_MS = 1500;

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let fetchMock: ReturnType<typeof vi.fn>;

function backend(meStatus: number) {
  fetchMock = vi.fn(async (url: string) => {
    const path = String(url).replace("/api/proxy", "");
    if (path === "/auth/me") {
      return meStatus === 200
        ? json(200, makeMe("superadmin"))
        : json(meStatus, { detail: "The backend did not respond within 30 seconds. Please try again." });
    }
    if (path.startsWith("/sessions")) return json(200, []);
    return json(200, {});
  });
  vi.stubGlobal("fetch", fetchMock);
}

const calls = (path: string) => fetchMock.mock.calls.filter(([url]) => String(url) === `/api/proxy${path}`).length;

function renderChat() {
  return render(
    <MotionProvider>
      <ToastProvider>
        <AuthProvider>
          <AppLayout>
            <ChatPage />
          </AppLayout>
        </AuthProvider>
      </ToastProvider>
    </MotionProvider>,
  );
}

async function quietPeriod() {
  await new Promise((resolve) => setTimeout(resolve, QUIET_PERIOD_MS));
}

beforeEach(() => {
  queryCache.clear();
  Object.values(nav).forEach((fn) => fn.mockClear());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("/chat when the backend times out", { timeout: 15_000 }, () => {
  it("shows the session error state from the proxy's 504 and does not retry or navigate on its own", async () => {
    backend(504);
    renderChat();
    expect(await screen.findByText("We couldn't check your session")).toBeInTheDocument();
    await quietPeriod();
    expect(calls("/auth/me")).toBe(1);
    expect(nav.replace).not.toHaveBeenCalled();
    expect(nav.push).not.toHaveBeenCalled();
    expect(nav.refresh).not.toHaveBeenCalled();
  });
});

describe("/chat when signed in", { timeout: 15_000 }, () => {
  it("renders once and settles: no route refresh, navigation or /auth/me polling loop", async () => {
    backend(200);
    renderChat();
    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument());
    await quietPeriod();
    expect(calls("/auth/me")).toBe(1);
    expect(nav.replace).not.toHaveBeenCalled();
    expect(nav.push).not.toHaveBeenCalled();
    expect(nav.refresh).not.toHaveBeenCalled();
  });
});
