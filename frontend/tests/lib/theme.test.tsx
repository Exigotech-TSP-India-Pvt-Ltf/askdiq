import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider, useTheme } from "@/lib/theme-context";
import { THEME_INIT_SCRIPT, THEME_STORAGE_KEY } from "@/lib/theme-script";
import { useChartTheme } from "@/lib/use-chart-theme";
import { chartTheme } from "@/lib/theme-tokens";
import { ThemeToggle } from "@/components/ThemeToggle";

/** matchMedia mock whose "prefers dark" answer can flip at runtime. */
function installMatchMedia(initialDark: boolean) {
  let dark = initialDark;
  const listeners = new Set<(e: MediaQueryListEvent) => void>();
  const mql = {
    get matches() {
      return dark;
    },
    media: "(prefers-color-scheme: dark)",
    onchange: null,
    addEventListener: (_: string, cb: (e: MediaQueryListEvent) => void) => listeners.add(cb),
    removeEventListener: (_: string, cb: (e: MediaQueryListEvent) => void) => listeners.delete(cb),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  };
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => (query.includes("prefers-color-scheme") ? mql : { ...mql, matches: false })),
  );
  return {
    setDark(next: boolean) {
      dark = next;
      listeners.forEach((cb) => cb({ matches: next } as MediaQueryListEvent));
    },
  };
}

function ChartProbe() {
  const ct = useChartTheme();
  const { mode, resolvedTheme, setTheme, toggleTheme } = useTheme();
  return (
    <div>
      <span data-testid="series" data-color={ct.categorical[0]} />
      <span data-testid="grid" data-color={ct.grid} />
      <span data-testid="mode">{mode}</span>
      <span data-testid="resolved">{resolvedTheme}</span>
      <button onClick={toggleTheme}>toggle</button>
      <button onClick={() => setTheme("dark")}>dark</button>
    </div>
  );
}

const html = () => document.documentElement;

beforeEach(() => {
  window.localStorage.clear();
  html().classList.remove("dark");
  html().style.colorScheme = "";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ThemeProvider (F0.3.5, T1/T4)", () => {
  it("toggle flips the dark class and re-renders chart colours through useChartTheme", async () => {
    installMatchMedia(false);
    render(
      <ThemeProvider>
        <ChartProbe />
      </ThemeProvider>,
    );
    expect(html()).not.toHaveClass("dark");
    await waitFor(() => expect(screen.getByTestId("series").dataset.color).toBe(chartTheme.light.categorical[0]));

    fireEvent.click(screen.getByText("toggle"));
    expect(html()).toHaveClass("dark");
    expect(html().style.colorScheme).toBe("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    await waitFor(() => expect(screen.getByTestId("series").dataset.color).toBe(chartTheme.dark.categorical[0]));
    await waitFor(() => expect(screen.getByTestId("grid").dataset.color).toBe(chartTheme.dark.grid));

    // Light -> dark -> light leaves no stuck styles.
    fireEvent.click(screen.getByText("toggle"));
    expect(html()).not.toHaveClass("dark");
    expect(html().style.colorScheme).toBe("light");
    await waitFor(() => expect(screen.getByTestId("series").dataset.color).toBe(chartTheme.light.categorical[0]));
  });

  it("syncs the theme from another tab through the storage event", async () => {
    installMatchMedia(false);
    render(
      <ThemeProvider>
        <ChartProbe />
      </ThemeProvider>,
    );
    act(() => {
      window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
      window.dispatchEvent(new StorageEvent("storage", { key: THEME_STORAGE_KEY, newValue: "dark" }));
    });
    expect(html()).toHaveClass("dark");
    expect(screen.getByTestId("mode")).toHaveTextContent("dark");
    await waitFor(() => expect(screen.getByTestId("series").dataset.color).toBe(chartTheme.dark.categorical[0]));

    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: "unrelated", newValue: "light" }));
    });
    expect(html()).toHaveClass("dark");

    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: THEME_STORAGE_KEY, newValue: "light" }));
    });
    expect(html()).not.toHaveClass("dark");
  });

  it("has no System mode: a missing preference resolves once to the OS setting, is persisted, and never follows the OS afterwards", async () => {
    const media = installMatchMedia(true);
    render(
      <ThemeProvider>
        <ChartProbe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("mode")).toHaveTextContent("dark");
    expect(html()).toHaveClass("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    act(() => media.setDark(false));
    expect(html()).toHaveClass("dark");
    expect(screen.getByTestId("resolved")).toHaveTextContent("dark");
    await waitFor(() => expect(screen.getByTestId("series").dataset.color).toBe(chartTheme.dark.categorical[0]));
  });

  it.each([
    ["system", false, "light"],
    ["system", true, "dark"],
    ["bogus", false, "light"],
  ])("migrates a legacy stored %s value (OS dark=%s) to an explicit %s", (stored, osDark, expected) => {
    installMatchMedia(osDark);
    window.localStorage.setItem(THEME_STORAGE_KEY, stored);
    render(
      <ThemeProvider>
        <ChartProbe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("mode")).toHaveTextContent(expected);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe(expected);
    expect(html().classList.contains("dark")).toBe(expected === "dark");
  });

  it("ignores a cross-tab write of a value that is not light or dark", () => {
    installMatchMedia(false);
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    render(
      <ThemeProvider>
        <ChartProbe />
      </ThemeProvider>,
    );
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: THEME_STORAGE_KEY, newValue: "system" }));
    });
    expect(screen.getByTestId("mode")).toHaveTextContent("dark");
    expect(html()).toHaveClass("dark");
  });

  it("restores a stored preference on mount", () => {
    installMatchMedia(false);
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    render(
      <ThemeProvider>
        <ChartProbe />
      </ThemeProvider>,
    );
    expect(html()).toHaveClass("dark");
    expect(screen.getByTestId("mode")).toHaveTextContent("dark");
  });

  it("ThemeToggle switches only between Light and Dark with an accessible label", () => {
    installMatchMedia(false);
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    );
    const button = screen.getByRole("button");
    expect(button).toHaveAttribute("data-theme-mode", "light");
    expect(button).toHaveAccessibleName(/switch to dark mode/i);
    fireEvent.click(button);
    expect(button).toHaveAttribute("data-theme-mode", "dark");
    expect(html()).toHaveClass("dark");
    expect(button).toHaveAccessibleName(/switch to light mode/i);
    fireEvent.click(button);
    expect(button).toHaveAttribute("data-theme-mode", "light");
    expect(html()).not.toHaveClass("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(button).not.toHaveAccessibleName(/system/i);
  });
});

describe("useChartTheme without a provider", () => {
  it("re-renders when anything flips the dark class on <html>", async () => {
    function Swatch() {
      return <span data-testid="axis" data-color={useChartTheme().axis} />;
    }
    render(<Swatch />);
    expect(screen.getByTestId("axis").dataset.color).toBe(chartTheme.light.axis);
    act(() => html().classList.add("dark"));
    await waitFor(() => expect(screen.getByTestId("axis").dataset.color).toBe(chartTheme.dark.axis));
  });
});

describe("pre-paint theme script (T3)", () => {
  const run = () => new Function(THEME_INIT_SCRIPT)();

  it("applies a stored dark preference before React mounts", () => {
    installMatchMedia(false);
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    run();
    expect(html()).toHaveClass("dark");
    expect(html().style.colorScheme).toBe("dark");
  });

  it("resolves a missing or legacy System preference once to the OS setting and persists it", () => {
    const media = installMatchMedia(true);
    run();
    expect(html()).toHaveClass("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    // Persisted: a later OS change no longer affects it.
    media.setDark(false);
    run();
    expect(html()).toHaveClass("dark");

    window.localStorage.setItem(THEME_STORAGE_KEY, "system");
    run();
    expect(html()).not.toHaveClass("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(html().style.colorScheme).toBe("light");
  });

  it("keeps light when light is stored even if the OS prefers dark", () => {
    installMatchMedia(true);
    html().classList.add("dark");
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    run();
    expect(html()).not.toHaveClass("dark");
  });

  it("never throws when storage is unavailable", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("blocked");
      },
    });
    expect(run).not.toThrow();
    expect(html()).not.toHaveClass("dark");
    expect(html().style.colorScheme).toBe("light");
  });
});
