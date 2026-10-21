import { describe, expect, it } from "vitest";
import { formatLastActive, sessionGroup } from "@/lib/session-recency";

// Local-time fixtures, so the assertions hold in any timezone.
const now = new Date(2026, 9, 21, 15, 30).getTime(); // Wed 21 Oct 2026, 15:30
const at = (d: number, h: number, min = 0, y = 2026, mo = 9) => new Date(y, mo, d, h, min);

describe("session recency", () => {
  it("labels the last activity", () => {
    expect(formatLastActive(new Date(now - 20_000), now)).toBe("just now");
    expect(formatLastActive(new Date(now - 5 * 60_000), now)).toBe("5m ago");
    expect(formatLastActive(at(21, 13, 10), now)).toBe("2h ago");
    expect(formatLastActive(at(20, 14, 5), now)).toBe("Yesterday 14:05");
    expect(formatLastActive(at(17, 9, 30), now)).toBe("Sat 09:30");
    expect(formatLastActive(at(12, 9), now)).toBe("12 Oct");
    expect(formatLastActive(at(3, 9, 0, 2025, 11), now)).toBe("3 Dec 2025");
  });

  it("groups by local calendar day", () => {
    expect(sessionGroup(at(21, 0, 5), now)).toBe("Today");
    expect(sessionGroup(at(20, 23, 59), now)).toBe("Yesterday");
    expect(sessionGroup(at(15, 12), now)).toBe("Previous 7 days");
    expect(sessionGroup(at(14, 12), now)).toBe("Older");
  });

  it("never says expired", () => {
    for (let days = 0; days < 400; days += 7) {
      expect(formatLastActive(new Date(now - days * 86_400_000), now)).not.toMatch(/expired/i);
    }
  });
});
