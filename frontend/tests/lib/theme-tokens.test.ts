import { describe, expect, it } from "vitest";
import fs from "fs";
import path from "path";
import {
  BRAND_SHADES,
  brandRamp,
  chartTheme,
  contrastRatio,
  hexToRgbTriplet,
  legacyDarkPalette,
  semanticTokens,
  type ThemeName,
} from "@/lib/theme-tokens";
import tailwindConfig from "@/tailwind.config";

const css = fs.readFileSync(path.resolve(__dirname, "../../app/globals.css"), "utf-8");

/** Parses `--name: r g b;` declarations from the first `selector {` block. */
function cssBlock(selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  expect(start, `missing ${selector} block`).toBeGreaterThanOrEqual(0);
  const body = css.slice(start, css.indexOf("}", start));
  const vars: Record<string, string> = {};
  for (const m of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) vars[m[1]] = m[2].trim();
  return vars;
}

const AA_TEXT = 4.5;
const AA_NON_TEXT = 3;
const THEMES: ThemeName[] = ["light", "dark"];
const SURFACES = ["surface-page", "surface-card", "surface-muted", "surface-elevated", "surface-studio"] as const;
const TEXT_TOKENS = [
  "fg",
  "fg-muted",
  "fg-subtle",
  "accent-fg",
  "status-ok-fg",
  "status-warn-fg",
  "status-danger-fg",
  "status-info-fg",
] as const;
const STATUSES = ["ok", "warn", "danger", "info"] as const;

describe("theme tokens: values (snapshot)", () => {
  it("seeds the Exigo Tech teal ramp", () => {
    expect(brandRamp["600"]).toBe("#008080");
    expect(brandRamp["800"]).toBe("#00584F");
    expect(brandRamp["50"]).toBe("#EAF6F5");
  });

  it("keeps the documented warn and danger light values and lifts them in dark (T6)", () => {
    expect(semanticTokens.light["status-warn"]).toBe("#B7791F");
    expect(semanticTokens.light["status-danger"]).toBe("#C0362C");
    expect(semanticTokens.dark["status-warn"]).not.toBe(semanticTokens.light["status-warn"]);
    expect(semanticTokens.dark["status-danger"]).not.toBe(semanticTokens.light["status-danger"]);
  });

  it("matches the light and dark token snapshot", () => {
    expect({ brandRamp, semanticTokens, chartTheme }).toMatchSnapshot();
  });

  it("chartTheme has the categorical, sequential and diverging scales in both themes", () => {
    for (const t of THEMES) {
      expect(chartTheme[t].categorical).toHaveLength(6);
      // Eight distinct hues for multi-series stacks (VA-9); none repeats.
      expect(chartTheme[t].multiSeries).toHaveLength(8);
      expect(new Set(chartTheme[t].multiSeries).size).toBe(8);
      expect(chartTheme[t].sequential.length).toBeGreaterThanOrEqual(5);
      expect(chartTheme[t].diverging.length % 2).toBe(1);
      expect(chartTheme[t].dimOpacity).toBe(0.35);
    }
  });
});

describe("globals.css mirrors lib/theme-tokens.ts", () => {
  it("defines every brand shade on :root", () => {
    const root = cssBlock(":root");
    for (const shade of BRAND_SHADES) {
      expect(root[`brand-${shade}`], `--brand-${shade}`).toBe(hexToRgbTriplet(brandRamp[shade]));
    }
  });

  it("defines every semantic token for light (:root) and dark (.dark)", () => {
    const light = cssBlock(":root");
    const dark = cssBlock(".dark");
    for (const [key, hex] of Object.entries(semanticTokens.light)) {
      expect(light[key], `light --${key}`).toBe(hexToRgbTriplet(hex));
    }
    for (const [key, hex] of Object.entries(semanticTokens.dark)) {
      expect(dark[key], `dark --${key}`).toBe(hexToRgbTriplet(hex));
    }
  });
});

describe("tailwind maps colours to the CSS variables", () => {
  const colors = (tailwindConfig.theme?.extend?.colors ?? {}) as Record<string, Record<string, string>>;

  it("keeps every brand-* class and makes it var-backed with alpha support", () => {
    for (const shade of BRAND_SHADES) {
      expect(colors.brand[shade]).toBe(`rgb(var(--brand-${shade}) / <alpha-value>)`);
    }
  });

  it("exposes the semantic token classes", () => {
    expect(colors.surface.card).toBe("rgb(var(--surface-card) / <alpha-value>)");
    expect(colors.fg.DEFAULT).toBe("rgb(var(--fg) / <alpha-value>)");
    expect(colors.fg.subtle).toBe("rgb(var(--fg-subtle) / <alpha-value>)");
    expect(colors.line.DEFAULT).toBe("rgb(var(--line) / <alpha-value>)");
    expect(colors.status["danger-fg"]).toBe("rgb(var(--status-danger-fg) / <alpha-value>)");
  });
});

describe("contrast (WCAG AA) in both themes", () => {
  for (const theme of THEMES) {
    const t = semanticTokens[theme];

    it(`${theme}: every text token is >= 4.5:1 on every surface`, () => {
      for (const fg of TEXT_TOKENS) {
        for (const bg of SURFACES) {
          expect(contrastRatio(t[fg], t[bg]), `${theme} ${fg} on ${bg}`).toBeGreaterThanOrEqual(AA_TEXT);
        }
      }
    });

    it(`${theme}: status text is >= 4.5:1 on its own tint`, () => {
      for (const s of STATUSES) {
        expect(contrastRatio(t[`status-${s}-fg`], t[`status-${s}-bg`]), `${theme} ${s}`).toBeGreaterThanOrEqual(
          AA_TEXT,
        );
      }
    });

    it(`${theme}: status fills, accent and chart series are >= 3:1 on every surface`, () => {
      const fills = [
        ...STATUSES.map((s) => t[`status-${s}`]),
        t.accent,
        ...chartTheme[theme].categorical,
        ...chartTheme[theme].multiSeries,
      ];
      for (const fill of fills) {
        for (const bg of SURFACES) {
          expect(contrastRatio(fill, t[bg]), `${theme} ${fill} on ${bg}`).toBeGreaterThanOrEqual(AA_NON_TEXT);
        }
      }
    });

    it(`${theme}: chart axis labels are readable (>= 4.5:1)`, () => {
      for (const bg of SURFACES) {
        expect(contrastRatio(chartTheme[theme].axis, t[bg])).toBeGreaterThanOrEqual(AA_TEXT);
      }
    });
  }

  it("white text on the primary brand button (brand-600) passes AA", () => {
    expect(contrastRatio("#FFFFFF", brandRamp["600"])).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it("contrastRatio matches known WCAG values", () => {
    expect(contrastRatio("#000000", "#FFFFFF")).toBeCloseTo(21, 0);
    expect(contrastRatio("#777777", "#FFFFFF")).toBeCloseTo(4.48, 1);
  });
});

describe("studio surface (Live Telemetry look rolled out to every card)", () => {
  const ext = tailwindConfig.theme?.extend as {
    backgroundImage: Record<string, string>;
    boxShadow: Record<string, string>;
  };

  it("bg-studio and shadow-studio are built from theme variables only", () => {
    expect(ext.backgroundImage.studio).toContain("var(--surface-studio)");
    expect(ext.backgroundImage.studio).toContain("var(--surface-card)");
    expect(ext.backgroundImage.studio).toContain("var(--brand-400)");
    expect(ext.backgroundImage.studio).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(ext.boxShadow.studio).toBe("var(--shadow-studio)");
    expect(ext.boxShadow["studio-hover"]).toBe("var(--shadow-studio-hover)");
  });

  it("defines the studio effects separately for light (:root) and dark (.dark)", () => {
    const light = cssBlock(":root");
    const dark = cssBlock(".dark");
    for (const key of ["studio-glow-alpha", "shadow-studio", "shadow-studio-hover"]) {
      expect(light[key], `light --${key}`).toBeTruthy();
      expect(dark[key], `dark --${key}`).toBeTruthy();
      expect(dark[key], `--${key} differs per theme`).not.toBe(light[key]);
    }
  });

  it("dark neutrals carry the brand hue (green/blue channels above red), not slate", () => {
    for (const key of ["surface-card", "surface-muted", "surface-elevated", "surface-studio", "line"] as const) {
      const [r, g, b] = hexToRgbTriplet(semanticTokens.dark[key]).split(" ").map(Number);
      expect(g, key).toBeGreaterThan(r);
      expect(b, key).toBeGreaterThan(r);
    }
  });

  it("legacy dark-* classes mirror the dark tokens so old pages match new ones", () => {
    expect(legacyDarkPalette.bg).toBe(semanticTokens.dark["surface-page"]);
    expect(legacyDarkPalette.surface).toBe(semanticTokens.dark["surface-muted"]);
    expect(legacyDarkPalette.card).toBe(semanticTokens.dark["surface-elevated"]);
    expect(legacyDarkPalette.border).toBe(semanticTokens.dark.line);
  });
});
