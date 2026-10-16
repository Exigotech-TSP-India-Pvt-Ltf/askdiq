import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { RelativeTime } from "@/components/ui/RelativeTime";
import { formatAbsoluteTime } from "@/lib/time";

const NOW = new Date("2026-10-16T12:00:00Z");

describe("RelativeTime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it("renders a semantic <time> with the UTC instant, relative text and a local tooltip", () => {
    render(<RelativeTime value="2026-10-16T09:00:00" className="text-xs" />);
    const el = screen.getByText("3h ago");
    expect(el.tagName).toBe("TIME");
    expect(el).toHaveAttribute("datetime", "2026-10-16T09:00:00.000Z");
    expect(el).toHaveAttribute("title", formatAbsoluteTime(new Date("2026-10-16T09:00:00Z")));
    expect(el).toHaveClass("text-xs", "whitespace-nowrap");
  });

  it("absolute display swaps the visible text and the tooltip", () => {
    render(<RelativeTime value="2026-10-14T12:00:00Z" display="absolute" />);
    const el = screen.getByTitle("2d ago");
    expect(el.textContent).toBe(formatAbsoluteTime(new Date("2026-10-14T12:00:00Z")));
  });

  it("prefixes the visible text", () => {
    render(<RelativeTime value="2026-10-16T11:55:00Z" prefix="last seen " />);
    expect(screen.getByText("last seen 5m ago").tagName).toBe("TIME");
  });

  it.each([null, undefined, "", "garbage"])("shows the fallback for %s", (value) => {
    const { container } = render(<RelativeTime value={value} fallback="Never" />);
    expect(screen.getByText("Never")).toBeInTheDocument();
    expect(container.querySelector("time")).toBeNull();
  });
});
