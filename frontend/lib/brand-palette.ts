/**
 * Client-side mirror of the backend palette derivation and WCAG check
 * (backend/app/branding/palette.py). Used for the LIVE preview
 * and contrast badge in Settings → Branding; the server stays the authority
 * (`PUT /admin/branding` answers 422 with the reason when a colour fails).
 *
 * The 50 to 950 ramp is derived in OKLCH: 600 is the primary itself, lighter
 * steps move towards white and darker steps towards near-black, keeping the
 * hue, with chroma reduced at the ends and clipped into sRGB.
 */
import { BRAND_SHADES, contrastRatio, isHexColor, semanticTokens, type BrandRamp, type BrandShade } from "@/lib/theme-tokens";

const LIGHT_FRACTION: Partial<Record<BrandShade, number>> = {
  "50": 0.95,
  "100": 0.87,
  "200": 0.72,
  "300": 0.55,
  "400": 0.38,
  "500": 0.19,
};
const DARK_FRACTION: Partial<Record<BrandShade, number>> = { "700": 0.2, "800": 0.42, "900": 0.62, "950": 0.8 };
const LIGHT_END = 0.985;
const DARK_END = 0.18;
const LIGHT_CHROMA_LOSS = 0.8;
const DARK_CHROMA_LOSS = 0.35;
const GAMUT_STEPS = 24;

/** Light-theme text sits on white cards; dark-theme text on the dark page background. */
export const LIGHT_TEXT_BACKGROUND = semanticTokens.light["surface-card"];
export const DARK_TEXT_BACKGROUND = semanticTokens.dark["surface-page"];
/** WCAG AA for body text. */
export const MIN_TEXT_CONTRAST = 4.5;
/** WCAG AA for large text and UI components. */
export const MIN_UI_CONTRAST = 3;

interface Oklch {
  l: number;
  c: number;
  h: number;
}

type Rgb = [number, number, number];

/** "#RRGGBB" (any case, 6 digits) to lower case; null when invalid. */
export function normalizeHex(value: string): string | null {
  const v = value.trim();
  return /^#[0-9a-f]{6}$/i.test(v) ? v.toLowerCase() : null;
}

function hexToUnitRgb(hex: string): Rgb {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255) as Rgb;
}

function unitRgbToHex(rgb: Rgb): string {
  return `#${rgb
    .map((c) => Math.round(Math.min(Math.max(c, 0), 1) * 255).toString(16).padStart(2, "0"))
    .join("")}`;
}

const toLinear = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const fromLinear = (c: number) => (c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055);

function rgbToOklch(rgb: Rgb): Oklch {
  const [r, g, b] = rgb.map(toLinear);
  const lms = [
    0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
    0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
    0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b,
  ].map(Math.cbrt);
  const [l_, m_, s_] = lms;
  const l = 0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_;
  const a = 1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_;
  const b2 = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_;
  return { l, c: Math.hypot(a, b2), h: Math.atan2(b2, a) };
}

function oklchToLinear({ l, c, h }: Oklch): Rgb {
  const a = c * Math.cos(h);
  const b = c * Math.sin(h);
  const l3 = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m3 = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s3 = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3,
    -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3,
    -0.0041960863 * l3 - 0.7034186147 * m3 + 1.707614701 * s3,
  ];
}

const inGamut = (rgb: Rgb) => rgb.every((c) => c >= -1e-6 && c <= 1 + 1e-6);

function oklchToHex(color: Oklch): string {
  let linear = oklchToLinear(color);
  if (!inGamut(linear)) {
    let low = 0;
    let high = color.c;
    for (let i = 0; i < GAMUT_STEPS; i += 1) {
      const mid = (low + high) / 2;
      if (inGamut(oklchToLinear({ ...color, c: mid }))) low = mid;
      else high = mid;
    }
    linear = oklchToLinear({ ...color, c: low });
  }
  return unitRgbToHex(linear.map((c) => fromLinear(Math.min(Math.max(c, 0), 1))) as Rgb);
}

/** The "50" to "950" ramp for `primaryHex` (600 is the primary itself), or null for an invalid colour. */
export function deriveRamp(primaryHex: string): BrandRamp | null {
  const primary = normalizeHex(primaryHex);
  if (!primary) return null;
  const base = rgbToOklch(hexToUnitRgb(primary));
  const ramp = {} as BrandRamp;
  for (const shade of BRAND_SHADES) {
    if (shade === "600") {
      ramp[shade] = primary;
      continue;
    }
    const light = LIGHT_FRACTION[shade];
    const fraction = light ?? DARK_FRACTION[shade] ?? 0;
    const l = light !== undefined ? base.l + (LIGHT_END - base.l) * fraction : base.l - (base.l - DARK_END) * fraction;
    const c = base.c * (1 - (light !== undefined ? LIGHT_CHROMA_LOSS : DARK_CHROMA_LOSS) * fraction);
    ramp[shade] = oklchToHex({ l, c, h: base.h });
  }
  return ramp;
}

export interface ContrastCheck {
  id: "light" | "dark";
  label: string;
  foreground: string;
  background: string;
  ratio: number;
  passes: boolean;
}

export interface PaletteCheck {
  ramp: BrandRamp | null;
  checks: ContrastCheck[];
  passes: boolean;
  /** Same wording as the server's 422 reason; null when it passes. */
  reason: string | null;
}

/** Brand-600 on white (light theme) and brand-400 on the dark page (dark theme), both 4.5:1. */
export function checkPrimary(primaryHex: string): PaletteCheck {
  const ramp = deriveRamp(primaryHex);
  if (!ramp) return { ramp: null, checks: [], passes: false, reason: "Enter a colour as #RRGGBB." };
  const candidates: ContrastCheck[] = [
    {
      id: "light",
      label: "Light theme (brand-600 on white)",
      foreground: ramp["600"],
      background: LIGHT_TEXT_BACKGROUND,
      ratio: contrastRatio(ramp["600"], LIGHT_TEXT_BACKGROUND),
      passes: false,
    },
    {
      id: "dark",
      label: "Dark theme (brand-400 on dark background)",
      foreground: ramp["400"],
      background: DARK_TEXT_BACKGROUND,
      ratio: contrastRatio(ramp["400"], DARK_TEXT_BACKGROUND),
      passes: false,
    },
  ];
  const checks = candidates.map((c) => ({ ...c, passes: c.ratio >= MIN_TEXT_CONTRAST }));
  const problems = checks
    .filter((c) => !c.passes)
    .map((c) =>
      c.id === "light"
        ? `brand-600 ${c.foreground} on white is ${formatRatio(c.ratio)} (needs ${MIN_TEXT_CONTRAST}:1); choose a darker primary colour`
        : `brand-400 ${c.foreground} on the dark background is ${formatRatio(c.ratio)} (needs ${MIN_TEXT_CONTRAST}:1); choose a lighter or more saturated primary colour`,
    );
  return { ramp, checks, passes: problems.length === 0, reason: problems.length ? problems.join("; ") : null };
}

/** Contrast of `foreground` on `background`, or null when either is not a colour. */
export function safeContrast(foreground: string, background: string): number | null {
  return isHexColor(foreground) && isHexColor(background) ? contrastRatio(foreground, background) : null;
}

export function formatRatio(ratio: number): string {
  return `${ratio.toFixed(2)}:1`;
}
