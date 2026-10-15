import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FileText, LayoutDashboard, UserCog } from "lucide-react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { CommandPalette, SEARCH_DEBOUNCE_MS, type PaletteItem } from "@/components/ui/CommandPalette";
import { CommandCenter, PALETTE_PAGES } from "@/components/shell/CommandCenter";
import { isTypingTarget } from "@/lib/shortcuts";
import { AuthProvider } from "@/lib/auth-context";
import type { Role } from "@/lib/permissions";
import { makeMe } from "../helpers/auth";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => "/admin",
  useSearchParams: () => new URLSearchParams(),
}));

afterEach(() => {
  push.mockClear();
});

const PAGES: PaletteItem[] = [
  { id: "dash", label: "Dashboard", href: "/admin", icon: LayoutDashboard },
  { id: "docs", label: "Documents", href: "/documents", icon: FileText, keywords: ["knowledge base"] },
  { id: "um", label: "User Management", href: "/admin/user-management", icon: UserCog, permission: "users.manage" },
];

function renderPalette(props: Partial<React.ComponentProps<typeof CommandPalette>> = {}) {
  const onClose = vi.fn();
  const onNavigate = vi.fn();
  render(
    <MotionProvider>
      <CommandPalette open onClose={onClose} pages={PAGES} onNavigate={onNavigate} {...props} />
    </MotionProvider>,
  );
  return { onClose, onNavigate };
}

describe("CommandPalette (F0.6.4)", () => {
  it("moves with the arrow keys and opens the active result with Enter", async () => {
    const { onNavigate, onClose } = renderPalette();
    const input = await screen.findByRole("combobox", { name: /search pages and records/i });
    await waitFor(() => expect(input).toHaveFocus());

    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(3);
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    expect(input).toHaveAttribute("aria-activedescendant", options[0].id);

    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "ArrowUp" });
    fireEvent.keyDown(input, { key: "ArrowUp" });
    // Wraps from the first to the last result.
    expect(screen.getAllByRole("option")[2]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "Home" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onNavigate).toHaveBeenCalledWith("/documents");
    expect(onClose).toHaveBeenCalled();
  });

  it("filters by label and keywords, and shows an empty state", async () => {
    const user = userEvent.setup();
    renderPalette();
    const input = await screen.findByRole("combobox");
    await user.type(input, "knowledge");
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([expect.stringContaining("Documents")]);
    await user.clear(input);
    await user.type(input, "zzz");
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByText("No matching pages or records")).toBeInTheDocument();
  });

  it("hides results the user is not permitted to see", async () => {
    renderPalette({ canAccess: (permission) => permission !== "users.manage" });
    await screen.findByRole("combobox");
    expect(screen.queryByRole("option", { name: /user management/i })).not.toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(2);
  });

  it("debounces the search provider by 200 ms, aborts stale queries and filters remote results by permission", async () => {
    const searchProvider = vi.fn(async (query: string) => [
      { id: "c-1", label: `Conversation about ${query}`, href: "/admin/conversations/c-1", hint: "c-1" },
      { id: "u-1", label: "admin@example.com", href: "/admin/user-management?user=u-1", permission: "users.manage" },
    ]);
    renderPalette({ searchProvider, canAccess: (p) => p !== "users.manage" });
    const input = await screen.findByRole("combobox");

    // Real timers: Motion's frame loop must not see a faked clock.
    const started = Date.now();
    fireEvent.change(input, { target: { value: "mf" } });
    fireEvent.change(input, { target: { value: "mfa" } });
    await new Promise((resolve) => setTimeout(resolve, SEARCH_DEBOUNCE_MS / 2));
    expect(searchProvider).not.toHaveBeenCalled();
    await waitFor(() => expect(searchProvider).toHaveBeenCalledTimes(1));
    expect(Date.now() - started).toBeGreaterThanOrEqual(SEARCH_DEBOUNCE_MS - 5);
    expect(searchProvider.mock.calls[0][0]).toBe("mfa");

    expect(await screen.findByRole("option", { name: /conversation about mfa/i })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /admin@example.com/i })).not.toBeInTheDocument();
    expect(screen.getByText("Results")).toBeInTheDocument();
  });

  it("Esc closes", async () => {
    const { onClose } = renderPalette();
    await screen.findByRole("combobox");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});

describe("CommandCenter shortcuts (F0.6.4)", () => {
  function renderCenter(role: Role = "admin") {
    render(
      <AuthProvider initialMe={makeMe(role)}>
        <MotionProvider>
          <CommandCenter>
            <label>
              Search table
              <input />
            </label>
          </CommandCenter>
        </MotionProvider>
      </AuthProvider>,
    );
  }

  it("Ctrl+K and Cmd+K toggle the palette, even from a text field", async () => {
    renderCenter();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(await screen.findByRole("dialog", { name: "Command palette" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Command palette" })).not.toBeInTheDocument());

    const field = screen.getByLabelText("Search table");
    field.focus();
    fireEvent.keyDown(field, { key: "K", metaKey: true });
    expect(await screen.findByRole("dialog", { name: "Command palette" })).toBeInTheDocument();
  });

  it("navigates to an enabled page with the router", async () => {
    renderCenter();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByRole("combobox");
    fireEvent.change(input, { target: { value: "feedback" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(push).toHaveBeenCalledWith("/admin/feedback");
  });

  it("? opens the shortcuts sheet, but not while typing", async () => {
    renderCenter();
    const field = screen.getByLabelText("Search table");
    field.focus();
    fireEvent.keyDown(field, { key: "?", shiftKey: true });
    expect(screen.queryByRole("dialog", { name: "Keyboard shortcuts" })).not.toBeInTheDocument();

    field.blur();
    fireEvent.keyDown(document.body, { key: "?", shiftKey: true });
    const sheet = await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
    expect(sheet).toHaveTextContent("Open the command palette");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Keyboard shortcuts" })).not.toBeInTheDocument());
  });

  it("offers only enabled sidebar pages (pre-registered pages stay hidden)", () => {
    const hrefs = PALETTE_PAGES.map((p) => p.href);
    expect(hrefs).toContain("/admin/governance");
    expect(hrefs).not.toContain("/admin/analytics");
    expect(hrefs).not.toContain("/admin/settings");
    // Enabled by PR-18, but only for roles with users:manage.
    expect(PALETTE_PAGES.find((p) => p.href === "/admin/user-management")?.permission).toBe("users:manage");
  });

  it("filters palette pages by the signed-in role's permissions (PR-18)", async () => {
    renderCenter("admin");
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByRole("combobox");
    fireEvent.change(input, { target: { value: "user management" } });
    expect(screen.queryByRole("option", { name: /user management/i })).not.toBeInTheDocument();
  });

  it("shows User Management in the palette for a superadmin (PR-18)", async () => {
    renderCenter("superadmin");
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByRole("combobox");
    fireEvent.change(input, { target: { value: "user management" } });
    expect(await screen.findByRole("option", { name: /user management/i })).toBeInTheDocument();
  });

  it("isTypingTarget treats text fields as typing and buttons / checkboxes as not", () => {
    const text = document.createElement("input");
    const box = document.createElement("input");
    box.type = "checkbox";
    const area = document.createElement("textarea");
    expect(isTypingTarget(text)).toBe(true);
    expect(isTypingTarget(area)).toBe(true);
    expect(isTypingTarget(box)).toBe(false);
    expect(isTypingTarget(document.createElement("button"))).toBe(false);
  });
});
