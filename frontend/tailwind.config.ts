import type { Config } from "tailwindcss";
import {
  BRAND_SHADES,
  cssVarColor,
  inkColor,
  legacyDarkPalette,
  semanticTokens,
} from "./lib/theme-tokens";

// Every colour comes from lib/theme-tokens.ts. Brand and semantic colours are
// CSS variables (app/globals.css), so `brand-*`, `surface-*`, `fg-*`, `line-*`
// and `status-*` classes follow the theme and the runtime brand (BrandProvider)
// while still supporting opacity modifiers such as `bg-brand-600/20`.
const brand = Object.fromEntries(
  BRAND_SHADES.map((shade) => [shade, cssVarColor(`brand-${shade}`)]),
);

const semantic = (prefix: string) =>
  Object.fromEntries(
    Object.keys(semanticTokens.light)
      .filter((key) => key === prefix || key.startsWith(`${prefix}-`))
      .map((key) => [key === prefix ? "DEFAULT" : key.slice(prefix.length + 1), cssVarColor(key)]),
  );

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Legacy fixed dark palette (dark:bg-dark-bg etc.) kept for existing classes.
        dark: { ...legacyDarkPalette },
        // Drawn from the Exigo Tech AI mark (Teal -> Turquoise sweep).
        brand,
        ink: inkColor,
        surface: semantic("surface"),
        fg: semantic("fg"),
        line: semantic("line"),
        accent: semantic("accent"),
        status: semantic("status"),
        chart: semantic("chart"),
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
      },
      backgroundImage: {
        "brand-panel":
          "linear-gradient(135deg, rgb(var(--brand-950)) 0%, rgb(var(--brand-900)) 48%, rgb(var(--brand-600)) 100%)",
        "brand-gradient":
          "linear-gradient(135deg, var(--brand-gradient-from) 0%, var(--brand-gradient-via) 50%, var(--brand-gradient-to) 100%)",
      },
      boxShadow: {
        card: "0 1px 2px rgba(10, 28, 26, 0.04), 0 8px 24px rgba(10, 28, 26, 0.06)",
        "brand-glow": "0 4px 14px 0 var(--brand-glow)",
      },
    },
  },
  plugins: [],
};

export default config;
