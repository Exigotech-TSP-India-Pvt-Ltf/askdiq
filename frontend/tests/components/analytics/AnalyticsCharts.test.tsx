import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { chartTheme, contrastRatio, mixHex, readableOn, semanticTokens, type ThemeName } from "@/lib/theme-tokens";
import { arcPath } from "@/components/admin/analytics/arc";
import { DropoffChart } from "@/components/admin/analytics/DropoffChart";
import { FlowSankey } from "@/components/admin/analytics/FlowSankey";
import { HourOfDayChart } from "@/components/admin/analytics/HourOfDayChart";
import { KpiStrip } from "@/components/admin/analytics/KpiStrip";
import { PendingState } from "@/components/admin/analytics/PendingState";
import { PeopleBubble, packCircles } from "@/components/admin/analytics/PeopleBubble";
import { QualityDonut, orderQuality } from "@/components/admin/analytics/QualityDonut";
import { TrendChart, monotonePath } from "@/components/admin/analytics/TrendChart";
import { insightsQuery } from "@/lib/insights-api";
import { RECORDED } from "../../journeys/analytics-harness";

const THEMES: ThemeName[] = ["light", "dark"];

afterEach(() => {
  cleanup();
  document.documentElement.classList.remove("dark");
});

async function setTheme(theme: ThemeName) {
  await act(async () => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    await Promise.resolve();
  });
}

describe("geometry helpers", () => {
  it("monotonePath passes through every point and never overshoots a flat run", () => {
    const d = monotonePath([
      [0, 10],
      [10, 10],
      [20, 0],
    ]);
    expect(d.startsWith("M0,10")).toBe(true);
    expect(d).toContain(" 10,10C");
    expect(d.endsWith(" 20,0")).toBe(true);
    expect(monotonePath([])).toBe("");
  });

  it("arcPath draws sectors and full rings", () => {
    expect(arcPath(5, 10, 0, Math.PI / 2)).toMatch(/^M0,-10A10,10 0 0 1 10,/);
    expect(arcPath(5, 10, 0, 2 * Math.PI)).toContain("Z");
    expect(arcPath(5, 10, 1, 1)).toBe("");
  });

  it("packCircles never overlaps and keeps area proportional to the value", () => {
    const values = RECORDED.people.items.map((p) => p.conversations);
    const packed = packCircles(values);
    expect(packed).toHaveLength(values.length);
    for (let i = 0; i < packed.length; i++) {
      expect(packed[i].r).toBeCloseTo(Math.sqrt(values[i]));
      for (let j = i + 1; j < packed.length; j++) {
        const gap = Math.hypot(packed[i].x - packed[j].x, packed[i].y - packed[j].y) - packed[i].r - packed[j].r;
        expect(gap).toBeGreaterThanOrEqual(0);
      }
    }
  });

  it("orderQuality keeps the donut() order and puts unknown codes last", () => {
    const items = [
      { quality: "ZZZ", label: "New", color: null, questions: 1, pct: 1 },
      { quality: "OFF", label: "Off-target", color: null, questions: 2, pct: 2 },
      { quality: "OK", label: "Answered correctly", color: null, questions: 3, pct: 3 },
      { quality: "PART", label: "Partly answered", color: null, questions: 0, pct: 0 },
    ];
    expect(orderQuality(items).map((i) => i.quality)).toEqual(["OK", "OFF", "ZZZ"]);
  });

  it("insightsQuery sends from / to only with a custom period, in a stable order", () => {
    expect(insightsQuery({ period: "all", from: "2026-01-01", to: "2026-01-02", intent: "SVC", tz: "UTC" })).toBe(
      "period=all&intent=SVC&tz=UTC",
    );
    expect(insightsQuery({ period: "custom", from: "2026-01-01", to: "2026-01-02" }, { limit: 25 })).toBe(
      "period=custom&from=2026-01-01&to=2026-01-02&limit=25",
    );
  });
});

describe("analytics colour tokens (light and dark)", () => {
  it.each(THEMES)("outcome, quality and handled-scale ends are at least 3:1 on cards (%s)", (theme) => {
    const card = semanticTokens[theme]["surface-card"];
    const c = chartTheme[theme].insights;
    const marks = [...Object.values(c.outcome), ...Object.values(c.quality), c.handled.low, c.handled.high, c.hours.high];
    for (const colour of marks) expect(contrastRatio(colour, card), `${colour} on ${card}`).toBeGreaterThanOrEqual(3);
  });

  it.each(THEMES)("readableOn picks the higher-contrast text colour (%s)", (theme) => {
    const { fg } = semanticTokens[theme];
    const inverse = semanticTokens[theme]["fg-inverse"];
    for (const fill of Object.values(chartTheme[theme].insights.quality)) {
      const text = readableOn(fill, theme);
      const other = text === fg ? inverse : fg;
      expect(contrastRatio(text, fill)).toBeGreaterThanOrEqual(contrastRatio(other, fill));
    }
  });

  it("mixHex blends between the handled-scale ends", () => {
    const { low, high } = chartTheme.light.insights.handled;
    expect(mixHex(low, high, 0)).toBe(low.toUpperCase());
    expect(mixHex(low, high, 1)).toBe(high.toUpperCase());
    expect(mixHex(low, high, 2)).toBe(high.toUpperCase());
  });

  it("charts switch every mark colour when the theme flips (no reload)", async () => {
    const flow = RECORDED.flow;
    const { container } = render(
      <>
        <FlowSankey flow={flow} />
        <QualityDonut items={RECORDED.quality.items} />
        <DropoffChart items={RECORDED.dropoff.items} />
      </>,
    );
    const fills = (name: RegExp) =>
      Array.from(container.querySelectorAll<SVGElement>("[aria-label]"))
        .filter((el) => name.test(el.getAttribute("aria-label") ?? ""))
        .map((el) => el.getAttribute("fill") ?? el.querySelector("[fill]")?.getAttribute("fill"));

    for (const theme of THEMES) {
      await setTheme(theme);
      const ins = chartTheme[theme].insights;
      expect(new Set(fills(/to Served:/))).toEqual(new Set([ins.outcome.Served]));
      expect(new Set(fills(/to Not served:/))).toEqual(new Set([ins.outcome["Not served"]]));
      expect(fills(/^Partly answered:/)).toEqual([ins.quality.PART]);
      expect(fills(/^Answered correctly:/)).toEqual([ins.quality.OK]);
      const lollipop = container.querySelector('[aria-label="No live job openings: 13 conversations."] circle');
      expect(lollipop?.getAttribute("fill")).toBe(chartTheme[theme].status.danger);
    }
  });

  it("hour ramp and empty hours follow the theme", async () => {
    const hours = Array.from({ length: 24 }, (_, h) => (h === 12 ? 4 : 0));
    const { container } = render(<HourOfDayChart hours={hours} />);
    for (const theme of THEMES) {
      await setTheme(theme);
      const ins = chartTheme[theme].insights;
      const peak = container.querySelector('[aria-label="12:00, 4 questions"]');
      const empty = container.querySelector('[aria-label="3:00, 0 questions"]');
      expect(peak?.getAttribute("fill")).toBe(ins.hours.high.toUpperCase());
      expect(empty?.getAttribute("fill")).toBe(chartTheme[theme].track);
    }
  });
});

describe("chart interaction", () => {
  it("donut slice: click and Enter call onSelect; arrows move focus", () => {
    const onSelect = vi.fn();
    render(<QualityDonut items={RECORDED.quality.items} onSelect={onSelect} />);
    const ok = screen.getByRole("button", { name: "Answered correctly: 32 questions." });
    fireEvent.click(ok);
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ quality: "OK" }));
    ok.focus();
    fireEvent.keyDown(ok, { key: "ArrowRight" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Correct refusal: 8 questions." }));
    fireEvent.keyDown(document.activeElement as Element, { key: "Enter" });
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ quality: "CORR" }));
  });

  it("people bubble selects by person_id and marks the selection", () => {
    const onSelect = vi.fn();
    const [first] = RECORDED.people.items;
    render(<PeopleBubble items={RECORDED.people.items} selected={first.person_id} onSelect={onSelect} />);
    const bubble = screen.getByRole("button", { name: new RegExp(`^${first.person}: `) });
    expect(bubble).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(bubble);
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ person_id: first.person_id }));
  });

  it("flow band toggles user type and outcome", () => {
    const onSelect = vi.fn();
    render(<FlowSankey flow={RECORDED.flow} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Career seeker to Not served: 9 conversations." }));
    expect(onSelect).toHaveBeenCalledWith({ user_type: "Career seeker", outcome: "Not served", conversations: 9 });
  });

  it("trend marks the selected day", () => {
    render(<TrendChart days={RECORDED.trend.days.slice(0, 3)} selectedDay={RECORDED.trend.days[1].day} onSelectDay={() => undefined} />);
    const pressed = screen.getAllByRole("button").filter((b) => b.getAttribute("aria-pressed") === "true");
    expect(pressed).toHaveLength(1);
  });

  it("empty data shows the HTML's empty messages", () => {
    render(
      <>
        <QualityDonut items={[]} />
        <DropoffChart items={[]} />
        <PeopleBubble items={[]} />
        <HourOfDayChart hours={new Array(24).fill(0)} />
      </>,
    );
    expect(screen.getAllByText("No questions in this selection.")).toHaveLength(2);
    expect(screen.getByText("No dropped conversations in this selection.")).toBeInTheDocument();
    expect(screen.getByText("No people in this selection.")).toBeInTheDocument();
  });
});

describe("KPI strip and pending state", () => {
  it("shows n/a when a rate has no denominator", () => {
    render(
      <KpiStrip
        summary={{ ...RECORDED.summary, handled_pct: null, served_pct: null, avg_questions_per_conversation: null }}
      />,
    );
    expect(within(screen.getByTestId("kpi-handled")).getByText("n/a")).toBeInTheDocument();
    expect(within(screen.getByTestId("kpi-served")).getByText("n/a")).toBeInTheDocument();
    expect(within(screen.getByTestId("kpi-avg")).getByText("n/a")).toBeInTheDocument();
  });

  it("renders skeletons while loading", () => {
    render(<KpiStrip summary={undefined} loading />);
    expect(screen.getByTestId("kpi-conversations")).toHaveAttribute("aria-busy", "true");
  });

  it("pending banner computes progress when the API sends none", () => {
    render(<PendingState pending={{ pending: 1, labelled: 3, total: 4, progress_pct: null }} variant="banner" />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "75");
  });
});
