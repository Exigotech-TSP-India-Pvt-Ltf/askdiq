/**
 * Pure form model for Settings → Branding: server payload ⇄ form state,
 * client validation (mirrors backend/app/schemas/branding.py) and the partial
 * `PUT /admin/branding` diff.
 */
import { DEFAULT_BRAND, defaultChatSampleQuestions, normalizeBrand, type Brand } from "@/lib/brand";
import { deriveRamp, normalizeHex } from "@/lib/brand-palette";
import {
  BRAND_ASSET_SLOTS,
  MAX_QUESTION_CHARS,
  MAX_SAMPLE_QUESTIONS,
  assetField,
  isSafeLink,
  type BrandAssetSlot,
  type BrandAssetUrls,
  type BrandingDefaults,
  type BrandingRead,
  type BrandingUpdate,
} from "@/lib/settings-api";
import { brandRamp } from "@/lib/theme-tokens";

export interface BrandingForm {
  product_name: string;
  assistant_name: string;
  company_name: string;
  support_email: string;
  access_contact_email: string;
  primary_hex: string;
  /** "" = derive from the primary colour. */
  accent_hex: string;
  /** "" = derive from the primary colour. */
  tint_hex: string;
  login_eyebrow: string;
  login_headline: string;
  login_subtext: string;
  login_description: string;
  privacy_url: string;
  terms_url: string;
  help_url: string;
  chat_sample_questions: string[];
  logo_light_asset_id: string | null;
  logo_dark_asset_id: string | null;
  mark_light_asset_id: string | null;
  mark_dark_asset_id: string | null;
  favicon_asset_id: string | null;
}

export type BrandingFormErrors = Partial<Record<keyof BrandingForm, string>>;

export const TEXT_LIMITS = {
  product_name: 80,
  assistant_name: 80,
  company_name: 120,
  login_eyebrow: 60,
  login_headline: 160,
  login_subtext: 400,
  login_description: 1000,
} as const;

export const URL_FIELDS = ["privacy_url", "terms_url", "help_url"] as const;
const REQUIRED_TEXT = ["product_name", "assistant_name", "company_name"] as const;
const OPTIONAL_TEXT = ["login_eyebrow", "login_headline", "login_subtext", "login_description"] as const;
const SINGLE_LINE = ["product_name", "assistant_name", "company_name", "login_eyebrow", "login_headline"] as const;
const COLOR_FIELDS = ["primary_hex", "accent_hex", "tint_hex"] as const;
// Pragmatic email shape; the server (EmailStr) is the authority.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
// eslint-disable-next-line no-control-regex -- the server rejects control characters in single-line fields
const CONTROL_RE = /[\u0000-\u001f\u007f]/;

export function formFromBranding(b: BrandingDefaults): BrandingForm {
  return {
    product_name: b.product_name,
    assistant_name: b.assistant_name,
    company_name: b.company_name,
    support_email: b.support_email,
    access_contact_email: b.access_contact_email ?? "",
    primary_hex: b.primary_hex,
    accent_hex: b.accent_hex ?? "",
    tint_hex: b.tint_hex ?? "",
    login_eyebrow: b.login_eyebrow ?? "",
    login_headline: b.login_headline ?? "",
    login_subtext: b.login_subtext ?? "",
    login_description: b.login_description ?? "",
    privacy_url: b.privacy_url ?? "",
    terms_url: b.terms_url ?? "",
    help_url: b.help_url ?? "",
    chat_sample_questions: [...b.chat_sample_questions],
    logo_light_asset_id: b.logo_light_asset_id,
    logo_dark_asset_id: b.logo_dark_asset_id,
    mark_light_asset_id: b.mark_light_asset_id,
    mark_dark_asset_id: b.mark_dark_asset_id,
    favicon_asset_id: b.favicon_asset_id,
  };
}

/** The server's seeded row as a form ("Reset to defaults"). */
export function formFromDefaults(d: BrandingDefaults): BrandingForm {
  return formFromBranding(d);
}

/** Built-in fallback when the server defaults cannot be loaded (older backend). */
export function defaultBrandingForm(): BrandingForm {
  const d = DEFAULT_BRAND;
  return {
    product_name: d.productName,
    assistant_name: d.assistantName,
    company_name: d.companyName,
    support_email: d.supportEmail,
    access_contact_email: "",
    primary_hex: brandRamp["600"].toLowerCase(),
    accent_hex: "",
    tint_hex: "",
    login_eyebrow: "",
    login_headline: "",
    login_subtext: "",
    login_description: "",
    privacy_url: "",
    terms_url: "",
    help_url: "",
    chat_sample_questions: defaultChatSampleQuestions(d.productName),
    logo_light_asset_id: null,
    logo_dark_asset_id: null,
    mark_light_asset_id: null,
    mark_dark_asset_id: null,
    favicon_asset_id: null,
  };
}

export function validateBrandingForm(form: BrandingForm): BrandingFormErrors {
  const errors: BrandingFormErrors = {};
  for (const field of REQUIRED_TEXT) {
    if (!form[field].trim()) errors[field] = "Required.";
  }
  for (const field of [...REQUIRED_TEXT, ...OPTIONAL_TEXT]) {
    if (!errors[field] && form[field].trim().length > TEXT_LIMITS[field]) {
      errors[field] = `At most ${TEXT_LIMITS[field]} characters.`;
    }
  }
  for (const field of SINGLE_LINE) {
    if (!errors[field] && CONTROL_RE.test(form[field])) errors[field] = "Use a single line.";
  }
  if (!EMAIL_RE.test(form.support_email.trim())) errors.support_email = "Enter a valid email address.";
  if (form.access_contact_email.trim() && !EMAIL_RE.test(form.access_contact_email.trim())) {
    errors.access_contact_email = "Enter a valid email address, or leave it empty.";
  }
  for (const field of COLOR_FIELDS) {
    const value = form[field].trim();
    const optional = field !== "primary_hex";
    if ((value || !optional) && !normalizeHex(value)) errors[field] = "Use a #RRGGBB colour.";
  }
  for (const field of URL_FIELDS) {
    const value = form[field].trim();
    if (value && !isSafeLink(value)) {
      errors[field] = "Must start with https:// or be a relative path such as /privacy.";
    }
  }
  const questions = form.chat_sample_questions;
  if (questions.length === 0) errors.chat_sample_questions = "Add at least one sample question.";
  else if (questions.length > MAX_SAMPLE_QUESTIONS) errors.chat_sample_questions = `At most ${MAX_SAMPLE_QUESTIONS} questions.`;
  else if (questions.some((q) => !q.trim())) errors.chat_sample_questions = "Questions cannot be empty.";
  else if (questions.some((q) => q.trim().length > MAX_QUESTION_CHARS)) {
    errors.chat_sample_questions = `Each question is at most ${MAX_QUESTION_CHARS} characters.`;
  } else if (questions.some((q) => CONTROL_RE.test(q))) errors.chat_sample_questions = "Use one line per question.";
  return errors;
}

const NULLABLE: ReadonlySet<keyof BrandingForm> = new Set([
  "access_contact_email",
  "accent_hex",
  "tint_hex",
  ...OPTIONAL_TEXT,
  ...URL_FIELDS,
]);

function normalizedValue(form: BrandingForm, key: keyof BrandingForm): string | string[] | null {
  const value = form[key];
  if (Array.isArray(value)) return value.map((q) => q.trim());
  if (value === null) return null;
  let v = value.trim();
  if (COLOR_FIELDS.includes(key as (typeof COLOR_FIELDS)[number])) v = v.toLowerCase();
  if (!v && NULLABLE.has(key)) return null;
  return v;
}

/** Only the fields that differ from `baseline` (blank optional fields become null). */
export function diffBrandingForm(baseline: BrandingForm, form: BrandingForm): Omit<BrandingUpdate, "version"> {
  const changes: Record<string, unknown> = {};
  for (const key of Object.keys(form) as (keyof BrandingForm)[]) {
    const next = normalizedValue(form, key);
    const prev = normalizedValue(baseline, key);
    if (JSON.stringify(next) !== JSON.stringify(prev)) changes[key] = next;
  }
  return changes as Omit<BrandingUpdate, "version">;
}

/** Every field, for "Reset to defaults" (cleared optional fields included). */
export function fullBrandingPayload(form: BrandingForm): Omit<BrandingUpdate, "version"> {
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(form) as (keyof BrandingForm)[]) out[key] = normalizedValue(form, key);
  return out as Omit<BrandingUpdate, "version">;
}

export function assetUrlsFromBranding(b: BrandingRead): BrandAssetUrls {
  return Object.fromEntries(BRAND_ASSET_SLOTS.map((slot) => [slot, b.assets?.[slot] ?? null])) as BrandAssetUrls;
}

/** The unsaved draft as a Brand, for the live preview. */
export function brandFromForm(form: BrandingForm, assetUrls: BrandAssetUrls): Brand {
  const url = (slot: BrandAssetSlot) => (form[assetField(slot)] ? assetUrls[slot] : null);
  const ramp = deriveRamp(form.primary_hex);
  return normalizeBrand({
    product_name: form.product_name,
    assistant_name: form.assistant_name,
    company_name: form.company_name,
    support_email: form.support_email,
    access_contact_email: form.access_contact_email,
    palette: ramp ?? undefined,
    assets: {
      logo_light: url("logo_light"),
      logo_dark: url("logo_dark"),
      mark_light: url("mark_light"),
      mark_dark: url("mark_dark"),
      favicon: url("favicon"),
    },
    login_eyebrow: form.login_eyebrow,
    login_headline: form.login_headline,
    login_subtext: form.login_subtext,
    login_description: form.login_description,
    privacy_url: isSafeLink(form.privacy_url.trim()) ? form.privacy_url.trim() : null,
    terms_url: isSafeLink(form.terms_url.trim()) ? form.terms_url.trim() : null,
    help_url: isSafeLink(form.help_url.trim()) ? form.help_url.trim() : null,
    chat_sample_questions: form.chat_sample_questions,
  });
}
