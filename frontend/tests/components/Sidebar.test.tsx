import React from "react";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NAV_ITEMS, Sidebar, canSeeNavItem, isNavItemEnabled } from "@/components/Sidebar";
import { MobileTopBar } from "@/components/shell/MobileTopBar";
import { CommandCenter } from "@/components/shell/CommandCenter";
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
  // The collapsed preference persists; each test starts expanded.
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin/usage",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

describe("Sidebar Navigation Component (Task 4.3)", () => {
  it("renders every admin-visible navigation link with text-[13px] typography", () => {
    render(<Sidebar />);

    const expectedLabels = [
      "Dashboard",
      "AskDIQ",
      "Knowledge",
      "Visitors & Conversations",
      "Usage & Cost",
      "Feedback",
      "Evaluation",
      "Governance",
      "Analytics",
    ];

    for (const label of expectedLabels) {
      const link = screen.getByText(label);
      expect(link).toBeInTheDocument();
      const anchor = link.closest("a");
      expect(anchor?.className).toContain("text-[13px]");
      expect(anchor?.className).toContain("py-1.5");
    }
  });

  it("links Evaluation to the live page (not the retired one) and drops the old Users label", () => {
    render(<Sidebar />);

    expect(screen.getByText("Evaluation").closest("a")).toHaveAttribute("href", "/admin/evaluation-live");
    expect(screen.queryByText("Users")).not.toBeInTheDocument();
    expect(document.querySelector('a[href="/admin/evaluation"]')).toBeNull();
    expect(screen.getByText("Visitors & Conversations").closest("a")).toHaveAttribute("href", "/admin/visitors");
  });

  it("shows one flat list with no collapsible group header (no ADMIN submenu)", () => {
    signInAs("superadmin");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ total: 0 }), { status: 200 })));
    render(<Sidebar />);

    const nav = screen.getAllByRole("navigation", { name: "Main" })[0];
    expect(within(nav).queryAllByRole("button")).toHaveLength(0);
    expect(within(nav).queryByText(/^admin$/i)).toBeNull();
    expect(nav.querySelector("[aria-expanded]")).toBeNull();
    expect(within(nav).getAllByRole("link").map((a) => a.textContent?.trim())).toEqual([
      "Dashboard",
      "AskDIQ",
      "Knowledge",
      "Visitors & Conversations",
      "Usage & Cost",
      "Feedback",
      "Evaluation",
      "Governance",
      "Analytics",
      "User Management",
      "Settings",
    ]);
  });

  it("collapsed rail still offers Sign out", () => {
    render(<Sidebar />);
    fireEvent.click(screen.getByTitle("Collapse sidebar"));
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(auth.logout).toHaveBeenCalled();
  });

  it("Sign out shows a busy state and ignores repeat clicks while signing out", () => {
    auth.logout.mockReset();
    auth.logout.mockReturnValue(new Promise<void>(() => undefined));
    render(<Sidebar />);
    const button = screen.getByRole("button", { name: "Sign out" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(auth.logout).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
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

  it("registers every page enabled in the combined preview, role-filtered (PR-18)", () => {
    signInAs("superadmin");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 })));
    render(<Sidebar />);
    expect(NAV_ITEMS.filter((item) => !isNavItemEnabled(item))).toEqual([]);
    expect(NAV_ITEMS.map((item) => item.href)).not.toContain("/admin/evaluation");
    const um = NAV_ITEMS.find((item) => item.label === "User Management");
    expect(um?.badgeKey).toBe("pendingAccess");
    expect(um?.permission).toBe("users:manage");
    expect(NAV_ITEMS.find((item) => item.label === "Settings")?.anyPermission).toEqual(["branding:write", "taxonomy:write", "models:write"]);
    expect(screen.getByRole("link", { name: /user management/i })).toHaveAttribute("href", "/admin/user-management");
    expect(screen.getByRole("link", { name: /^analytics$/i })).toHaveAttribute("href", "/admin/analytics");
    expect(screen.getByRole("link", { name: /^settings$/i })).toHaveAttribute("href", "/admin/settings");
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
    // Admin's last item (no users:manage / branding:write) is Analytics.
    expect(within(drawer).getByRole("link", { name: /^analytics$/i })).toHaveFocus();
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
    fireEvent.click(within(drawer).getByRole("link", { name: /knowledge/i }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Navigation" })).not.toBeInTheDocument());
  });

  it("the desktop aside is hidden below md", () => {
    const { container } = render(<Sidebar />);
    expect(container.querySelector("aside")).toHaveClass("hidden", "md:flex");
  });
});

describe("Role-aware sidebar (PR-18, F1.3)", () => {
  beforeEach(() => window.localStorage.clear());

  const DASHBOARD_LINKS = ["Dashboard", "Knowledge", "Visitors & Conversations", "Usage & Cost", "Feedback", "Governance", "Analytics"];

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
    expect(linkNames()).toEqual(["AskDIQ"]);
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
    expect(screen.getByRole("link", { name: /visitors/i })).toHaveAttribute("title", "Visitors & Conversations");
  });

  it("profile area shows the real name, email and role badge, and signs out", () => {
    signInAs("superadmin");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ total: 0 }), { status: 200 })));
    render(<Sidebar />);
    expect(screen.getByText("Jane Doe")).toHaveAttribute("title", "Jane Doe");
    const email = screen.getByTestId("account-email");
    expect(email).toHaveTextContent("jane@contoso.example");
    // Full address on hover even when the line truncates.
    expect(email).toHaveAttribute("title", "jane@contoso.example");
    expect(screen.getByTestId("role-badge")).toHaveTextContent("Superadmin");
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(auth.logout).toHaveBeenCalled();
  });

  it("puts the role badge, theme toggle and sign out on their own row, not beside the identity", () => {
    signInAs("superadmin");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ total: 0 }), { status: 200 })));
    render(<Sidebar />);
    const identity = screen.getByText("Jane Doe").parentElement!;
    const signOut = screen.getByRole("button", { name: "Sign out" });
    const actions = signOut.parentElement!.parentElement!;
    expect(identity).not.toContainElement(signOut);
    expect(identity).not.toContainElement(screen.getByTestId("role-badge"));
    expect(actions).toContainElement(screen.getByTestId("role-badge"));
    expect(actions).toContainElement(screen.getByRole("button", { name: /theme:/i }));
  });

  it("collapsed account shows an avatar with the name, email and role as its label", () => {
    signInAs("superadmin");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ total: 0 }), { status: 200 })));
    render(<Sidebar />);
    fireEvent.click(screen.getByTitle("Collapse sidebar"));
    const avatar = screen.getByRole("img", { name: "Jane Doe, jane@contoso.example, Superadmin" });
    expect(avatar).toHaveTextContent("JD");
    expect(screen.queryByTestId("account-email")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /theme:/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });
});

describe("Go to page (command palette) in the sidebar", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  function renderWithCenter(children: React.ReactNode = <Sidebar />) {
    return render(
      <MotionProvider>
        <CommandCenter>{children}</CommandCenter>
      </MotionProvider>,
    );
  }

  it("is hidden without a CommandCenter", () => {
    render(<Sidebar />);
    expect(screen.queryByRole("button", { name: "Go to page" })).not.toBeInTheDocument();
  });

  it("expanded: a search-field button with the Ctrl K hint that opens the palette", async () => {
    renderWithCenter();
    const button = screen.getByRole("button", { name: "Go to page" });
    expect(button).toHaveAttribute("data-tour", "shell-command-palette");
    expect(button).toHaveAttribute("aria-keyshortcuts", "Control+K Meta+K");
    expect(button).toHaveTextContent("Go to page…");
    expect(button.querySelector("kbd")).toHaveTextContent("Ctrl K");
    // Sits above the nav, not inside it.
    expect(screen.getByRole("navigation", { name: "Main" })).not.toContainElement(button);
    fireEvent.click(button);
    expect(await screen.findByRole("dialog", { name: "Command palette" })).toBeInTheDocument();
  });

  it("shows ⌘K on Apple platforms", async () => {
    vi.spyOn(window.navigator, "platform", "get").mockReturnValue("MacIntel");
    renderWithCenter();
    await waitFor(() => expect(screen.getByRole("button", { name: "Go to page" }).querySelector("kbd")).toHaveTextContent("⌘K"));
  });

  it("collapsed: an icon button with a tooltip", () => {
    renderWithCenter();
    fireEvent.click(screen.getByTitle("Collapse sidebar"));
    const button = screen.getByRole("button", { name: "Go to page" });
    expect(button).toHaveAttribute("title", "Go to page (Ctrl K)");
    expect(button.querySelector("kbd")).toBeNull();
  });

  it("Ctrl+K still toggles the palette", async () => {
    renderWithCenter();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(await screen.findByRole("dialog", { name: "Command palette" })).toBeInTheDocument();
  });

  it("mobile drawer offers it at the top and closes itself before opening the palette", async () => {
    renderWithCenter(
      <MobileNavProvider>
        <MobileTopBar />
        <Sidebar />
      </MobileNavProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    const drawer = await screen.findByRole("dialog", { name: "Navigation" });
    fireEvent.click(within(drawer).getByRole("button", { name: "Go to page" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Navigation" })).not.toBeInTheDocument());
    expect(await screen.findByRole("dialog", { name: "Command palette" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("combobox")).toHaveFocus());
  });
});

describe("Settings nav gating matches the page", () => {
  const settings = NAV_ITEMS.find((item) => item.label === "Settings")!;
  it("shows Settings with branding:write or taxonomy:write, hides it with neither", () => {
    expect(canSeeNavItem(settings, ["branding:write"])).toBe(true);
    expect(canSeeNavItem(settings, ["taxonomy:write"])).toBe(true);
    expect(canSeeNavItem(settings, ["dashboard:read"])).toBe(false);
  });
});
