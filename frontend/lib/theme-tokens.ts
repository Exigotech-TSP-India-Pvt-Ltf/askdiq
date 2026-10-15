/**
 * Single source of truth for every colour used by the UI.
 *
 * - `app/globals.css` mirrors these values as CSS variables (`:root` for light,
 *   `.dark` for dark). `tests/lib/theme-tokens.test.ts` fails if they drift.
 * - `tailwind.config.ts` maps the variables to utility classes
 *   (`bg-surface`, `text-fg-muted`, `border-line`, `text-status-danger-fg`,
 *   `bg-brand-600/20`, ...), so classes follow the theme without a `dark:` pair.
 * - Charts read `chartTheme` through `useChartTheme()` (lib/use-chart-theme.ts),
 *   which re-renders when the `dark` class on <html> changes.
 *
 * This is the ONLY file in `app/`, `components/` and `lib/` allowed to contain
 * hex colours (enforced by `tests/lib/theme-guard.test.ts`).
 */

export type ThemeName = "light" | "dark";

export const BRAND_SHADES = [
  "50",
  "100",
  "200",
  "300",
  "400",
  "500",
  "600",
  "700",
  "800",
  "900",
  "950",
] as const;

export type BrandShade = (typeof BRAND_SHADES)[number];
export type BrandRamp = Record<BrandShade, string>;

/**
 * Seeded Exigo Tech teal ramp: 600 = #008080 (teal), 800 = #00584F (dark teal),
 * 50 = #EAF6F5 (tint). 400 is the "lifted" teal used on dark surfaces and in the
 * dark logo variants. White-label tenants override it at runtime (BrandProvider).
 */
export const brandRamp: BrandRamp = {
  "50": "#EAF6F5",
  "100": "#CCEDEA",
  "200": "#99DBD5",
  "300": "#5FC3BB",
  "400": "#2DA9A0",
  "500": "#13958F",
  "600": "#008080",
  "700": "#006B68",
  "800": "#00584F",
  "900": "#00433D",
  "950": "#002623",
};

/** Semantic tokens. Keys map 1:1 to `--<key>` CSS variables. */
export interface SemanticTokens {
  "surface-page": string;
  "surface-card": string;
  "surface-muted": string;
  "surface-elevated": string;
  "surface-inverse": string;
  line: string;
  "line-strong": string;
  "line-brand": string;
  fg: string;
  "fg-muted": string;
  "fg-subtle": string;
  "fg-inverse": string;
  accent: string;
  "accent-fg": string;
  "status-ok": string;
  "status-ok-fg": string;
  "status-ok-bg": string;
  "status-warn": string;
  "status-warn-fg": string;
  "status-warn-bg": string;
  "status-danger": string;
  "status-danger-fg": string;
  "status-danger-bg": string;
  "status-info": string;
  "status-info-fg": string;
  "status-info-bg": string;
  "chart-grid": string;
  "chart-axis": string;
  "chart-track": string;
}

export const semanticTokens: Record<ThemeName, SemanticTokens> = {
  light: {
    "surface-page": "#F4FBFB",
    "surface-card": "#FFFFFF",
    "surface-muted": "#F1F5F9",
    "surface-elevated": "#FFFFFF",
    "surface-inverse": "#0A1C1A",
    line: "#E2E8F0",
    "line-strong": "#CBD5E1",
    "line-brand": "#99DBD5",
    fg: "#0A1C1A",
    "fg-muted": "#475569",
    "fg-subtle": "#5B6B7F",
    "fg-inverse": "#FFFFFF",
    accent: "#008080",
    "accent-fg": "#006B68",
    // Status fills (icons, borders, chart marks) need 3:1; the *-fg text
    // variants need 4.5:1 (AA body text) on page, card and their *-bg tint.
    "status-ok": "#047857",
    "status-ok-fg": "#065F46",
    "status-ok-bg": "#ECFDF5",
    "status-warn": "#B7791F",
    "status-warn-fg": "#7C4A03",
    "status-warn-bg": "#FDF6E7",
    "status-danger": "#C0362C",
    "status-danger-fg": "#9F2A22",
    "status-danger-bg": "#FDEEEC",
    "status-info": "#0369A1",
    "status-info-fg": "#075985",
    "status-info-bg": "#EAF4FB",
    "chart-grid": "#E2E8F0",
    "chart-axis": "#5B6B7F",
    "chart-track": "#E2E8F0",
  },
  dark: {
    "surface-page": "#090D11",
    "surface-card": "#0C0D10",
    "surface-muted": "#0F1720",
    "surface-elevated": "#131D27",
    "surface-inverse": "#F1F5F9",
    line: "#1E293B",
    "line-strong": "#334155",
    "line-brand": "#0F4A46",
    fg: "#F1F5F9",
    "fg-muted": "#A3B1C2",
    "fg-subtle": "#8494A7",
    "fg-inverse": "#0A1C1A",
    accent: "#2DA9A0",
    "accent-fg": "#5FC3BB",
    // T6: lifted amber and red so status colours stay AA on dark surfaces.
    "status-ok": "#34D399",
    "status-ok-fg": "#6EE7B7",
    "status-ok-bg": "#062A1E",
    "status-warn": "#F2B84B",
    "status-warn-fg": "#F6CB74",
    "status-warn-bg": "#2B1F07",
    "status-danger": "#F87171",
    "status-danger-fg": "#FCA5A5",
    "status-danger-bg": "#2C0F0E",
    "status-info": "#38BDF8",
    "status-info-fg": "#7DD3FC",
    "status-info-bg": "#082234",
    "chart-grid": "#1E293B",
    "chart-axis": "#8494A7",
    "chart-track": "#083038",
  },
};

export interface ChartTheme {
  /** teal, dark teal, amber, slate, red, indigo */
  categorical: readonly string[];
  /** Low to high, teal ramp. */
  sequential: readonly string[];
  /** Teal (good) through neutral to red (bad). */
  diverging: readonly string[];
  /** Semantic series colours (pass/fail/warn charts, latency percentiles). */
  status: { ok: string; warn: string; danger: string; info: string };
  grid: string;
  axis: string;
  track: string;
  tooltip: { background: string; border: string; text: string };
  /** Opacity applied to non-hovered series. */
  dimOpacity: number;
}

export const chartTheme: Record<ThemeName, ChartTheme> = {
  light: {
    categorical: ["#008080", "#00584F", "#B7791F", "#64748B", "#C0362C", "#4F46E5"],
    sequential: ["#CCEDEA", "#99DBD5", "#5FC3BB", "#2DA9A0", "#008080", "#00584F"],
    diverging: ["#00584F", "#2DA9A0", "#99DBD5", "#F1F5F9", "#F4B4AC", "#E0665A", "#C0362C"],
    status: {
      ok: semanticTokens.light["status-ok"],
      warn: semanticTokens.light["status-warn"],
      danger: semanticTokens.light["status-danger"],
      info: semanticTokens.light["status-info"],
    },
    grid: semanticTokens.light["chart-grid"],
    axis: semanticTokens.light["chart-axis"],
    track: semanticTokens.light["chart-track"],
    tooltip: {
      background: semanticTokens.light["surface-elevated"],
      border: semanticTokens.light.line,
      text: semanticTokens.light.fg,
    },
    dimOpacity: 0.35,
  },
  dark: {
    categorical: ["#2DA9A0", "#5FC3BB", "#F2B84B", "#94A3B8", "#F87171", "#818CF8"],
    sequential: ["#00433D", "#006B68", "#13958F", "#2DA9A0", "#5FC3BB", "#99DBD5"],
    diverging: ["#5FC3BB", "#2DA9A0", "#00584F", "#1E293B", "#7A2A24", "#D4574B", "#F87171"],
    status: {
      ok: semanticTokens.dark["status-ok"],
      warn: semanticTokens.dark["status-warn"],
      danger: semanticTokens.dark["status-danger"],
      info: semanticTokens.dark["status-info"],
    },
    grid: semanticTokens.dark["chart-grid"],
    axis: semanticTokens.dark["chart-axis"],
    track: semanticTokens.dark["chart-track"],
    tooltip: {
      background: semanticTokens.dark["surface-elevated"],
      border: semanticTokens.dark.line,
      text: semanticTokens.dark.fg,
    },
    dimOpacity: 0.35,
  },
};

/** Fixed Tailwind palette for the legacy `dark-*` classes (kept for compatibility). */
export const legacyDarkPalette = {
  bg: "#090D11",
  surface: "#0F1720",
  card: "#131D27",
  border: "#1E293B",
  text: "#F1F5F9",
  muted: "#94A3B8",
} as const;

export const inkColor = "#0A1C1A";

/** Brand gradient stops used by `bg-brand-gradient` and the hero panels. */
export const brandGradient = {
  from: "#40E0D0",
  via: "#14B8A6",
  to: "#068987",
} as const;

/**
 * Microsoft's four-square logo colours, required unchanged by Microsoft's
 * sign-in branding guidelines (used only by the "Sign in with Microsoft" button).
 */
export const microsoftLogoColors = {
  red: "#F25022",
  green: "#7FBA00",
  blue: "#00A4EF",
  yellow: "#FFB900",
} as const;

const HEX_RE = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;

export function isHexColor(value: unknown): value is string {
  return typeof value === "string" && HEX_RE.test(value.trim());
}

/** "#008080" -> "0 128 128" (the format Tailwind's `<alpha-value>` needs). */
export function hexToRgbTriplet(hex: string): string {
  const [r, g, b] = hexToRgb(hex);
  return `${r} ${g} ${b}`;
}

export function hexToRgb(hex: string): [number, number, number] {
  if (!isHexColor(hex)) throw new Error(`Invalid hex colour: ${hex}`);
  let h = hex.trim().slice(1);
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function channel(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

export function relativeLuminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** WCAG 2.x contrast ratio between two opaque colours. */
export function contrastRatio(a: string, b: string): number {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/** Tailwind colour value backed by a CSS variable holding an "r g b" triplet. */
export function cssVarColor(name: string): string {
  return `rgb(var(--${name}) / <alpha-value>)`;
}
