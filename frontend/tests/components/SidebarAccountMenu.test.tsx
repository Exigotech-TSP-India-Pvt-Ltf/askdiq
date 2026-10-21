import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SidebarAccount } from "@/components/shell/SidebarAccount";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { TourProvider } from "@/components/tour/TourProvider";
import { BrandProvider } from "@/lib/brand-context";
import { DEFAULT_BRAND } from "@/lib/brand";
import { ThemeProvider } from "@/lib/theme-context";
import { THEME_STORAGE_KEY } from "@/lib/theme-script";
import { queryCache } from "@/lib/query-cache";
import { authValue, makeMe } from "../helpers/auth";

const auth = vi.hoisted(() => ({ current: null as unknown, logout: vi.fn() }));
vi.mock("@/lib/auth-context", () => ({ useAuth: () => auth.current }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/admin/usage",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

function renderMenu({ collapsed = false, pending = 0 }: { collapsed?: boolean; pending?: number } = {}) {
  auth.current = authValue(makeMe("superadmin"), { logout: auth.logout });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      String(url).includes("access-requests")
        ? new Response(JSON.stringify({ items: [], total: pending, limit: 1, offset: 0 }), { status: 200 })
        : new Response(JSON.stringify([]), { status: 200 }),
    ),
  );
  return render(
    <BrandProvider brand={{ ...DEFAULT_BRAND, links: { ...DEFAULT_BRAND.links, help: "https://help.example.com" } }}>
      <ThemeProvider>
        <MotionProvider>
          <TourProvider>
            <button type="button">Outside</button>
            <SidebarAccount collapsed={collapsed} />
          </TourProvider>
        </MotionProvider>
      </ThemeProvider>
    </BrandProvider>,
  );
}

const trigger = () => screen.getByRole("button", { name: /account menu/i });
const mainMenu = () => screen.getByRole("menu", { name: "Account" });
const items = (menu: HTMLElement) => Array.from(menu.querySelectorAll<HTMLElement>("[role^=menuitem]"));
const closed = () => waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());

beforeEach(() => {
  queryCache.clear();
  window.localStorage.clear();
  auth.logout.mockReset();
  auth.logout.mockResolvedValue(undefined);
});
afterEach(() => vi.unstubAllGlobals());

describe("SidebarAccount profile menu (OR-3)", () => {
  it("is an APG menu button: Enter opens it, focus lands on the first item, ↑ ↓ Home End move", async () => {
    const user = userEvent.setup();
    renderMenu();
    const button = trigger();
    expect(button).toHaveAttribute("aria-haspopup", "menu");
    expect(button).toHaveAttribute("aria-expanded", "false");
    button.focus();
    await user.keyboard("{Enter}");
    expect(button).toHaveAttribute("aria-expanded", "true");
    const menu = mainMenu();
    expect(button).toHaveAttribute("aria-controls", menu.parentElement!.id);
    const list = items(menu);
    expect(list.map((el) => el.textContent?.replace(/\d+$/, "").trim())).toEqual([
      "Notifications",
      "Walkthrough guide",
      "ThemeLight",
      "Sign out",
    ]);
    expect(list[0]).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(list[1]).toHaveFocus();
    await user.keyboard("{ArrowUp}{ArrowUp}");
    expect(list[3]).toHaveFocus();
    await user.keyboard("{Home}");
    expect(list[0]).toHaveFocus();
    await user.keyboard("{End}");
    expect(list[3]).toHaveFocus();
    // Typeahead: "t" jumps to Theme.
    await user.keyboard("t");
    expect(within(menu).getByRole("menuitem", { name: /^Theme/ })).toHaveFocus();
  });

  it("Space also opens it, and ArrowDown on the trigger opens it", async () => {
    const user = userEvent.setup();
    renderMenu();
    trigger().focus();
    await user.keyboard(" ");
    expect(mainMenu()).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await closed();
    fireEvent.keyDown(trigger(), { key: "ArrowDown" });
    expect(mainMenu()).toBeInTheDocument();
  });

  it("Esc closes one level at a time and returns focus (submenu parent, then the trigger)", async () => {
    const user = userEvent.setup();
    renderMenu();
    trigger().focus();
    await user.keyboard("{Enter}");
    const theme = within(mainMenu()).getByRole("menuitem", { name: /^Theme/ });
    theme.focus();
    await user.keyboard("{ArrowRight}");
    const sub = screen.getByRole("menu", { name: "Theme" });
    expect(within(sub).getByRole("menuitem", { name: /back to account menu/i })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu", { name: "Theme" })).not.toBeInTheDocument();
    expect(within(mainMenu()).getByRole("menuitem", { name: /^Theme/ })).toHaveFocus();
    await user.keyboard("{Escape}");
    await closed();
    expect(trigger()).toHaveFocus();
    expect(trigger()).toHaveAttribute("aria-expanded", "false");
  });

  it("← closes a submenu; Enter opens one", async () => {
    const user = userEvent.setup();
    renderMenu();
    trigger().focus();
    await user.keyboard("{Enter}");
    await user.keyboard("{Enter}");
    expect(screen.getByRole("menu", { name: "Notifications" })).toBeInTheDocument();
    await user.keyboard("{ArrowLeft}");
    expect(within(mainMenu()).getByRole("menuitem", { name: /^Notifications/ })).toHaveFocus();
  });

  it("closes on an outside click and on Tab", async () => {
    const user = userEvent.setup();
    renderMenu();
    fireEvent.click(trigger());
    expect(mainMenu()).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole("button", { name: "Outside" }));
    await closed();

    trigger().focus();
    await user.keyboard("{Enter}");
    expect(mainMenu()).toBeInTheDocument();
    await user.tab();
    await closed();
  });

  it("Theme submenu: Light and Dark radios only (no System), applied at once and persisted", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    }));
    renderMenu();
    fireEvent.click(trigger());
    fireEvent.click(within(mainMenu()).getByRole("menuitem", { name: /^Theme/ }));
    const sub = screen.getByRole("menu", { name: "Theme" });
    const radios = within(sub).getAllByRole("menuitemradio");
    expect(radios.map((r) => r.textContent)).toEqual(["Light", "Dark"]);
    expect(within(sub).queryByRole("menuitemradio", { name: /system/i })).not.toBeInTheDocument();
    // A missing preference resolved once to the OS setting (light here): it is the checked one.
    expect(within(sub).getByRole("menuitemradio", { name: "Light" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(within(sub).getByRole("menuitemradio", { name: "Dark" }));
    expect(within(sub).getByRole("menuitemradio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
    expect(within(sub).getByRole("menuitemradio", { name: "Light" })).toHaveAttribute("aria-checked", "false");
    expect(document.documentElement).toHaveClass("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    act(() => {
      fireEvent.click(within(sub).getByRole("menuitemradio", { name: "Light" }));
    });
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("shows the unread count on the Notifications item and a dot on the avatar", async () => {
    renderMenu({ pending: 3 });
    const button = await screen.findByRole("button", { name: /account menu \(1 new notification\)/i });
    expect(screen.getByTestId("account-unread-dot")).toBeInTheDocument();
    fireEvent.click(button);
    expect(within(mainMenu()).getByRole("menuitem", { name: /^Notifications/ })).toHaveTextContent(/1$/);
    fireEvent.click(within(mainMenu()).getByRole("menuitem", { name: /^Notifications/ }));
    const sub = screen.getByRole("menu", { name: "Notifications" });
    fireEvent.click(within(sub).getByRole("menuitem", { name: "Mark all read" }));
    expect(screen.queryByTestId("account-unread-dot")).not.toBeInTheDocument();
    expect(trigger()).toHaveAccessibleName("Jane Doe, account menu");
  });

  it("Walkthrough guide lists this page's tour and the basics, and starts one", async () => {
    renderMenu();
    fireEvent.click(trigger());
    fireEvent.click(within(mainMenu()).getByRole("menuitem", { name: "Walkthrough guide" }));
    const sub = screen.getByRole("menu", { name: "Walkthrough guide" });
    expect(within(sub).getByRole("menuitem", { name: /tour this page/i })).toBeInTheDocument();
    fireEvent.click(within(sub).getByRole("menuitem", { name: /dashboard basics/i }));
    await closed();
    expect(screen.getByRole("dialog")).toHaveTextContent("Welcome to the AskDIQ dashboard");
  });

  it("Sign out calls logout once", () => {
    renderMenu();
    fireEvent.click(trigger());
    const item = within(mainMenu()).getByRole("menuitem", { name: "Sign out" });
    fireEvent.click(item);
    fireEvent.click(item);
    expect(auth.logout).toHaveBeenCalledTimes(1);
  });

  it("has no Help item even when the brand sets a help URL", () => {
    renderMenu();
    fireEvent.click(trigger());
    expect(within(mainMenu()).queryByRole("menuitem", { name: /help/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /help/i })).not.toBeInTheDocument();
  });

  it("collapsed: avatar-only trigger with a tooltip, menu beside the rail", () => {
    renderMenu({ collapsed: true });
    expect(trigger()).toHaveAttribute("title", expect.stringContaining("Jane Doe"));
    fireEvent.click(trigger());
    const panel = mainMenu().parentElement!;
    expect(panel).toHaveAttribute("data-placement", "side");
    expect(panel.className).toContain("left-full");
  });
});
