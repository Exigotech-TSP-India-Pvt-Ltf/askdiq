import { describe, expect, it } from "vitest";
import { formatAbsoluteTime, formatRelativeTime, parseServerDate, toServerDate } from "@/lib/time";

const NOW = Date.parse("2026-10-16T12:00:00Z");
const ago = (ms: number) => new Date(NOW - ms);
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

describe("time helpers", () => {
  it("reads naive server timestamps as UTC", () => {
    expect(parseServerDate("2026-10-16T01:00:00").toISOString()).toBe("2026-10-16T01:00:00.000Z");
    expect(parseServerDate("2026-10-16T11:00:00+10:00").toISOString()).toBe("2026-10-16T01:00:00.000Z");
  });

  it("toServerDate returns null for empty or invalid input", () => {
    expect(toServerDate(null)).toBeNull();
    expect(toServerDate(undefined)).toBeNull();
    expect(toServerDate("")).toBeNull();
    expect(toServerDate("not a date")).toBeNull();
    expect(toServerDate("2026-10-16T01:00:00Z")?.toISOString()).toBe("2026-10-16T01:00:00.000Z");
  });

  it.each([
    [0, "just now"],
    [59_000, "just now"],
    [5 * MIN, "5m ago"],
    [59 * MIN, "59m ago"],
    [3 * HOUR + 20 * MIN, "3h ago"],
    [2 * DAY + HOUR, "2d ago"],
    [29 * DAY, "29d ago"],
    [-2 * HOUR, "in 2h"],
  ])("formats an age of %d ms as %s", (ms, text) => {
    expect(formatRelativeTime(ago(ms), NOW)).toBe(text);
  });

  it("switches to a calendar date past 30 days", () => {
    const text = formatRelativeTime(ago(45 * DAY), NOW);
    expect(text).not.toMatch(/ago/);
    expect(text).toMatch(/2026/);
  });

  it("absolute time carries the date, time and a timezone name", () => {
    const text = formatAbsoluteTime(new Date(NOW));
    expect(text).toMatch(/2026/);
    expect(text).toMatch(/\d{1,2}:\d{2}/);
    expect(text).toMatch(/(GMT|UTC|[A-Z]{2,5})([+-]\d{1,2}(:\d{2})?)?$/);
  });
});
