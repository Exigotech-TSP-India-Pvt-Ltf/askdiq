/**
 * Question topics as a ranked list (replaces the treemap): order, full labels,
 * handled-% colour, click-to-filter and "Show all N".
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { chartTheme, mixHex } from "@/lib/theme-tokens";
import { TOPIC_PREVIEW_COUNT, TopicsBreakdown, rankTopics, topicMeta } from "@/components/admin/analytics/TopicsBreakdown";
import type { TopicItem } from "@/lib/insights-api";
import { RECORDED } from "../../journeys/analytics-harness";

const ITEMS = RECORDED.topics.items;

const topic = (intent: string, label: string, questions: number, handledPct: number): TopicItem => ({
  intent,
  label,
  group: null,
  questions,
  handled: Math.round((questions * handledPct) / 100),
  handled_pct: handledPct,
});

const rows = () => within(screen.getByTestId("topics-breakdown")).getAllByRole("listitem");

afterEach(() => {
  cleanup();
  document.documentElement.classList.remove("dark");
});

describe("TopicsBreakdown", () => {
  it("ranks topics by volume, drops empty ones and keeps ties stable", () => {
    const ranked = rankTopics([topic("b", "Beta", 3, 0), topic("z", "Zero", 0, 0), topic("a", "Alpha", 3, 0), topic("c", "Gamma", 9, 0)]);
    expect(ranked.map((t) => t.intent)).toEqual(["c", "a", "b"]);
    expect(topicMeta(topic("x", "X", 52, 87.6))).toBe("52 · 88% handled");
  });

  it("renders rows in volume order with the full topic name and the handled % as text", () => {
    const long = "Platform and services for regulated industries";
    render(<TopicsBreakdown items={[topic("s", "Small", 2, 50), topic("p", long, 52, 87.6), topic("c", "Contact and sales", 10, 0)]} />);
    const list = rows();
    expect(list.map((li) => li.getAttribute("data-intent"))).toEqual(["p", "c", "s"]);
    // Never cut: the whole name is in the DOM, no ellipsis or truncate class.
    const name = within(list[0]).getByText(long);
    expect(name.className).not.toMatch(/truncate|line-clamp|text-ellipsis/);
    expect(list[0]).toHaveTextContent("52 · 88% handled");
    expect(within(list[0]).getByRole("group", { name: `${long}: 52 questions, 88% handled` })).toBeInTheDocument();
  });

  it("sizes bars by volume and colours them on the handled scale", () => {
    render(<TopicsBreakdown items={[topic("a", "All handled", 40, 100), topic("b", "None handled", 10, 0), topic("c", "Half", 20, 50)]} />);
    const bars = screen.getAllByTestId("topic-bar");
    const { low, high } = chartTheme.light.insights.handled;
    expect(bars.map((b) => b.getAttribute("data-color"))).toEqual([high, mixHex(low, high, 0.5), low]);
    expect(bars.map((b) => b.style.width)).toEqual(["100%", "50%", "25%"]);
  });

  it("uses the dark handled scale in dark mode", async () => {
    render(<TopicsBreakdown items={[topic("a", "All handled", 4, 100)]} />);
    await act(async () => {
      document.documentElement.classList.add("dark");
      await Promise.resolve();
    });
    expect(screen.getByTestId("topic-bar").getAttribute("data-color")).toBe(chartTheme.dark.insights.handled.high);
  });

  it("filters on click with the same callback, marks the selection and is keyboard focusable", () => {
    const onSelect = vi.fn();
    const { rerender } = render(<TopicsBreakdown items={ITEMS} onSelect={onSelect} />);
    const services = screen.getByRole("button", { name: "Services: 33 questions, 58% handled" });
    services.focus();
    expect(services).toHaveFocus();
    fireEvent.click(services);
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ intent: "SVC" }));
    rerender(<TopicsBreakdown items={ITEMS} onSelect={onSelect} selected="SVC" />);
    expect(screen.getByRole("button", { name: /^Services:/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^Careers:/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("shows the top 10, then every topic in place with Show all", () => {
    render(<TopicsBreakdown items={ITEMS} onSelect={() => undefined} />);
    expect(rows()).toHaveLength(TOPIC_PREVIEW_COUNT);
    const toggle = screen.getByRole("button", { name: `Show all ${ITEMS.length}` });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(rows()).toHaveLength(ITEMS.length);
    expect(screen.getByRole("button", { name: "Show top 10" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.queryByTestId("treemap-small-topics")).toBeNull();
  });

  it("shows every topic when a filtered topic sits outside the top 10", () => {
    const last = rankTopics(ITEMS).at(-1)!;
    render(<TopicsBreakdown items={ITEMS} onSelect={() => undefined} selected={last.intent} />);
    expect(rows()).toHaveLength(ITEMS.length);
    expect(screen.queryByRole("button", { name: /^Show / })).toBeNull();
  });

  it("has no Show all with 10 topics or fewer", () => {
    render(<TopicsBreakdown items={ITEMS.slice(0, 4)} />);
    expect(rows()).toHaveLength(4);
    expect(screen.queryByRole("button", { name: /Show all/ })).toBeNull();
  });
});
