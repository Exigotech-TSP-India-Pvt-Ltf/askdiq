import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import { DateRangePicker } from "@/components/ui/DateRangePicker";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { PageToolbar } from "@/components/admin/PageToolbar";
import {
  MAX_RANGE_DAYS,
  RangeProvider,
  clampCustomRange,
  presetRange,
  rangeFromParams,
  rangeUtcDays,
  toIsoDay,
  useRange,
} from "@/lib/range-context";
import { rangeParams } from "@/lib/dashboard-api";
import { LayoutDashboard } from "lucide-react";

const router = vi.hoisted(() => {
  const state = { url: "/admin", listeners: new Set<() => void>() };
  return {
    state,
    replace: vi.fn((url: string) => {
      state.url = url;
      state.listeners.forEach((listener) => listener());
    }),
  };
});

vi.mock("next/navigation", async () => {
  const { useSyncExternalStore } = await import("react");
  const subscribe = (cb: () => void) => {
    router.state.listeners.add(cb);
    return () => {
      router.state.listeners.delete(cb);
    };
  };
  const useUrl = () => useSyncExternalStore(subscribe, () => router.state.url);
  return {
    useRouter: () => ({ replace: router.replace, push: router.replace }),
    usePathname: () => useUrl().split("?")[0],
    useSearchParams: () => new URLSearchParams(useUrl().split("?")[1] ?? ""),
  };
});

vi.mock("@/lib/audience-context", () => ({
  useAudience: () => ({ audience: "overview", setAudience: vi.fn(), counts: null }),
}));

beforeEach(() => {
  router.state.url = "/admin";
  router.replace.mockClear();
});

function Caption() {
  return <p data-testid="caption">{useRange()?.range.label}</p>;
}

function renderPicker() {
  return render(
    <MotionProvider>
      <RangeProvider>
        <DateRangePicker />
        <Caption />
      </RangeProvider>
    </MotionProvider>,
  );
}

const NOW = new Date(2026, 9, 15, 12, 0, 0);

describe("range parsing and clamping", () => {
  it("defaults to the last 7 days and reads presets", () => {
    expect(rangeFromParams(new URLSearchParams(""), NOW).kind).toBe("7d");
    expect(rangeFromParams(new URLSearchParams("range=90d"), NOW).label).toBe("Last 90 days");
    expect(rangeFromParams(new URLSearchParams("range=bogus"), NOW).kind).toBe("7d");
  });

  it("swaps reversed ends, caps the end at today and limits the span", () => {
    const swapped = clampCustomRange(new Date(2026, 9, 10), new Date(2026, 9, 1), NOW);
    expect([toIsoDay(swapped.from), toIsoDay(swapped.to)]).toEqual(["2026-10-01", "2026-10-10"]);

    const future = clampCustomRange(new Date(2026, 9, 1), new Date(2027, 0, 1), NOW);
    expect(toIsoDay(future.to)).toBe("2026-10-15");

    const huge = clampCustomRange(new Date(2020, 0, 1), new Date(2026, 9, 15), NOW);
    const days = Math.round((huge.to.getTime() - huge.from.getTime()) / 86_400_000) + 1;
    expect(days).toBe(MAX_RANGE_DAYS);
  });

  it("rejects malformed or impossible dates in the URL", () => {
    expect(rangeFromParams(new URLSearchParams("from=2026-02-31&to=2026-03-05"), NOW).kind).toBe("7d");
    expect(rangeFromParams(new URLSearchParams("from=<script>&to=2026-03-05"), NOW).kind).toBe("7d");
    const ok = rangeFromParams(new URLSearchParams("from=2026-10-01&to=2026-10-05"), NOW);
    expect(ok.kind).toBe("custom");
  });
});

describe("DateRangePicker + RangeProvider", () => {
  it("writes presets to the URL and reads them back (round trip)", () => {
    renderPicker();
    expect(screen.getByTestId("caption")).toHaveTextContent("Last 7 days");

    fireEvent.click(screen.getByRole("button", { name: /date range: last 7 days/i }));
    const panel = screen.getByRole("dialog", { name: "Choose date range" });
    fireEvent.click(within(panel).getByRole("button", { name: "Last 30 days" }));
    expect(router.replace).toHaveBeenLastCalledWith("/admin?range=30d", { scroll: false });
    expect(screen.getByTestId("caption")).toHaveTextContent("Last 30 days");

    // Back to the default keeps the URL clean.
    fireEvent.click(screen.getByRole("button", { name: /date range: last 30 days/i }));
    fireEvent.click(screen.getByRole("button", { name: "Last 7 days" }));
    expect(router.replace).toHaveBeenLastCalledWith("/admin", { scroll: false });
  });

  it("applies a custom range as ?from=&to= and clamps a future end to today", () => {
    router.state.url = "/admin?audience=users";
    renderPicker();
    fireEvent.click(screen.getByRole("button", { name: /date range/i }));
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-01-05" } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2099-12-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply range" }));

    const today = toIsoDay(new Date());
    expect(router.replace).toHaveBeenLastCalledWith(`/admin?audience=users&from=2026-01-05&to=${today}`, {
      scroll: false,
    });
    expect(screen.getByRole("button", { name: /date range:.*5 jan 2026/i })).toBeInTheDocument();
  });

  it("reads as a filter dropdown (OR-2): filter icon, the active range as visible text and a chevron, with a dot when not the default", () => {
    renderPicker();
    const trigger = screen.getByRole("button", { name: "Date range: Last 7 days" });
    // Visible label = part of the accessible name (WCAG 2.5.3); it collapses to the icon below sm.
    const label = trigger.querySelector("[data-label]");
    expect(label).toHaveTextContent("Last 7 days");
    expect(label).toHaveClass("hidden", "sm:inline");
    expect(trigger.querySelector("svg.lucide-list-filter")).not.toBeNull();
    expect(trigger.querySelector("svg.lucide-chevron-down")).not.toBeNull();
    expect(trigger).toHaveAttribute("aria-haspopup", "dialog");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    // The tooltip is for phones only (the text is visible from sm up).
    expect(trigger.parentElement?.querySelector("[data-tooltip]")).toHaveClass("sm:hidden");
    expect(trigger.querySelector("[data-indicator]")).toBeNull();

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    // The bubble hides while the menu is open so it never covers the panel.
    expect(trigger.parentElement?.querySelector("[data-tooltip]")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Last 30 days" }));

    const changed = screen.getByRole("button", { name: "Date range: Last 30 days" });
    expect(changed.querySelector("[data-label]")).toHaveTextContent("Last 30 days");
    expect(changed.querySelector("[data-indicator]")).not.toBeNull();
  });

  it("shows a custom range as its dates", () => {
    router.state.url = "/admin?from=2026-10-12&to=2026-10-19";
    renderPicker();
    const trigger = screen.getByRole("button", { name: /date range: 12 oct 2026 – 19 oct 2026/i });
    expect(trigger.querySelector("[data-label]")).toHaveTextContent("12 Oct 2026 – 19 Oct 2026");
  });

  it("restores a shared custom URL and closes on Esc", () => {
    router.state.url = "/admin?from=2026-10-01&to=2026-10-05";
    renderPicker();
    const trigger = screen.getByRole("button", { name: /date range: 1 oct 2026 – 5 oct 2026/i });
    trigger.focus();
    fireEvent.click(trigger);
    expect(screen.getByLabelText("From")).toHaveValue("2026-10-01");
    act(() => {
      fireEvent.keyDown(document, { key: "Escape" });
    });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
  });

  it("sits last on the tab row (PageToolbar), after the actions and filters, never in the header; hides outside a provider", () => {
    const { unmount } = render(
      <RangeProvider>
        <DashboardHeader title="Dashboard" icon={LayoutDashboard} />
        <PageToolbar
          tabs={<div role="tablist" aria-label="Sections" />}
          actions={<button type="button">Page action</button>}
          filters={<span data-testid="chips" />}
          dateRange
        />
      </RangeProvider>,
    );
    const header = screen.getByRole("banner");
    const picker = screen.getByRole("button", { name: /date range/i });
    expect(header).not.toContainElement(picker);
    const rangeAnchor = picker.closest('[data-tour="shell-date-range"]');
    const cluster = picker.closest("[data-toolbar-actions]");
    expect(cluster).not.toBeNull();
    expect(cluster?.lastElementChild).toBe(rangeAnchor);
    const order = Array.from(cluster!.children);
    expect(order.indexOf(screen.getByRole("button", { name: "Page action" }))).toBeLessThan(order.indexOf(screen.getByTestId("chips")));
    expect(order.indexOf(screen.getByTestId("chips"))).toBeLessThan(order.indexOf(rangeAnchor!));
    unmount();

    render(<PageToolbar dateRange />);
    expect(screen.queryByRole("button", { name: /date range/i })).not.toBeInTheDocument();
  });
});

describe("one day convention: UTC days end to end", () => {
  const now = new Date("2026-10-16T01:30:00Z");

  it("presets become exactly N UTC days ending today for day-based endpoints", () => {
    expect(rangeUtcDays(presetRange("7d", now))).toEqual({ from: "2026-10-10", to: "2026-10-16" });
    expect(rangeUtcDays(presetRange("30d", now))).toEqual({ from: "2026-09-17", to: "2026-10-16" });
    // The last 24 hours touch yesterday and today (UTC).
    expect(rangeUtcDays(presetRange("24h", now))).toEqual({ from: "2026-10-15", to: "2026-10-16" });
  });

  it("custom ranges send the picked dates, and the Dashboard their UTC midnights", () => {
    const custom = clampCustomRange(new Date(2026, 9, 1), new Date(2026, 9, 3), now);
    expect(rangeUtcDays(custom)).toEqual({ from: "2026-10-01", to: "2026-10-03" });
    expect(rangeParams(custom, "overview")).toEqual({
      audience: "overview",
      from: "2026-10-01T00:00:00.000Z",
      to: "2026-10-04T00:00:00.000Z",
    });
  });
});
