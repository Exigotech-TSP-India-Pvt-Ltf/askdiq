import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { AccessStepper, accessSteps } from "@/components/auth/AccessRequiredView";
import { SidebarAccount } from "@/components/shell/SidebarAccount";
import { SuspendedView } from "@/components/auth/SuspendedView";
import { AuthProvider } from "@/lib/auth-context";
import { queryCache } from "@/lib/query-cache";
import type { AccessRequest } from "@/lib/auth-api";
import { makeMe } from "../helpers/auth";

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

function mockReducedMotion(reduce: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: reduce && query.includes("prefers-reduced-motion"),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    })),
  );
}

const pending: AccessRequest = {
  id: "r1",
  status: "pending",
  requested_role: null,
  justification: null,
  created_at: "2026-10-15T00:00:00Z",
  decided_at: null,
  decision_note: null,
};

beforeEach(() => {
  queryCache.clear();
  mockReducedMotion(false);
});
afterEach(() => vi.unstubAllGlobals());

describe("Access Required stepper (F1.2.2)", () => {
  it("models requested -> under review -> approved, and a rejection", () => {
    const states = (steps: ReturnType<typeof accessSteps>) => steps.map((s) => s.state);
    expect(states(accessSteps(null, false))).toEqual(["current", "upcoming", "upcoming"]);
    expect(states(accessSteps(pending, false))).toEqual(["done", "current", "upcoming"]);
    expect(states(accessSteps(pending, true))).toEqual(["done", "done", "done"]);
    const rejected = accessSteps({ ...pending, status: "rejected" }, false);
    expect(states(rejected)).toEqual(["done", "failed", "upcoming"]);
    expect(rejected[1].label).toBe("Not approved");
  });

  it("animates with Motion normally and is static under reduced motion", () => {
    const { unmount } = render(
      <MotionProvider>
        <AccessStepper steps={accessSteps(pending, false)} />
      </MotionProvider>,
    );
    expect(screen.getByTestId("access-stepper")).toHaveAttribute("data-motion", "animated");
    unmount();

    mockReducedMotion(true);
    render(
      <MotionProvider>
        <AccessStepper steps={accessSteps(pending, false)} />
      </MotionProvider>,
    );
    const stepper = screen.getByTestId("access-stepper");
    expect(stepper).toHaveAttribute("data-motion", "static");
    // Progress rail is drawn at its final value immediately; dots have no entrance state.
    const styled = Array.from(stepper.querySelectorAll<HTMLElement>("[style]"));
    expect(styled.map((el) => el.style.transform)).toEqual(["scaleX(0.5)"]);
    expect(within(stepper).getByText("Under review").closest("li")).toHaveAttribute("aria-current", "step");
  });
});

describe("Account corner: role badge and notification bell (F1.3)", () => {
  it("superadmin: role badge beside the name and the pending access-request notification", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ items: [], total: 4, limit: 1, offset: 0 }), { status: 200 })),
    );
    render(
      <AuthProvider initialMe={makeMe("superadmin")}>
        <MotionProvider>
          <SidebarAccount collapsed={false} />
        </MotionProvider>
      </AuthProvider>,
    );
    const bell = await screen.findByRole("button", { name: "Notifications (1 new)" });
    fireEvent.click(bell);
    const panel = await screen.findByRole("dialog", { name: "Notifications" });
    const link = within(panel).getByRole("link", { name: /4 access requests waiting/i });
    expect(link).toHaveAttribute("href", "/admin/user-management?tab=requests");

    expect(screen.getByTestId("role-badge")).toHaveTextContent("Superadmin");
    expect(screen.getByTestId("account-email")).toHaveTextContent("jane@contoso.example");
  });

  it("admin: no access-request fetch, empty bell, Admin badge", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    render(
      <AuthProvider initialMe={makeMe("admin")}>
        <MotionProvider>
          <SidebarAccount collapsed={false} />
        </MotionProvider>
      </AuthProvider>,
    );
    expect(screen.getByRole("button", { name: "Notifications" })).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId("role-badge")).toHaveTextContent("Admin");
  });
});

describe("Suspended page (F1.2.2)", () => {
  it("shows the contact email and a sign-out action when signed in", () => {
    const onSignOut = vi.fn();
    render(<SuspendedView contactEmail="it@contoso.example" onSignOut={onSignOut} />);
    expect(screen.getByRole("heading", { name: "Your account is suspended" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "it@contoso.example" })).toHaveAttribute("href", "mailto:it@contoso.example");
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(onSignOut).toHaveBeenCalled();
  });

  it("without a session offers the way back to sign in and a generic contact", () => {
    render(<SuspendedView contactEmail={null} />);
    expect(screen.getByText(/your Exigo Tech administrator/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to sign in" })).toHaveAttribute("href", "/login");
  });
});
