import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TourProvider, useTour } from "@/components/tour/TourProvider";
import { TourLauncher } from "@/components/tour/TourLauncher";
import type { PageTour, TourId } from "@/lib/tour/types";
import { placeCard } from "@/lib/tour/dom";

const nav = vi.hoisted(() => ({ pathname: "/admin/usage", replace: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ replace: nav.replace, push: nav.push, prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const demo: PageTour = {
  id: "usage",
  title: "Usage",
  steps: [
    { title: "Intro", body: "Welcome.", placement: "center" },
    { target: '[data-tour="t-one"]', title: "First", body: "One.", placement: "bottom" },
    { target: '[data-tour="t-missing"]', title: "Missing", body: "Never shown." },
    { target: '[data-tour="t-two"]', title: "Second", body: "Two.", tab: "queries" },
  ],
};
const tours: Partial<Record<TourId, PageTour>> = { usage: demo };

function Harness() {
  const tour = useTour();
  return (
    <div>
      <button type="button" onClick={() => tour.start("usage")}>
        start
      </button>
      <span data-testid="active">{String(tour.isActive)}</span>
      <div data-tour="t-one">one</div>
      <div data-tour="t-two">two</div>
    </div>
  );
}

function renderTour() {
  return render(
    <TourProvider tours={tours}>
      <Harness />
    </TourProvider>,
  );
}

async function startTour() {
  fireEvent.click(screen.getByRole("button", { name: "start" }));
  return screen.findByRole("dialog");
}

beforeEach(() => {
  // jsdom has no layout: treat every element as rendered.
  vi.spyOn(Element.prototype, "getClientRects").mockImplementation(
    () => [{ top: 0, left: 0, width: 10, height: 10 }] as unknown as DOMRectList,
  );
  window.localStorage.clear();
  window.history.replaceState({}, "", "/admin/usage?range=7d");
  nav.pathname = "/admin/usage";
  nav.replace.mockClear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Guided tour engine", () => {
  it("starts on the intro step with counter and progress", async () => {
    renderTour();
    const dialog = await startTour();
    expect(dialog).toHaveTextContent("Intro");
    expect(dialog).toHaveTextContent("Step 1 of 4");
    expect(screen.getByTestId("active")).toHaveTextContent("true");
    expect(screen.queryByRole("button", { name: /back/i })).not.toBeInTheDocument();
  });

  it("moves next and back with the buttons and arrow keys", async () => {
    renderTour();
    await startTour();
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(await screen.findByText("First")).toBeInTheDocument();
    expect(screen.getByTestId("tour-spotlight")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    expect(await screen.findByText("Intro")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(await screen.findByText("First")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(await screen.findByText("Intro")).toBeInTheDocument();
  });

  it("skips a step whose target never renders and updates ?tab= for tab steps", async () => {
    renderTour();
    await startTour();
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    await screen.findByText("First");
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(await screen.findByText("Second", undefined, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByText("Missing")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toHaveTextContent("Step 4 of 4");
    expect(nav.replace).toHaveBeenCalledWith("/admin/usage?range=7d&tab=queries", { scroll: false });
    // Back from step 4 skips the missing step again.
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    expect(await screen.findByText("First", undefined, { timeout: 3000 })).toBeInTheDocument();
  });

  it("finishes on the last step", async () => {
    renderTour();
    await startTour();
    for (const title of ["First", "Second"]) {
      fireEvent.click(screen.getByRole("button", { name: /next/i }));
      await screen.findByText(title, undefined, { timeout: 3000 });
    }
    fireEvent.click(screen.getByRole("button", { name: /finish/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByTestId("active")).toHaveTextContent("false");
  });

  it("closes on Escape and on Skip", async () => {
    renderTour();
    await startTour();
    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await startTour();
    fireEvent.click(screen.getByRole("button", { name: "Skip tour" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("Tour launcher", () => {
  function renderLauncher() {
    return render(
      <TourProvider>
        <TourLauncher />
      </TourProvider>,
    );
  }

  it("shows an icon-only launcher with page and basics options on a toured route", () => {
    nav.pathname = "/admin/usage";
    renderLauncher();
    const trigger = screen.getByRole("button", { name: "Take the tour" });
    expect(trigger).toHaveAttribute("title", "Take the tour");
    expect(trigger).not.toHaveTextContent(/\S/);
    fireEvent.click(trigger);
    expect(screen.getByRole("menuitem", { name: /dashboard basics/i })).toBeInTheDocument();
  });

  it("starts the dashboard basics tour from the menu", async () => {
    renderLauncher();
    fireEvent.click(screen.getByRole("button", { name: "Take the tour" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /dashboard basics/i }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("Welcome to the AskDIQ dashboard");
  });

  it("is hidden on routes without a tour", () => {
    nav.pathname = "/login";
    renderLauncher();
    expect(screen.queryByRole("button", { name: "Take the tour" })).not.toBeInTheDocument();
  });

  it("renders nothing outside a TourProvider", () => {
    render(<TourLauncher />);
    expect(screen.queryByRole("button", { name: "Take the tour" })).not.toBeInTheDocument();
  });

  it("pulses once on the first visit only", () => {
    const first = renderLauncher();
    expect(screen.getByTestId("tour-hint")).toBeInTheDocument();
    first.unmount();
    renderLauncher();
    expect(screen.queryByTestId("tour-hint")).not.toBeInTheDocument();
  });
});

describe("placeCard", () => {
  it("centers without a target and clamps to the viewport", () => {
    expect(placeCard(null, "bottom", 300, 200, 1000, 800)).toEqual({ top: 300, left: 350 });
    const pos = placeCard({ top: 700, left: 950, width: 40, height: 40 }, "bottom", 300, 200, 1000, 800);
    expect(pos.top + 200).toBeLessThanOrEqual(800 - 16);
    expect(pos.left + 300).toBeLessThanOrEqual(1000 - 16);
  });
});
