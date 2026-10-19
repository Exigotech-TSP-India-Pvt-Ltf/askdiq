import { describe, expect, it } from "vitest";
import { parseAlerts } from "@/lib/alerts-api";
import { alertNotification } from "@/lib/use-shell-notifications";

const NOW = Date.parse("2026-10-19T12:00:00Z");

describe("alerts in the notification bell", () => {
  it("keeps only well-formed alerts and internal console paths", () => {
    const alerts = parseAlerts({
      items: [
        {
          id: "a1",
          title: "p95 latency 24 s above 20 s",
          summary: "Slow answers",
          severity: "critical",
          last_seen_at: "2026-10-19T11:55:00Z",
          console_path: "/admin/governance?tab=Monitoring",
        },
        { id: "a2", title: "Bad link", severity: "warning", console_path: "https://evil.example.com" },
        { id: "a3", title: "Protocol-relative", severity: "info", console_path: "//evil.example.com" },
        { id: "a4", title: "Unknown severity", severity: "fatal" },
        { title: "No id", severity: "info" },
      ],
    });
    expect(alerts.map((a) => a.id)).toEqual(["a1", "a2", "a3"]);
    expect(alerts[1].consolePath).toBe("/admin/governance");
    expect(alerts[2].consolePath).toBe("/admin/governance");
    expect(parseAlerts(null)).toEqual([]);
    expect(parseAlerts({ items: "nope" })).toEqual([]);
  });

  it("maps severity to tone and adds a relative time", () => {
    const [alert] = parseAlerts({
      items: [
        {
          id: "a1",
          title: "Error rate 12.0% above 10.0%",
          summary: "",
          severity: "critical",
          last_seen_at: "2026-10-19T11:55:00Z",
          console_path: "/admin/governance",
        },
      ],
    });
    const item = alertNotification(alert, NOW);
    expect(item).toMatchObject({
      id: "alert-a1",
      title: "Error rate 12.0% above 10.0%",
      href: "/admin/governance",
      tone: "danger",
    });
    expect(item.description).toBeUndefined();
    expect(item.time).toBeTruthy();
  });
});
