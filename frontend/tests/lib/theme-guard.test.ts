import { describe, expect, it } from "vitest";
import fs from "fs";
import path from "path";

/**
 * Theme guard. Mirrors the ESLint `no-restricted-syntax`
 * rules in .eslintrc.json (which run in CI via `npm run lint`) as a
 * line-based grep over app/, components/ and lib/, so a violation fails both
 * `npm run lint` and `npm test`.
 */
const ROOT = path.resolve(__dirname, "../..");
const ALLOWED = new Set([path.join(ROOT, "lib", "theme-tokens.ts")]);

const HEX = /(^|[^&\w])#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})(?![\w-])/;
const BG_WHITE = /(^|[\s"'`])bg-white(?=[\s"'`]|$)/;
const TEXT_GRAY = /(^|[\s"'`])text-gray-\d{2,3}(?=[\s"'`]|$)/;
const DARK_BG = /dark:(?:[\w-]+:)*bg-/;
const DARK_TEXT = /dark:(?:[\w-]+:)*text-/;

function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) return walk(p);
    return /\.(ts|tsx)$/.test(entry.name) ? [p] : [];
  });
}

const files = ["app", "components", "lib"].flatMap((d) => walk(path.join(ROOT, d))).filter((f) => !ALLOWED.has(f));

function violations(test: (line: string) => boolean): string[] {
  const found: string[] = [];
  for (const file of files) {
    fs.readFileSync(file, "utf-8")
      .split(/\r?\n/)
      .forEach((line, i) => {
        const code = line.replace(/\/\/.*$/, "");
        if (test(code)) found.push(`${path.relative(ROOT, file)}:${i + 1}: ${line.trim().slice(0, 120)}`);
      });
  }
  return found;
}

describe("theme guard: tokens only outside lib/theme-tokens.ts", () => {
  it("scans the UI source tree", () => {
    expect(files.length).toBeGreaterThan(20);
  });

  it("has no hex colours in app/, components/ or lib/", () => {
    expect(violations((l) => HEX.test(l))).toEqual([]);
  });

  it("has no bg-white without a dark: background on the same line", () => {
    expect(violations((l) => BG_WHITE.test(l) && !DARK_BG.test(l))).toEqual([]);
  });

  it("has no text-gray-* without a dark: text colour on the same line", () => {
    expect(violations((l) => TEXT_GRAY.test(l) && !DARK_TEXT.test(l))).toEqual([]);
  });

  it("the patterns catch what they should (self-test)", () => {
    expect(HEX.test('stroke="#06b6d4"')).toBe(true);
    expect(HEX.test('fill: "#fff"')).toBe(true);
    expect(HEX.test('href="#about"')).toBe(false);
    expect(HEX.test("&#123;")).toBe(false);
    expect(BG_WHITE.test('className="bg-white p-4"')).toBe(true);
    expect(BG_WHITE.test('className="bg-white/10"')).toBe(false);
    expect(BG_WHITE.test('className="odd:bg-white"')).toBe(false);
    expect(TEXT_GRAY.test('"text-gray-500"')).toBe(true);
    expect(DARK_BG.test("dark:hover:bg-white/10")).toBe(true);
  });
});
