import { describe, expect, it } from "vitest";
import { nextPathOr, safeNextPath } from "@/lib/safe-redirect";

describe("safeNextPath (open-redirect protection, F1.1.1)", () => {
  it.each([
    ["/admin", "/admin"],
    ["/admin/governance?tab=Traces&request_id=r1", "/admin/governance?tab=Traces&request_id=r1"],
    ["/documents#top", "/documents#top"],
    ["/", "/"],
  ])("keeps the same-origin path %s", (raw, expected) => {
    expect(safeNextPath(raw)).toBe(expected);
  });

  it.each([
    "https://evil.example/phish",
    "//evil.example/phish",
    "/\\evil.example",
    "\\\\evil.example",
    "javascript:alert(1)",
    "http:/evil.example",
    "/admin\u0000",
    "/admin\nSet-Cookie: x=1",
    "admin",
    "",
    "/login",
    "/login?next=/admin",
    "/auth/callback?code=x",
    "/api/proxy/auth/logout",
    "/mock-idp/x",
    "/suspended",
    `/${"a".repeat(3000)}`,
  ])("rejects %j", (raw) => {
    expect(safeNextPath(raw)).toBeNull();
  });

  it("keeps lookalike paths that only share a prefix", () => {
    expect(safeNextPath("/loginhelp")).toBe("/loginhelp");
    expect(safeNextPath("/authors")).toBe("/authors");
  });

  it("nextPathOr falls back for unsafe values", () => {
    expect(nextPathOr("https://evil.example", "/chat")).toBe("/chat");
    expect(nextPathOr(null, "/")).toBe("/");
  });
});
