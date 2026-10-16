import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { LOADER_MESSAGES, MascotLoader } from "@/components/ui/MascotLoader";
import { loaderShowDelayMs, loaderStatusRotateMs } from "@/lib/motion";

function mockReducedMotion(reduce: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: reduce && query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

const renderLoader = (props: React.ComponentProps<typeof MascotLoader> = {}) =>
  render(
    <MotionProvider>
      <MascotLoader {...props} />
    </MotionProvider>,
  );

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("MascotLoader", () => {
  it("announces a polite status at once but shows the mascot only after the delay", () => {
    vi.useFakeTimers();
    mockReducedMotion(false);
    renderLoader({ label: "Loading conversation" });

    const status = screen.getByRole("status", { name: "Loading conversation" });
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(screen.queryByTestId("mascot-loader-visual")).toBeNull();

    act(() => vi.advanceTimersByTime(loaderShowDelayMs - 10));
    expect(screen.queryByTestId("mascot-loader-visual")).toBeNull();

    act(() => vi.advanceTimersByTime(20));
    expect(screen.getByTestId("mascot-loader-visual")).toBeInTheDocument();
    expect(status).toHaveTextContent(LOADER_MESSAGES[0]);
    expect(screen.getByTestId("mascot-loader-mascot")).toHaveAttribute("data-motion", "animated");
    expect(screen.getByTestId("mascot-loader-mascot").querySelector("img")).toHaveAttribute("src", expect.stringContaining("askdiq-mascot-256.webp"));
  });

  it("rotates the friendly status text", async () => {
    vi.useFakeTimers();
    mockReducedMotion(true);
    renderLoader({ delayMs: 0 });
    expect(screen.getByRole("status")).toHaveTextContent(LOADER_MESSAGES[0]);
    act(() => vi.advanceTimersByTime(loaderStatusRotateMs));
    vi.useRealTimers();
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(LOADER_MESSAGES[1]));
  });

  it("renders a static mascot under reduced motion", () => {
    mockReducedMotion(true);
    renderLoader({ delayMs: 0, variant: "inline" });
    expect(screen.getByTestId("mascot-loader")).toHaveAttribute("data-variant", "inline");
    expect(screen.getByTestId("mascot-loader-mascot")).toHaveAttribute("data-motion", "static");
  });

  it("never shows anything when loading ends before the delay", () => {
    vi.useFakeTimers();
    mockReducedMotion(false);
    const { rerender } = renderLoader();
    act(() => vi.advanceTimersByTime(loaderShowDelayMs / 2));
    rerender(
      <MotionProvider>
        <MascotLoader show={false} />
      </MotionProvider>,
    );
    act(() => vi.advanceTimersByTime(loaderShowDelayMs * 2));
    expect(screen.queryByTestId("mascot-loader-visual")).toBeNull();
  });

  it("disappears once loading ends", async () => {
    mockReducedMotion(false);
    const { rerender } = renderLoader({ delayMs: 0 });
    expect(screen.getByTestId("mascot-loader-visual")).toBeInTheDocument();
    rerender(
      <MotionProvider>
        <MascotLoader delayMs={0} show={false} />
      </MotionProvider>,
    );
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  });
});
