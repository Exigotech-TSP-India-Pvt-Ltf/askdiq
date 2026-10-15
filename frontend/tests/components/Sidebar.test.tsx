import React from "react";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NAV_ITEMS, Sidebar, isNavItemEnabled } from "@/components/Sidebar";
import { MobileTopBar } from "@/components/shell/MobileTopBar";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { MobileNavProvider } from "@/lib/mobile-nav-context";
import { queryCache } from "@/lib/query-cache";
import type { Role } from "@/lib/permissions";
import { authValue, makeMe } from "../helpers/auth";

const auth = vi.hoisted(() => ({ current: null as unknown, logout: vi.fn() }));
vi.mock("@/lib/auth-context", () => ({ useAuth: () => auth.current }));

function signInAs(role: Role | null, status?: "pending" | "rejected" | "active") {
  auth.current = authValue(makeMe(role, status), { logout: auth.logout });
}

beforeEach(() => {
  // Admin by default; the role matrix below switches roles per case.
  signInAs("admin");
  queryCache.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin/usage",
}));

describe("Sidebar Navigation Component (Task 4.3)", () => {
  it("renders all 9 navigation links with text-[13px] typography", () => {
    render(<Sidebar />);

    const expectedLabels = [
      "Dashboard",
      "Chat",
      "Documents",
      "Visitors",
      "Usage & Cost",
      "Conversation Logs",
      "Feedback",
      "Evaluation (Live)",
      "Admin Governance",
    ];

    for (const label of expectedLabels) {
      const link = screen.getByText(label);
      expect(link).toBeInTheDocument();
      const anchor = link.closest("a");
      expect(anchor?.className).toContain("text-[13px]");
      expect(anchor?.className).toContain("py-1.5");
    }
  });

  it("no longer lists the retired Evaluation page or the old Users label", () => {
    render(<Sidebar />);

    expect(screen.queryByText("Evaluation")).not.toBeInTheDocument();
    expect(screen.queryByText("Users")).not.toBeInTheDocument();
    expect(document.querySelector('a[href="/admin/evaluation"]')).toBeNull();
    expect(screen.getByText("Visitors").closest("a")).toHaveAttribute("href", "/admin/users");
  });

  it("renders the Admin accordion button with uppercase tracking and text-[11px]", () => {
    render(<Sidebar />);

    const adminButton = screen.getByRole("button", { name: /admin/i });
    expect(adminButton).toBeInTheDocument();
    expect(adminButton.className).toContain("text-[11px]");
  });

  it("toggles sidebar collapsed state on expand/collapse button click", () => {
    const { container } = render(<Sidebar />);

    const aside = container.querySelector("aside");
    expect(aside).toHaveClass("w-64");

    const toggleButton = screen.getByTitle("Collapse sidebar");
    fireEvent.click(toggleButton);

    expect(aside).toHaveClass("w-16");
  });
});

describe("Sidebar nav registry, icons and active indicator (PR-17)", () => {
  // The collapse test above stores "collapsed"; start each case expanded.
  beforeEach(() => window.localStorage.clear());

  it("pre-registers Analytics and Settings but does not render them yet; User Management is live (PR-18)", () => {
    signInAs("superadmin");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 })));
    render(<Sidebar />);
    const planned = NAV_ITEMS.filter((item) => !isNavItemEnabled(item)).map((item) => item.label);
    expect(planned).toEqual(["Analytics", "Settings"]);
    for (const label of planned) expect(screen.queryByText(label)).not.toBeInTheDocument();
    const um = NAV_ITEMS.find((item) => item.label === "User Management");
    expect(um?.badgeKey).toBe("pendingAccess");
    expect(um?.permission).toBe("users:manage");
    expect(NAV_ITEMS.find((item) => item.label === "Settings")?.permission).toBe("branding:write");
    expect(screen.getByRole("link", { name: /user management/i })).toHaveAttribute("href", "/admin/user-management");
  });

  it("marks only the current page active, with the layoutId indicator and 18px / 1.75 icons", () => {
    render(<Sidebar />);
    const active = screen.getByRole("link", { current: "page" });
    expect(active).toHaveTextContent("Usage & Cost");
    expect(screen.getAllByRole("link", { current: "page" })).toHaveLength(1);
    expect(screen.getAllByTestId("nav-active-indicator")).toHaveLength(1);
    const icon = active.querySelector("svg");
    expect(icon).toHaveClass("h-[18px]", "w-[18px]");
    expect(icon).toHaveAttribute("stroke-width", "1.75");
  });

  it("names the icon-only controls", () => {
    render(<Sidebar />);
    expect(screen.getByRole("button", { name: "Collapse sidebar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });
});

describe("Mobile off-canvas sidebar (F0.6.6)", () => {
  beforeEach(() => window.localStorage.clear());

  it("opens from the hamburger, traps focus, closes on Esc and returns focus", async () => {
    const user = userEvent.setup();
    render(
      <MotionProvider>
        <MobileNavProvider>
          <MobileTopBar />
          <Sidebar />
        </MobileNavProvider>
      </MotionProvider>,
    );
    const hamburger = screen.getByRole("button", { name: "Open navigation" });
    expect(hamburger).toHaveAttribute("aria-expanded", "false");
    hamburger.focus();
    fireEvent.click(hamburger);

    const drawer = await screen.findByRole("dialog", { name: "Navigation" });
    expect(hamburger).toHaveAttribute("aria-expanded", "true");
    expect(within(drawer).getByRole("link", { name: /feedback/i })).toHaveAttribute("href", "/admin/feedback");
    await waitFor(() => expect(within(drawer).getByRole("button", { name: "Close navigation" })).toHaveFocus());

    // Shift+Tab from the first control wraps to the last link inside the drawer.
    await user.tab({ shift: true });
    expect(within(drawer).getByRole("link", { name: /user management/i })).toHaveFocus();
    await user.tab();
    expect(within(drawer).getByRole("button", { name: "Close navigation" })).toHaveFocus();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Navigation" })).not.toBeInTheDocument());
    expect(hamburger).toHaveFocus();
  });

  it("closes when a link is chosen", async () => {
    render(
      <MotionProvider>
        <MobileNavProvider>
          <MobileTopBar />
          <Sidebar />
        </MobileNavProvider>
      </MotionProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    const drawer = await screen.findByRole("dialog", { name: "Navigation" });
    fireEvent.click(within(drawer).getByRole("link", { name: /documents/i }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Navigation" })).not.toBeInTheDocument());
  });

  it("the desktop aside is hidden below md", () => {
    const { container } = render(<Sidebar />);
    expect(container.querySelector("aside")).toHaveClass("hidden", "md:flex");
  });
});

describe("Role-aware sidebar (PR-18, F1.3)", () => {
  beforeEach(() => window.localStorage.clear());

  const DASHBOARD_LINKS = ["Dashboard", "Documents", "Visitors", "Usage & Cost", "Conversation Logs", "Feedback", "Admin Governance"];

  const linkNames = () => screen.getAllByRole("link").map((a) => a.textContent?.trim());

  it.each([
    ["operator", false],
    ["admin", false],
    ["superadmin", true],
  ] as const)("%s sees the dashboard items; User Management only with users:manage", (role, seesUserManagement) => {
    signInAs(role);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 })));
    render(<Sidebar />);
    const names = linkNames();
    for (const label of DASHBOARD_LINKS) expect(names).toContain(label);
    expect(names.some((n) => n?.startsWith("User Management"))).toBe(seesUserManagement);
    expect(names).not.toContain("Users");
  });

  it.each(["pending", "rejected"] as const)("a %s user without a role sees Chat only", (status) => {
    signInAs(null, status);
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    render(<Sidebar />);
    expect(linkNames()).toEqual(["Chat"]);
    // No access-request count is requested without users:manage.
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("shows the pending access-request count as a badge for superadmins", async () => {
    signInAs("superadmin");
    const fetchSpy = vi.fn(async (_url: string) => new Response(JSON.stringify({ items: [], total: 3, limit: 1, offset: 0 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    render(
      <MotionProvider>
        <Sidebar />
      </MotionProvider>,
    );
    const badge = await screen.findByTestId("nav-badge");
    expect(badge).toHaveTextContent("3");
    expect(badge).toHaveAttribute("aria-label", "3 pending");
    expect(screen.getByRole("link", { name: /user management/i })).toContainElement(badge);
    expect(String(fetchSpy.mock.calls[0]?.[0])).toBe("/api/proxy/admin/access-requests?status=pending&limit=1");
  });

  it("collapsed rail keeps every visible item reachable with a tooltip", () => {
    signInAs("admin");
    render(<Sidebar />);
    fireEvent.click(screen.getByTitle("Collapse sidebar"));
    for (const link of screen.getAllByRole("link")) expect(link).toHaveAttribute("title");
    expect(screen.getByRole("link", { name: /visitors/i })).toHaveAttribute("title", "Visitors");
  });

  it("profile area shows the real name, email and role badge, and signs out", () => {
    signInAs("superadmin");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ total: 0 }), { status: 200 })));
    render(<Sidebar />);
    expect(screen.getByText("Jane Doe")).toBeInTheDocument();
    expect(screen.getByText("jane@contoso.example")).toBeInTheDocument();
    expect(screen.getByTestId("role-badge")).toHaveTextContent("Superadmin");
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(auth.logout).toHaveBeenCalled();
  });
});
