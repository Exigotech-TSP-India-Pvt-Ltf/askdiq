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
  /**
   * Studio surface (Usage "Live Telemetry" cards): the brand-tinted
   * top of every card gradient, table header rows and inset tiles.
   */
  "surface-studio": string;
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
    "surface-studio": "#F2FAF9",
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
    // Dark neutrals carry a slight brand (teal) hue so cards, table headers,
    // popovers and inputs match the studio look instead of reading as slate.
    "surface-page": "#090D11",
    "surface-card": "#0B1113",
    "surface-muted": "#0E1A1C",
    "surface-elevated": "#112123",
    "surface-inverse": "#F1F5F9",
    "surface-studio": "#071D1C",
    line: "#1A3032",
    "line-strong": "#2A4648",
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
    "chart-grid": "#1A3032",
    "chart-axis": "#8494A7",
    "chart-track": "#083038",
  },
};

export interface ChartTheme {
  /** teal, dark teal, amber, slate, red, indigo */
  categorical: readonly string[];
  /**
   * Distinct-hue palette for multi-series stacks and legends with more than
   * a few series (e.g. cost by stage): brand teal first, then hues that stay
   * apart under colour-vision deficiency (adjacent-pair CVD Delta E >= 8,
   * checked with a palette validator). Assign in order, never cycle.
   */
  multiSeries: readonly string[];
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
  /** Analytics (Chatbot Insights port): the reference HTML's colours, lifted for dark. */
  insights: InsightsChartColors;
}

export type InsightsOutcome = "Served" | "Partly served" | "Not served" | "Greeting only";

export interface InsightsChartColors {
  /** Conversation outcome (flow, bubbles, pills). */
  outcome: Record<InsightsOutcome, string>;
  /** Bot-quality code (donut); unknown codes fall back to `categorical`. */
  quality: Readonly<Record<string, string>>;
  /** Topic shading: 0 % handled (low) to 100 % handled (high). */
  handled: { low: string; high: string };
  /** Hour-of-day ramp ends (radial). */
  hours: { low: string; high: string };
}

// The HTML used #C8A039 (Partly) and #B7C4C5 (Greeting / Closing); both are under 3:1 on a
// white card, so light mode uses status-warn and slate instead (asserted in the token tests).
const insightsLight: InsightsChartColors = {
  outcome: { Served: "#008080", "Partly served": "#B7791F", "Not served": "#C0362C", "Greeting only": "#64748B" },
  quality: {
    OK: "#008080",
    CORR: "#2E8B8B",
    SCOPE: "#6E7C7D",
    PART: "#B7791F",
    FB: "#8A8F73",
    CLAR: "#5B8FA8",
    OFF: "#C0362C",
    FALSE: "#9A3B45",
    ACK: "#64748B",
  },
  handled: { low: "#C0362C", high: "#008080" },
  hours: { low: "#CFE9E6", high: "#008080" },
};

const insightsDark: InsightsChartColors = {
  outcome: { Served: "#2DA9A0", "Partly served": "#F2B84B", "Not served": "#F87171", "Greeting only": "#64748B" },
  quality: {
    OK: "#2DA9A0",
    CORR: "#5FC3BB",
    SCOPE: "#94A3B8",
    PART: "#F2B84B",
    FB: "#B5B98F",
    CLAR: "#7DB8D6",
    OFF: "#F87171",
    FALSE: "#E07A86",
    ACK: "#64748B",
  },
  handled: { low: "#F87171", high: "#2DA9A0" },
  hours: { low: "#0F3B38", high: "#5FC3BB" },
};

export const chartTheme: Record<ThemeName, ChartTheme> = {
  light: {
    categorical: ["#008080", "#00584F", "#B7791F", "#64748B", "#C0362C", "#4F46E5"],
    multiSeries: ["#008080", "#D4561C", "#2A78D6", "#A66B00", "#C83E86", "#2E7D32", "#6250D6", "#D93636"],
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
    insights: insightsLight,
  },
  dark: {
    categorical: ["#2DA9A0", "#5FC3BB", "#F2B84B", "#94A3B8", "#F87171", "#818CF8"],
    multiSeries: ["#2DA9A0", "#D95926", "#3987E5", "#C98500", "#D55181", "#008300", "#9085E9", "#E66767"],
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
    insights: insightsDark,
  },
};

/** Fixed Tailwind palette for the legacy `dark-*` classes (kept for compatibility). */
export const legacyDarkPalette = {
  // Mirrors the dark surface-muted / surface-elevated / line tokens so legacy
  // `dark:bg-dark-surface` classes sit on the same brand-tinted neutrals.
  bg: "#090D11",
  surface: "#0E1A1C",
  card: "#112123",
  border: "#1A3032",
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

/** Linear blend of two hex colours (`t` in 0..1), e.g. the topic handled-% scale. */
export function mixHex(from: string, to: string, t: number): string {
  const k = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0));
  const a = hexToRgb(from);
  const b = hexToRgb(to);
  const out = a.map((c, i) => Math.round(c + (b[i] - c) * k).toString(16).padStart(2, "0"));
  return `#${out.join("")}`.toUpperCase();
}

/** Text colour (`fg` or `fg-inverse` of the theme) with the higher contrast on a mark fill. */
export function readableOn(fill: string, theme: ThemeName): string {
  const a = semanticTokens[theme].fg;
  const b = semanticTokens[theme]["fg-inverse"];
  return contrastRatio(a, fill) >= contrastRatio(b, fill) ? a : b;
}

/** Tailwind colour value backed by a CSS variable holding an "r g b" triplet. */
export function cssVarColor(name: string): string {
  return `rgb(var(--${name}) / <alpha-value>)`;
}
