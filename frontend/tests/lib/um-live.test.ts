import { describe, expect, it } from "vitest";
import { prefixesForScopes } from "@/lib/live-updates";
import { authAuditQuery, umKeys } from "@/lib/user-management-api";

describe("User Management real time and audit paging", () => {
  it("a committed auth change refreshes the UM views and the pending badge", () => {
    expect(prefixesForScopes(["users"])).toEqual(expect.arrayContaining(["um/", "admin/access-requests"]));
    expect(prefixesForScopes(["users.stats"])).toEqual(["um/stats"]);
  });

  it("bounds and allow-lists the auth audit page query", () => {
    const qs = new URLSearchParams(
      authAuditQuery({ limit: 5000, action: "query" as never, search: `  ${"x".repeat(150)} `, cursor: "abc" }),
    );
    expect(qs.get("limit")).toBe("100");
    expect(qs.get("action")).toBeNull();
    expect(qs.get("search")).toHaveLength(100);
    expect(qs.get("cursor")).toBe("abc");
    expect(umKeys.auditPage({ limit: 25 })).toBe("um/audit/page?limit=25");
  });
});
