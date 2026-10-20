import { describe, expect, it } from "vitest";
import {
  audiencePageQueries,
  conversationListQuery,
  conversationQuery,
  conversationStatsQuery,
  defaultConversationList,
  defaultVisitorList,
  isOtherRange,
  nextSort,
  presetDayRanges,
  visitorListQuery,
  visitorStatsQuery,
  warmAudiencePage,
} from "@/lib/audience-data";
import { prefixesForScopes } from "@/lib/live-updates";
import { serializeKey, type QueryKey } from "@/lib/query-cache";

const RANGE = { from: "2026-10-14", to: "2026-10-20" };

/** Visitors & Conversations data: one key per view for the page, preload and prefetches. */
describe("audience-data", () => {
  it("builds the keys the panels read (default views)", () => {
    expect(serializeKey(visitorListQuery("t", defaultVisitorList("overview", RANGE)).key)).toBe(
      "visitors/list/overview//all/all/last_active_desc/10/0////",
    );
    expect(serializeKey(visitorStatsQuery("t", "users", RANGE).key)).toBe("visitors/stats/users/2026-10-14/2026-10-20");
    expect(serializeKey(conversationListQuery("t", defaultConversationList("overview", RANGE)).key)).toBe(
      "conversations/list/overview///////2026-10-14/2026-10-20/last_activity_desc/10/0",
    );
    expect(serializeKey(conversationStatsQuery("t", { audience: "overview", ip: null, search: "", range: RANGE }).key)).toBe(
      "conversations/stats/overview///2026-10-14/2026-10-20",
    );
    // The preview drawer and the detail page share one entry (`turn` only echoes).
    expect(conversationQuery("t", "c1", 3).key).toEqual(conversationQuery("t", "c1").key);
  });

  it("a heatmap slot adds the range to the visitor list key only", () => {
    const base = defaultVisitorList("overview", RANGE);
    expect(serializeKey(visitorListQuery("t", { ...base, heat: { weekday: 2, hour: 9 } }).key)).toBe(
      "visitors/list/overview//all/all/last_active_desc/10/0/2/9/2026-10-14/2026-10-20",
    );
  });

  it("the console preload warms both tabs' default views", () => {
    const warmed: QueryKey[] = [];
    warmAudiencePage((key) => warmed.push(key), "t", "testing", RANGE);
    expect(warmed.map(serializeKey)).toEqual(audiencePageQueries("t", "testing", RANGE).map((q) => serializeKey(q.key)));
    expect(warmed).toHaveLength(4);
  });

  it("toggles sorts like the table header", () => {
    expect(nextSort("last_active_desc", "sessions")).toBe("sessions_desc");
    expect(nextSort("sessions_desc", "sessions")).toBe("sessions_asc");
    expect(nextSort("sessions_asc", "sessions")).toBe("sessions_desc");
  });

  it("lists every range preset as UTC days", () => {
    const now = new Date("2026-10-20T11:00:00Z");
    expect(presetDayRanges(now)).toEqual([
      { from: "2026-10-19", to: "2026-10-20" },
      { from: "2026-10-14", to: "2026-10-20" },
      { from: "2026-09-21", to: "2026-10-20" },
      { from: "2026-07-23", to: "2026-10-20" },
    ]);
    expect(isOtherRange({ from: "2026-10-14", to: "2026-10-20" }, RANGE)).toBe(false);
    expect(isOtherRange({ from: "2026-09-21", to: "2026-10-20" }, RANGE)).toBe(true);
  });

  it("refreshes conversation views when their snapshots are recomputed", () => {
    expect(prefixesForScopes(["conversations.stats"])).toEqual(["conversations", "conversation"]);
    expect(prefixesForScopes(["conversations.detail"])).toEqual(["conversations", "conversation"]);
  });
});
