/**
 * Phase F0 integration journey: brand, theme, palette,
 * toast with undo and deep links working together, with `fetch` mocked and an
 * in-memory router history.
 */
import React, { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { fetchBrand } from "@/lib/brand-server";
import { BrandProvider } from "@/lib/brand-context";
import { ThemeProvider } from "@/lib/theme-context";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { ToastProvider, useToast } from "@/components/ui/ToastProvider";
import { CommandCenter } from "@/components/shell/CommandCenter";
import { ThemeToggle } from "@/components/ThemeToggle";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { EntityLink } from "@/components/ui/EntityLink";
import { useDeepLinkParam } from "@/lib/use-deep-link";

const history = vi.hoisted(() => {
  const state = { entries: ["/admin"], index: 0, listeners: new Set<() => void>() };
  const notify = () => state.listeners.forEach((listener) => listener());
  return {
    state,
    current: () => state.entries[state.index],
    reset(url: string) {
      state.entries = [url];
      state.index = 0;
    },
    push: vi.fn((url: string) => {
      state.entries = [...state.entries.slice(0, state.index + 1), url];
      state.index += 1;
      notify();
    }),
    replace: vi.fn((url: string) => {
      state.entries[state.index] = url;
      notify();
    }),
    back() {
      state.index = Math.max(0, state.index - 1);
      notify();
    },
    forward() {
      state.index = Math.min(state.entries.length - 1, state.index + 1);
      notify();
    },
  };
});

vi.mock("next/navigation", async () => {
  const { useSyncExternalStore } = await import("react");
  const subscribe = (cb: () => void) => {
    history.state.listeners.add(cb);
    return () => {
      history.state.listeners.delete(cb);
    };
  };
  const useUrl = () => useSyncExternalStore(subscribe, history.current);
  return {
    useRouter: () => ({ push: history.push, replace: history.replace }),
    usePathname: () => useUrl().split("?")[0],
    useSearchParams: () => new URLSearchParams(useUrl().split("?")[1] ?? ""),
  };
});

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    token: "t",
    user: { id: "u1", full_name: "Jane Doe", email: "jane@contoso.example", created_at: "" },
    isLoading: false,
    logout: vi.fn(),
    role: "admin",
    status: "active",
    // Admin role (PR-18 filters nav and palette items by permission).
    permissions: ["dashboard:read", "documents:write", "feedback:triage", "eval:run"],
  }),
}));

const BRAND_PAYLOAD = {
  product_name: "Contoso IQ",
  company_name: "Contoso",
  logo_light_url: "/branding/assets/light",
  logo_dark_url: "/branding/assets/dark",
  palette: { "600": "#3355CC" },
};

beforeEach(() => {
  history.reset("/admin");
  history.push.mockClear();
  window.localStorage.clear();
});

afterEach(() => {
  document.documentElement.removeAttribute("style");
  document.documentElement.classList.remove("dark");
  vi.unstubAllGlobals();
});

function TriageRow() {
  const { show } = useToast();
  const [dismissed, setDismissed] = useState(false);
  return dismissed ? (
    <p>Row hidden</p>
  ) : (
    <button
      type="button"
      onClick={() => {
        setDismissed(true);
        show({ title: "Feedback dismissed", onUndo: () => setDismissed(false) });
      }}
    >
      Dismiss feedback
    </button>
  );
}

function TurnViewer() {
  const [turn, setTurn] = useDeepLinkParam("turn", { history: "push" });
  return (
    <div>
      <p data-testid="turn">{turn ?? "none"}</p>
      <button type="button" onClick={() => setTurn("m-1")}>
        Turn 1
      </button>
      <button type="button" onClick={() => setTurn("m-2")}>
        Turn 2
      </button>
    </div>
  );
}

describe("Phase F0 foundations journey", { timeout: 15_000 }, () => {
  it("loads the brand from /branding, writes the palette variables and swaps the logo with the theme", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => BRAND_PAYLOAD });
    const brand = await fetchBrand(fetchMock as unknown as typeof fetch);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    render(
      <ThemeProvider>
        <BrandProvider brand={brand}>
          <MotionProvider>
            <BrandLogo />
            <ThemeToggle size="sm" />
          </MotionProvider>
        </BrandProvider>
      </ThemeProvider>,
    );
    expect(document.documentElement.style.getPropertyValue("--brand-600")).toBe("51 85 204");

    const [light, dark] = screen.getAllByAltText("Contoso");
    expect(light).toHaveAttribute("src", "/branding/assets/light");
    expect(light).toHaveClass("dark:hidden");
    expect(dark).toHaveAttribute("src", "/branding/assets/dark");
    expect(dark).toHaveClass("hidden", "dark:block");

    // Switching to dark from the account-corner theme toggle flips the <html> class that the logo pair keys off.
    fireEvent.click(screen.getByRole("button", { name: /switch to dark mode/i }));
    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
    fireEvent.click(screen.getByRole("button", { name: /switch to light mode/i }));
    await waitFor(() => expect(document.documentElement).not.toHaveClass("dark"));
  });

  it("navigates with the command palette by keyboard only", async () => {
    render(
      <MotionProvider>
        <CommandCenter>
          <p>Page</p>
        </CommandCenter>
      </MotionProvider>,
    );
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByRole("combobox");
    await waitFor(() => expect(input).toHaveFocus());
    fireEvent.change(input, { target: { value: "gov" } });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(history.push).toHaveBeenCalledWith("/admin/governance");
    await waitFor(() => expect(screen.queryByRole("combobox")).not.toBeInTheDocument());
  });

  it("dismisses a row with a toast and restores it with Undo", async () => {
    render(
      <MotionProvider>
        <ToastProvider>
          <TriageRow />
        </ToastProvider>
      </MotionProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Dismiss feedback" }));
    expect(screen.getByText("Row hidden")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Notifications" })).toHaveTextContent("Feedback dismissed");

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getByRole("button", { name: "Dismiss feedback" })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Feedback dismissed")).not.toBeInTheDocument());
  });

  it("round-trips deep links through back and forward, and EntityLink points at the same URL", () => {
    history.reset("/admin/conversations/c-1");
    render(
      <>
        <TurnViewer />
        <EntityLink entity={{ kind: "turn", id: "m-2", conversationId: "c-1" }} />
      </>,
    );
    expect(screen.getByTestId("turn")).toHaveTextContent("none");
    fireEvent.click(screen.getByRole("button", { name: "Turn 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Turn 2" }));
    expect(history.current()).toBe("/admin/conversations/c-1?turn=m-2");
    expect(screen.getByRole("link", { name: "m-2" })).toHaveAttribute("href", history.current());

    act(() => history.back());
    expect(screen.getByTestId("turn")).toHaveTextContent("m-1");
    act(() => history.back());
    expect(screen.getByTestId("turn")).toHaveTextContent("none");
    act(() => history.forward());
    act(() => history.forward());
    expect(screen.getByTestId("turn")).toHaveTextContent("m-2");
  });
});
