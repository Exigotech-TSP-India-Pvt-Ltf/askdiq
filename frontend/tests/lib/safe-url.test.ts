import { describe, expect, it } from "vitest";
import { displayUrl, hostAllowed, normalizeHostEntry, safeHttpsUrl, validateSourceUrl } from "@/lib/safe-url";

describe("safeHttpsUrl (href guard)", () => {
  it("accepts plain https URLs only", () => {
    expect(safeHttpsUrl("https://docs.deployiq.com.au/guide#setup")).toBe("https://docs.deployiq.com.au/guide#setup");
    for (const bad of [
      "javascript:alert(1)",
      "JAVASCRIPT:alert(1)",
      "data:text/html,<script>alert(1)</script>",
      "http://docs.example.com/",
      "//evil.example.com/x",
      "https://user:pass@docs.example.com/",
      "https://docs.example.com/a b",
      "https://docs.example.com/\u0000",
      " https://docs.example.com/",
      `https://docs.example.com/${"a".repeat(2050)}`,
      "vbscript:msgbox(1)",
      "file:///etc/passwd",
      "",
      null,
      undefined,
      42,
    ]) {
      expect(safeHttpsUrl(bad)).toBeNull();
    }
  });

  it("is fast on a 100 KB adversarial input", () => {
    const input = `https://${"[".repeat(50_000)}${"a ".repeat(25_000)}`;
    const start = performance.now();
    expect(safeHttpsUrl(input)).toBeNull();
    expect(validateSourceUrl(input).ok).toBe(false);
    expect(normalizeHostEntry(input).ok).toBe(false);
    expect(performance.now() - start).toBeLessThan(200);
  });
});

describe("validateSourceUrl (mirror of the server validator)", () => {
  it("empty is fine unless required", () => {
    expect(validateSourceUrl("   ")).toEqual({ ok: true, url: null });
    expect(validateSourceUrl("", { required: true })).toEqual({ ok: false, error: "A source URL is required." });
  });

  it("rejects non-https, protocol-relative, userinfo and whitespace with a clear message", () => {
    expect(validateSourceUrl("http://x.example.com")).toMatchObject({ ok: false, error: "Only https:// links are allowed." });
    expect(validateSourceUrl("javascript:alert(1)")).toMatchObject({ ok: false, error: "Only https:// links are allowed." });
    expect(validateSourceUrl("//x.example.com")).toMatchObject({ ok: false });
    expect(validateSourceUrl("https://a:b@x.example.com")).toMatchObject({ ok: false, error: expect.stringMatching(/user name or password/) });
    expect(validateSourceUrl("https://x.example.com/a b")).toMatchObject({ ok: false, error: expect.stringMatching(/spaces/) });
  });

  it("checks the allow-list: exact host or single leading wildcard; empty list = any https host", () => {
    const hosts = ["docs.deployiq.com.au", "*.example.com"];
    expect(validateSourceUrl("https://docs.deployiq.com.au/x", { allowedHosts: hosts })).toEqual({ ok: true, url: "https://docs.deployiq.com.au/x" });
    expect(validateSourceUrl("https://help.example.com/x", { allowedHosts: hosts }).ok).toBe(true);
    expect(validateSourceUrl("https://deployiq.com.au/x", { allowedHosts: hosts })).toMatchObject({
      ok: false,
      error: expect.stringMatching(/deployiq\.com\.au is not on the allowed domain list/),
    });
    expect(validateSourceUrl("https://anything.org", { allowedHosts: [] }).ok).toBe(true);
    expect(hostAllowed("example.com", ["*.example.com"])).toBe(false);
    expect(hostAllowed("evil-example.com", ["*.example.com"])).toBe(false);
  });
});

describe("normalizeHostEntry (Settings allow-list)", () => {
  it("lower-cases, punycodes and allows one leading wildcard", () => {
    expect(normalizeHostEntry(" Docs.Example.COM ")).toEqual({ ok: true, host: "docs.example.com" });
    expect(normalizeHostEntry("*.example.com")).toEqual({ ok: true, host: "*.example.com" });
    expect(normalizeHostEntry("bücher.example")).toEqual({ ok: true, host: "xn--bcher-kva.example" });
  });

  it("rejects schemes, paths, ports, inner wildcards, bad labels and long values", () => {
    for (const bad of ["https://x.com", "x.com/path", "x.com:443", "a.*.example.com", "*.*.example.com", "-bad.example.com", "localhost", "a b.com"]) {
      expect(normalizeHostEntry(bad).ok).toBe(false);
    }
    expect(normalizeHostEntry(`${"a".repeat(250)}.com`)).toMatchObject({ ok: false, error: expect.stringMatching(/at most 253/) });
  });
});

describe("displayUrl", () => {
  it("drops the scheme and a bare trailing slash", () => {
    expect(displayUrl("https://docs.example.com/")).toBe("docs.example.com");
    expect(displayUrl("https://docs.example.com/a/b?c=1")).toBe("docs.example.com/a/b?c=1");
  });
});
