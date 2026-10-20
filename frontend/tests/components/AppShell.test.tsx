import React from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { NotificationBell } from "@/components/shell/NotificationBell";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
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
    // Opens upward from the bottom-left account corner by default.
    expect(panel.className).toContain("bottom-full");
  });

  it("opens sideways from the collapsed rail", async () => {
    render(
      <MotionProvider>
        <NotificationBell placement="side" />
      </MotionProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
    const panel = await screen.findByRole("dialog", { name: "Notifications" });
    expect(panel.className).toContain("left-full");
  });

  it("shows a count badge and linked items", async () => {
    render(
      <MotionProvider>
        <NotificationBell items={[{ id: "1", title: "2 access requests", href: "/admin/user-management", tone: "warn" }]} />
      </MotionProvider>,
    );
    const trigger = screen.getByRole("button", { name: "Notifications (1 new)" });
    expect(trigger).toHaveAttribute("title", "Notifications (1 new)");
    expect(trigger).toHaveTextContent("1");
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByRole("link", { name: /2 access requests/i })).toHaveAttribute(
      "href",
      "/admin/user-management",
    );
  });
});

describe("DashboardHeader utilities", () => {
  it("holds only page actions: bell, tours and the account live in the sidebar", () => {
    render(<DashboardHeader title="Dashboard" icon={LayoutDashboard} actions={<button type="button">Export</button>} />);
    expect(screen.getByRole("button", { name: "Export" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /notifications/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /account menu/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /take the tour/i })).not.toBeInTheDocument();
    // The go-to-page (palette) trigger lives in the sidebar now, never in the header.
    expect(screen.queryByRole("button", { name: /go to page|command palette/i })).not.toBeInTheDocument();
  });
});

describe("Branded error pages", () => {
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
