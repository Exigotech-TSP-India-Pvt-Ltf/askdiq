import React from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { ProfileMenu } from "@/components/shell/ProfileMenu";
import { NotificationBell } from "@/components/shell/NotificationBell";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { ThemeProvider } from "@/lib/theme-context";
import { BrandProvider } from "@/lib/brand-context";
import { DEFAULT_BRAND } from "@/lib/brand";
import NotFound from "@/app/not-found";
import ErrorPage from "@/app/error";
import { LayoutDashboard } from "lucide-react";

const logout = vi.fn();
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    token: "t",
    user: { id: "u1", full_name: "Jane Doe", email: "jane@exigotech.com", created_at: "" },
    isLoading: false,
    logout,
  }),
}));

vi.mock("@/lib/audience-context", () => ({
  useAudience: () => ({ audience: "overview", setAudience: vi.fn(), counts: null }),
}));

describe("ProfileMenu (F0.6 shell)", () => {
  it("shows identity, a role badge slot, theme choice and sign out, with arrow-key navigation", async () => {
    render(
      <ThemeProvider>
        <MotionProvider>
          <ProfileMenu roleBadge={<span>Superadmin</span>} />
        </MotionProvider>
      </ThemeProvider>,
    );
    const trigger = screen.getByRole("button", { name: "Account menu for Jane Doe" });
    expect(trigger).toHaveTextContent("JD");
    fireEvent.click(trigger);

    const menu = await screen.findByRole("menu", { name: "Account" });
    expect(menu).toHaveTextContent("jane@exigotech.com");
    expect(menu).toHaveTextContent("Superadmin");

    const light = screen.getByRole("menuitemradio", { name: "Light" });
    await waitFor(() => expect(light).toHaveFocus());
    fireEvent.keyDown(light, { key: "ArrowDown" });
    const dark = screen.getByRole("menuitemradio", { name: "Dark" });
    expect(dark).toHaveFocus();
    fireEvent.click(dark);
    expect(dark).toHaveAttribute("aria-checked", "true");
    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));

    fireEvent.keyDown(dark, { key: "End" });
    const signOut = screen.getByRole("menuitem", { name: "Sign out" });
    expect(signOut).toHaveFocus();
    fireEvent.click(signOut);
    expect(logout).toHaveBeenCalledTimes(1);
  });

  it("closes on Esc and returns focus to the avatar", async () => {
    render(
      <MotionProvider>
        <ProfileMenu />
      </MotionProvider>,
    );
    const trigger = screen.getByRole("button", { name: /account menu/i });
    fireEvent.click(trigger);
    await screen.findByRole("menu");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
  });
});

describe("ProfileMenu Help link", () => {
  it("links to the branding help URL and is hidden when it is unset", async () => {
    const { unmount } = render(
      <BrandProvider brand={{ ...DEFAULT_BRAND, links: { ...DEFAULT_BRAND.links, help: "https://help.example.com/askdiq" } }}>
        <MotionProvider>
          <ProfileMenu />
        </MotionProvider>
      </BrandProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /account menu/i }));
    const help = await screen.findByRole("menuitem", { name: "Help" });
    expect(help).toHaveAttribute("href", "https://help.example.com/askdiq");
    expect(help).toHaveAttribute("rel", expect.stringContaining("noopener"));
    unmount();

    render(
      <MotionProvider>
        <ProfileMenu />
      </MotionProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /account menu/i }));
    await screen.findByRole("menu");
    expect(screen.queryByRole("menuitem", { name: "Help" })).toBeNull();
  });
});

describe("NotificationBell (F0.6 shell)", () => {
  it("shows an empty state until notifications are wired in PR-18", async () => {
    render(
      <MotionProvider>
        <NotificationBell />
      </MotionProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
    const panel = await screen.findByRole("dialog", { name: "Notifications" });
    expect(panel).toHaveTextContent("You're all caught up");
  });

  it("shows a count badge and linked items", async () => {
    render(
      <MotionProvider>
        <NotificationBell items={[{ id: "1", title: "2 access requests", href: "/admin/user-management", tone: "warn" }]} />
      </MotionProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Notifications (1 new)" }));
    expect(await screen.findByRole("link", { name: /2 access requests/i })).toHaveAttribute(
      "href",
      "/admin/user-management",
    );
  });
});

describe("DashboardHeader utilities", () => {
  it("carries the bell and profile menu (theme moved into the profile menu)", () => {
    render(<DashboardHeader title="Dashboard" icon={LayoutDashboard} />);
    expect(screen.getByRole("button", { name: "Notifications" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /account menu for jane doe/i })).toBeInTheDocument();
    // Without a CommandCenter there is no palette button to show.
    expect(screen.queryByRole("button", { name: "Open command palette" })).not.toBeInTheDocument();
  });
});

describe("Branded error pages (F0.6.6)", () => {
  it("not-found uses BrandLogo and offers a way back", () => {
    render(<NotFound />);
    expect(screen.getAllByRole("img", { name: "Exigo Tech" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the dashboard" })).toHaveAttribute("href", "/admin");
  });

  it("error page shows the reference, retries and never prints the error message", () => {
    const reset = vi.fn();
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const error = Object.assign(new Error("secret stack detail"), { digest: "abc123" });
    render(
      <MotionProvider>
        <ErrorPage error={error} reset={reset} />
      </MotionProvider>,
    );
    expect(screen.getAllByRole("img", { name: "Exigo Tech" }).length).toBeGreaterThan(0);
    expect(screen.getByText("abc123")).toBeInTheDocument();
    expect(screen.queryByText(/secret stack detail/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(reset).toHaveBeenCalledTimes(1);
    spy.mockRestore();
  });
});
