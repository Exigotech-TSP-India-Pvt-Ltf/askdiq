/**
 * Link contracts from the click audit (frontend/docs/interaction-audit.md):
 * every href a click produces is parsed back by its target into the same state.
 */
import { describe, expect, it } from "vitest";
import { parseFilters } from "@/lib/feedback-filters";
import { entityHref } from "@/lib/entity-links";
import { tracesHref } from "@/lib/governance-links";
import { evalRunHref } from "@/lib/usage-insights-api";
import { parseUsageDay, parseUsageTab } from "@/lib/usage-tabs";
import { queriesHrefFor } from "@/components/admin/dashboard/DashboardCharts";

const MESSAGE_ID = "3f2a9c1e-8b7d-4c6a-9e5f-1a2b3c4d5e6f";

describe("click audit link contracts", () => {
  it("a feedback deep link finds the message whatever its triage status", () => {
    const href = entityHref({ kind: "feedback", id: MESSAGE_ID });
    const filters = parseFilters(new URL(href, "http://x").searchParams);
    expect(filters.message_id).toBe(MESSAGE_ID);
    expect(filters.status).toBe("all");
    // Without a message link the queue still defaults to untriaged items.
    expect(parseFilters(new URLSearchParams()).status).toBe("new");
  });

  it("a session link lands on the Queries tab filtered to that session", () => {
    const url = new URL(entityHref({ kind: "session", id: "s-9" }), "http://x");
    expect(parseUsageTab(url.searchParams.get("tab"))).toBe("queries");
    expect(url.searchParams.get("session")).toBe("s-9");
  });

  it("a dashboard day link carries a day the Usage page accepts", () => {
    const url = new URL(queriesHrefFor("2026-10-14T00:00:00Z"), "http://x");
    expect(parseUsageTab(url.searchParams.get("tab"))).toBe("queries");
    expect(parseUsageDay(url.searchParams.get("from"))).toBe("2026-10-14");
    expect(parseUsageDay(url.searchParams.get("to"))).toBe("2026-10-14");
    expect(parseUsageDay("2026-13-45x")).toBe("");
    expect(parseUsageDay("not-a-day")).toBe("");
  });

  it("run markers open the live evaluation page that reads ?run=", () => {
    expect(evalRunHref("run 1")).toBe("/admin/evaluation-live?run=run%201");
  });

  it("trace links can filter by outcome", () => {
    expect(tracesHref({ outcome: "ungrounded" })).toBe("/admin/governance?tab=Traces&outcome=ungrounded");
  });
});
