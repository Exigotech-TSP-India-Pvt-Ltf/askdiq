import React from "react";
import { describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import {
  MarkAllReadButton,
  NotificationList,
  useNotificationReadState,
  type NotificationItem,
} from "@/components/shell/NotificationBell";
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

describe("Notification feed (OR-3 menu / VA-48)", () => {
  function Feed({ items }: { items: NotificationItem[] }) {
    const { unread, isUnread, markAllRead } = useNotificationReadState(items);
    return (
      <MotionProvider>
        <span data-testid="unread">{unread}</span>
        <NotificationList items={items} isUnread={isUnread} />
        <MarkAllReadButton onClick={markAllRead} disabled={unread === 0} />
      </MotionProvider>
    );
  }

  it("shows an empty state when there is nothing new", () => {
    render(<Feed items={[]} />);
    expect(screen.getByText("You're all caught up")).toBeInTheDocument();
  });

  it("links every item and lets titles and descriptions wrap to two lines with the full text as a tooltip", () => {
    const long = "Host memory at least 86% on ET-PLATFORM for the last 10 minutes and still rising";
    render(<Feed items={[{ id: "1", title: long, description: long, href: "/admin/governance?tab=Monitoring", tone: "warn" }]} />);
    const link = screen.getByRole("link", { name: /host memory/i });
    expect(link).toHaveAttribute("href", "/admin/governance?tab=Monitoring");
    const [title, body] = screen.getAllByTitle(long);
    expect(title).toHaveClass("line-clamp-2");
    expect(title).not.toHaveClass("truncate");
    expect(body).toHaveClass("line-clamp-2");
  });

  it("Mark all read clears the unread count and remembers it in this browser", () => {
    window.localStorage.clear();
    const items: NotificationItem[] = [{ id: "a", title: "2 access requests waiting", href: "/admin/user-management" }];
    const { unmount } = render(<Feed items={items} />);
    expect(screen.getByTestId("unread")).toHaveTextContent("1");
    fireEvent.click(screen.getByRole("button", { name: "Mark all read" }));
    expect(screen.getByTestId("unread")).toHaveTextContent("0");
    unmount();
    render(<Feed items={items} />);
    expect(screen.getByTestId("unread")).toHaveTextContent("0");
  });

  it("an item whose text changes counts as new again", () => {
    window.localStorage.clear();
    const { rerender } = render(<Feed items={[{ id: "a", title: "2 access requests waiting" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "Mark all read" }));
    rerender(<Feed items={[{ id: "a", title: "3 access requests waiting" }]} />);
    expect(screen.getByTestId("unread")).toHaveTextContent("1");
  });
});

describe("DashboardHeader utilities", () => {
  it("holds no actions (OR-2): bell, tours and the account live in the sidebar, page actions on the tab row", () => {
    const { container } = render(<DashboardHeader title="Dashboard" icon={LayoutDashboard} />);
    expect(container.querySelector("[data-header-actions]")).toBeNull();
    expect(screen.queryByRole("button", { name: /notifications/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /account menu/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /take the tour/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /date range/i })).not.toBeInTheDocument();
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
