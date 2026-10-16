import { afterEach, describe, expect, it, vi } from "vitest";
import fs from "fs";
import path from "path";
import { createRequire } from "module";

const require = createRequire(import.meta.url);
const configPath = path.resolve(__dirname, "../../next.config.js");

type Header = { key: string; value: string };
type NextConfigUnderTest = {
  poweredByHeader?: boolean;
  experimental?: { optimizePackageImports?: string[] };
  headers: () => Promise<{ source: string; headers: Header[] }[]>;
  buildSecurityHeaders: (isProduction: boolean) => Header[];
};

function loadConfig(): NextConfigUnderTest {
  delete require.cache[configPath];
  return require(configPath) as NextConfigUnderTest;
}

function toMap(headers: Header[]): Record<string, string> {
  return Object.fromEntries(headers.map((h) => [h.key, h.value]));
}

const readPackage = () =>
  JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../package.json"), "utf-8"));

describe("next.config.js (F0.1.2 security headers)", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("disables X-Powered-By and keeps optimizePackageImports", () => {
    const config = loadConfig();
    expect(config.poweredByHeader).toBe(false);
    expect(config.experimental?.optimizePackageImports).toEqual(
      expect.arrayContaining(["lucide-react", "recharts"]),
    );
  });

  it("applies the baseline security headers to every route", async () => {
    vi.stubEnv("NODE_ENV", "development");
    const rules = await loadConfig().headers();
    expect(rules).toHaveLength(1);
    expect(rules[0].source).toBe("/:path*");
    const headers = toMap(rules[0].headers);
    expect(headers).toMatchObject({
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "strict-origin-when-cross-origin",
      "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
      "X-Frame-Options": "DENY",
      "Cross-Origin-Opener-Policy": "same-origin",
    });
    expect(headers["Strict-Transport-Security"]).toBeUndefined();
  });

  it("adds HSTS only in production", async () => {
    vi.stubEnv("NODE_ENV", "production");
    const rules = await loadConfig().headers();
    expect(toMap(rules[0].headers)["Strict-Transport-Security"]).toBe(
      "max-age=63072000; includeSubDomains",
    );
    expect(toMap(loadConfig().buildSecurityHeaders(false))["Strict-Transport-Security"]).toBeUndefined();
  });
});

describe("package.json (F0.1.1 port pin, F0.2.1 exact motion pin)", () => {
  it("pins dev, dev:webpack and start to port 3000", () => {
    const pkg = readPackage();
    expect(pkg.scripts.dev).toBe("next dev --turbo -p 3000");
    expect(pkg.scripts["dev:webpack"]).toBe("next dev -p 3000");
    // `start` runs the standalone production build, whose launcher defaults PORT to 3000.
    expect(pkg.scripts.start).toBe("node scripts/start-standalone.mjs");
    const launcher = fs.readFileSync(path.resolve(__dirname, "../../scripts/start-standalone.mjs"), "utf-8");
    expect(launcher).toMatch(/PORT:\s*process\.env\.PORT\s*\?\?\s*"3000"/);
  });

  it("pins motion to an exact version (no range)", () => {
    expect(readPackage().dependencies.motion).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
