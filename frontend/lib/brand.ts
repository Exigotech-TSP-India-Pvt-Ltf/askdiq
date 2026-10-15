/**
 * White-label brand model (newplan §14). Server-safe (no React, no "use client"):
 * used by the root layout (server fetch + generateMetadata) and BrandProvider.
 *
 * `GET /branding` today returns { brand_name, brand_assistant_name,
 * brand_support_email }. PR-08 adds logo/mark/favicon URLs, a palette,
 * login copy and chat sample questions; `normalizeBrand` accepts both shapes
 * and falls back to the seeded Exigo Tech defaults for anything missing or
 * invalid.
 */
import type { Metadata } from "next";
import { BRAND_SHADES, brandRamp, hexToRgbTriplet, isHexColor, type BrandShade } from "@/lib/theme-tokens";

export interface BrandLogos {
  light: string;
  dark: string;
  markLight: string;
  markDark: string;
}

export interface BrandLogin {
  eyebrow: string | null;
  headline: string | null;
  subtext: string | null;
  description: string | null;
}

export interface Brand {
  productName: string;
  assistantName: string;
  companyName: string;
  supportEmail: string;
  logos: BrandLogos;
  favicon: string;
  /** Only the shades the tenant overrides; the rest come from theme-tokens. */
  palette: Partial<Record<BrandShade, string>>;
  login: BrandLogin;
  chatSampleQuestions: string[];
}

export function defaultChatSampleQuestions(productName: string): string[] {
  return [
    `What is the Essential Eight and how does ${productName} implement it?`,
    `How does ${productName} keep humans in the loop over AI actions?`,
    "What does the Zero Trust Professional plan include?",
    `How does ${productName} handle customer data?`,
  ];
}

export const DEFAULT_BRAND: Brand = {
  productName: "DeployIQ",
  assistantName: "AskDIQ",
  companyName: "Exigo Tech",
  supportEmail: "support@deployiq.com",
  logos: {
    light: "/exigotech-logo.svg",
    dark: "/exigotech-logo-dark.svg",
    markLight: "/exigotech-mark.svg",
    markDark: "/exigotech-mark-dark.svg",
  },
  favicon: "/favicon.svg",
  palette: {},
  login: { eyebrow: null, headline: null, subtext: null, description: null },
  chatSampleQuestions: defaultChatSampleQuestions("DeployIQ"),
};

const MAX_TEXT = 200;
const MAX_QUESTIONS = 8;

function text(value: unknown, max = MAX_TEXT): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed && trimmed.length <= max ? trimmed : null;
}

/** Only same-origin relative paths or https URLs (no javascript:, data:, //host). */
export function safeAssetUrl(value: unknown): string | null {
  const v = text(value, 2048);
  if (!v) return null;
  if (v.startsWith("/") && !v.startsWith("//") && !v.includes("\\")) return v;
  try {
    const url = new URL(v);
    return url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function pick(raw: Record<string, unknown>, ...keys: string[]): unknown {
  for (const key of keys) {
    if (raw[key] !== undefined && raw[key] !== null) return raw[key];
  }
  return undefined;
}

function normalizePalette(raw: Record<string, unknown>): Brand["palette"] {
  const palette: Brand["palette"] = {};
  const source = pick(raw, "palette", "brand_palette");
  if (source && typeof source === "object" && !Array.isArray(source)) {
    for (const shade of BRAND_SHADES) {
      const value = (source as Record<string, unknown>)[shade];
      if (isHexColor(value)) palette[shade] = value.trim();
    }
  }
  const primary = pick(raw, "primary_hex");
  if (!palette["600"] && isHexColor(primary)) palette["600"] = primary.trim();
  return palette;
}

function normalizeQuestions(value: unknown, productName: string): string[] {
  if (Array.isArray(value)) {
    const questions = value
      .map((q) => text(q, 300))
      .filter((q): q is string => q !== null)
      .slice(0, MAX_QUESTIONS);
    if (questions.length) return questions;
  }
  return defaultChatSampleQuestions(productName);
}

/** Maps any `/branding` payload (current or future) onto a complete Brand. */
export function normalizeBrand(payload: unknown): Brand {
  if (!payload || typeof payload !== "object") return DEFAULT_BRAND;
  const raw = payload as Record<string, unknown>;
  const productName = text(pick(raw, "product_name", "brand_name")) ?? DEFAULT_BRAND.productName;
  const d = DEFAULT_BRAND;

  return {
    productName,
    assistantName: text(pick(raw, "assistant_name", "brand_assistant_name")) ?? d.assistantName,
    companyName: text(pick(raw, "company_name")) ?? d.companyName,
    supportEmail: text(pick(raw, "support_email", "brand_support_email")) ?? d.supportEmail,
    logos: {
      light: safeAssetUrl(pick(raw, "logo_light_url")) ?? d.logos.light,
      dark: safeAssetUrl(pick(raw, "logo_dark_url")) ?? d.logos.dark,
      markLight: safeAssetUrl(pick(raw, "mark_light_url")) ?? d.logos.markLight,
      markDark: safeAssetUrl(pick(raw, "mark_dark_url")) ?? d.logos.markDark,
    },
    favicon: safeAssetUrl(pick(raw, "favicon_url")) ?? d.favicon,
    palette: normalizePalette(raw),
    login: {
      eyebrow: text(pick(raw, "login_eyebrow")),
      headline: text(pick(raw, "login_headline")),
      subtext: text(pick(raw, "login_subtext"), 500),
      description: text(pick(raw, "login_description"), 1000),
    },
    chatSampleQuestions: normalizeQuestions(pick(raw, "chat_sample_questions"), productName),
  };
}

/** CSS custom properties for the tenant's palette overrides ("r g b" triplets). */
export function brandCssVariables(brand: Pick<Brand, "palette">): Record<string, string> {
  const vars: Record<string, string> = {};
  for (const shade of BRAND_SHADES) {
    const value = brand.palette[shade];
    if (value && isHexColor(value) && value.toUpperCase() !== brandRamp[shade]) {
      vars[`--brand-${shade}`] = hexToRgbTriplet(value);
    }
  }
  return vars;
}

/** `:root{...}` rule for the server-rendered <style>, or "" when nothing is overridden. */
export function brandCssText(brand: Pick<Brand, "palette">): string {
  const entries = Object.entries(brandCssVariables(brand));
  if (!entries.length) return "";
  return `:root{${entries.map(([k, v]) => `${k}:${v};`).join("")}}`;
}

/** Document title, description and icons from the brand (used by generateMetadata). */
export function buildMetadata(brand: Brand): Metadata {
  return {
    title: `${brand.productName} | Governed Security & Compliance Assistant`,
    description: `Ask ${brand.productName} about your security posture, Essential Eight and Zero Trust delivery, and governance — grounded in your own knowledge base, with every answer traceable.`,
    icons: {
      icon: [
        { url: brand.favicon, type: brand.favicon.endsWith(".svg") ? "image/svg+xml" : undefined },
        { url: brand.logos.markLight, type: "image/svg+xml" },
      ],
      shortcut: brand.favicon,
      apple: brand.logos.markLight,
    },
  };
}
