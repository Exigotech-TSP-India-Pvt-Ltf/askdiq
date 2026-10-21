import React, { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Activity } from "lucide-react";
import { MotionProvider } from "@/components/providers/MotionProvider";
import {
  StudioCard,
  StudioGrid,
  StudioMetricTile,
  StudioSectionHeader,
  StudioSegmented,
  studioCardClass,
} from "@/components/ui/StudioCard";
import { Collapse } from "@/components/ui/Collapse";
import { ChartCard } from "@/components/charts/ChartCard";
import { KpiTrendCard } from "@/components/charts/KpiTrendCard";
import { TableCard } from "@/components/admin/TableCard";
import { DataTable } from "@/components/admin/DataTable";
import { StatCard } from "@/components/admin/insights/StatCard";
import { SkeletonCard } from "@/components/ui/Skeleton";
import {
  MAX_ANIMATED_ROWS,
  cardEnter,
  duration,
  expand,
  popover,
  reduceVariants,
  stagger,
  staggerFor,
} from "@/lib/motion";

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

afterEach(() => vi.unstubAllGlobals());

/** The studio surface: theme tokens that switch per theme, plus explicit dark pairs. */
const STUDIO_LIGHT = ["bg-studio", "shadow-studio", "border-line-brand", "bg-surface-card", "rounded-2xl"];
const STUDIO_DARK = ["dark:border-brand-500/25"];

function expectStudio(el: Element | null) {
  expect(el).not.toBeNull();
  for (const c of [...STUDIO_LIGHT, ...STUDIO_DARK]) expect(el).toHaveClass(c);
}

describe("StudioCard", () => {
  it("renders the studio header: icon badge, title, subtitle, mono pill, aside and footer", () => {
    render(
      <MotionProvider>
        <StudioCard
          title="Economic Waterfall"
          subtitle="Live capital outflow"
          icon={Activity}
          pill="Live accounting"
          aside={<span>$1.56</span>}
          footer={<span>100% real accounting</span>}
        >
          body
        </StudioCard>
      </MotionProvider>,
    );
    const card = screen.getByTestId("studio-card");
    expectStudio(card);
    expect(screen.getByRole("heading", { name: "Economic Waterfall" })).toHaveClass("text-fg");
    expect(card).toHaveAccessibleName("Economic Waterfall");
    expect(screen.getByTestId("studio-icon-badge")).toHaveClass("bg-brand-50", "dark:bg-brand-500/15");
    expect(screen.getByText("Live accounting")).toHaveClass("font-mono", "uppercase");
    expect(screen.getByText("$1.56")).toBeInTheDocument();
    expect(screen.getByText("100% real accounting")).toBeInTheDocument();
  });

  it("interactive cards lift and glow on hover only when motion is allowed; active cards get the ring", () => {
    const cls = studioCardClass({ interactive: true, active: true });
    expect(cls).toContain("hover:shadow-studio-hover");
    expect(cls).toContain("motion-safe:hover:-translate-y-0.5");
    expect(cls).toContain("motion-reduce:transition-none");
    expect(cls).toContain("ring-2");
    expect(cls).toContain("dark:ring-brand-400/60");
    expect(studioCardClass()).not.toContain("hover:shadow-studio-hover");
  });

  it("StudioSectionHeader renders a level-2 heading", () => {
    render(<StudioSectionHeader title="Telemetry" subtitle="Live" icon={Activity} />);
    expect(screen.getByRole("heading", { level: 2, name: "Telemetry" })).toBeInTheDocument();
  });
});

describe("StudioGrid entrance and reduced motion", () => {
  it("cards end with no transform when the user prefers reduced motion", async () => {
    mockReducedMotion(true);
    render(
      <MotionProvider>
        <StudioGrid>
          <StudioCard title="A">a</StudioCard>
          <StudioCard title="B">b</StudioCard>
        </StudioGrid>
      </MotionProvider>,
    );
    const cards = screen.getAllByTestId("studio-card");
    expect(cards).toHaveLength(2);
    await waitFor(() => {
      for (const c of cards) expect(["", "none"]).toContain(c.style.transform);
    });
  });

  it("long lists skip the stagger (no per-row animation above the limit)", () => {
    expect(staggerFor(3)).toBe(stagger);
    expect(staggerFor(MAX_ANIMATED_ROWS)).toBe(stagger);
    expect(staggerFor(MAX_ANIMATED_ROWS + 1)).toEqual({});
  });
});

describe("new motion presets", () => {
  it("popover, expand and cardEnter stay at or under 300 ms and reduce to opacity", () => {
    const d = (v: unknown) => (v as { transition?: { duration?: number } }).transition?.duration ?? 0;
    for (const preset of [popover, expand, cardEnter]) {
      expect(d(preset.visible)).toBeLessThanOrEqual(0.3);
      expect(d(preset.exit)).toBeLessThanOrEqual(duration.base);
    }
    expect(reduceVariants(popover).hidden).toEqual({ opacity: 0 });
    expect(reduceVariants(cardEnter).hidden).toEqual({ opacity: 0 });
  });
});

describe("StudioSegmented", () => {
  function Harness() {
    const [v, setV] = useState<"vol" | "pct">("vol");
    return (
      <StudioSegmented
        ariaLabel="Token view"
        value={v}
        onChange={setV}
        options={[
          { id: "vol", label: "Vol" },
          { id: "pct", label: "%", ariaLabel: "Percent" },
        ]}
      />
    );
  }

  it("is a labelled group with one pressed option and a sliding pill", () => {
    render(
      <MotionProvider>
        <Harness />
      </MotionProvider>,
    );
    expect(screen.getByRole("group", { name: "Token view" })).toHaveClass("dark:bg-surface-studio");
    const vol = screen.getByRole("button", { name: "Vol" });
    const pct = screen.getByRole("button", { name: "Percent" });
    expect(vol).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByTestId("studio-segment-pill")).toHaveLength(1);
    fireEvent.click(pct);
    expect(pct).toHaveAttribute("aria-pressed", "true");
    expect(vol).toHaveAttribute("aria-pressed", "false");
    expect(screen.getAllByTestId("studio-segment-pill")).toHaveLength(1);
  });
});

describe("StudioMetricTile count-up", () => {
  it("always exposes the final value to assistive tech; reduced motion shows it at once", () => {
    mockReducedMotion(true);
    render(
      <MotionProvider>
        <StudioMetricTile label="Turns" value={3901} unit="total" />
      </MotionProvider>,
    );
    const tile = screen.getByTestId("studio-metric-tile");
    expect(tile).toHaveClass("bg-surface-studio/60", "dark:bg-white/[0.03]");
    const shown = tile.querySelector('[aria-hidden="true"]');
    expect(shown).toHaveTextContent("3,901");
    expect(screen.getByText("3,901", { selector: ".sr-only" })).toBeInTheDocument();
  });

  it("clickable tiles are toggle buttons", () => {
    const onClick = vi.fn();
    render(<StudioMetricTile label="Blocked" value="0" onClick={onClick} selected />);
    const btn = screen.getByRole("button", { name: /Blocked/ });
    expect(btn).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe("Collapse", () => {
  it("reduced motion: content appears and disappears instantly", () => {
    mockReducedMotion(true);
    const { rerender } = render(
      <MotionProvider>
        <Collapse open={false}>detail</Collapse>
      </MotionProvider>,
    );
    expect(screen.queryByText("detail")).toBeNull();
    rerender(
      <MotionProvider>
        <Collapse open>detail</Collapse>
      </MotionProvider>,
    );
    expect(screen.getByTestId("collapse")).not.toHaveClass("overflow-hidden");
    rerender(
      <MotionProvider>
        <Collapse open={false}>detail</Collapse>
      </MotionProvider>,
    );
    expect(screen.queryByText("detail")).toBeNull();
  });

  it("animates height with the expand preset otherwise", () => {
    mockReducedMotion(false);
    render(
      <MotionProvider>
        <Collapse open>detail</Collapse>
      </MotionProvider>,
    );
    expect(screen.getByTestId("collapse")).toHaveClass("overflow-hidden");
    expect(screen.getByText("detail")).toBeInTheDocument();
  });
});

describe("shared components inherit the studio look", () => {
  it("ChartCard (plain and aurora) uses the studio surface and crossfades its body by state", () => {
    const { rerender } = render(
      <MotionProvider>
        <ChartCard title="Cost" loading>
          <div data-testid="chart" />
        </ChartCard>
      </MotionProvider>,
    );
    expectStudio(screen.getByTestId("chart-card"));
    expect(document.querySelector("[data-state]")).toHaveAttribute("data-state", "loading");
    rerender(
      <MotionProvider>
        <ChartCard title="Cost" variant="aurora" icon={Activity} pill="Live">
          <div data-testid="chart" />
        </ChartCard>
      </MotionProvider>,
    );
    expectStudio(screen.getByTestId("chart-card"));
    expect(document.querySelector("[data-state]")).toHaveAttribute("data-state", "chart");
    expect(screen.getByTestId("studio-icon-badge")).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
  });

  it("ChartCard 'View as table' uses the studio table header", () => {
    render(
      <MotionProvider>
        <ChartCard title="Cost" table={{ columns: [{ key: "d", label: "Day" }], rows: [{ d: "Mon" }] }}>
          <div />
        </ChartCard>
      </MotionProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /view as table/i }));
    const thead = screen.getByRole("table").querySelector("thead");
    expect(thead).toHaveClass("sticky", "bg-surface-studio", "dark:border-brand-500/20");
  });

  it("KpiTrendCard, StatCard, SkeletonCard and TableCard sit on the studio surface", () => {
    const { container } = render(
      <MotionProvider>
        <KpiTrendCard label="Queries" value="3,901" trend={[]} />
        <StatCard title="Users" value={23} />
        <SkeletonCard label="Loading" />
        <TableCard title="Logs">
          <table />
        </TableCard>
      </MotionProvider>,
    );
    const surfaces = container.querySelectorAll(".bg-studio");
    expect(surfaces.length).toBe(4);
    surfaces.forEach((s) => expectStudio(s));
    expect(screen.getByTestId("table-card")).toHaveClass("hover:shadow-studio-hover");
  });

  it("DataTable: sticky studio header, zebra + hover rows, token empty and error states", () => {
    const columns = [{ key: "name", header: "Name" }];
    const { rerender } = render(<DataTable columns={columns} data={[{ id: "1", name: "a" }, { id: "2", name: "b" }]} />);
    const thead = screen.getByRole("table").querySelector("thead");
    expect(thead).toHaveClass("sticky", "top-0", "bg-surface-studio");
    const row = screen.getByText("a").closest("tr");
    // Zebra / hover / selected tints are one --row-tint (globals.css) over the themed table surface.
    expect(row).toHaveClass("dt-row");
    expect(row?.closest("[data-scroll-under], .dt-surface")).toHaveClass("dt-surface");
    expect(row?.className).not.toMatch(/slate|dark-surface/);

    rerender(<DataTable columns={columns} data={[]} emptyMessage="Nothing yet" />);
    expect(screen.getByText("Nothing yet")).toHaveClass("text-fg");

    const onRetry = vi.fn();
    rerender(<DataTable columns={columns} data={[]} error="Couldn't load traces." onRetry={onRetry} />);
    expect(screen.getByText("Failed to load table records")).toHaveClass("text-fg");
    fireEvent.click(screen.getByRole("button", { name: "Try Again" }));
    expect(onRetry).toHaveBeenCalled();
  });
});
