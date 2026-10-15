/**
 * Typed client for Settings (newplan §14, planchanges4 F7):
 *
 * - Branding (backend PR-08, `backend/app/schemas/branding.py`,
 *   `backend/app/api/routes/branding.py`): `GET /branding`,
 *   `PUT /admin/branding`, `POST /admin/branding/assets`.
 * - Insights taxonomy (backend PR-13, `backend/app/schemas/insights.py`,
 *   `backend/app/api/routes/insights.py`): `GET|POST /admin/insights/taxonomy`,
 *   `PATCH /admin/insights/taxonomy/{kind}/{code}`, `POST .../archive`.
 *
 * Kept apart from `lib/api.ts` (shared hotspot); same `/api/proxy` route and
 * `ApiError`, plus the problem+json `request_id` and field errors.
 */
import { ApiError } from "./api";

const API_BASE_URL = "/api/proxy";
const TAXONOMY_PATH = "/admin/insights/taxonomy";

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export interface FieldError {
  loc: string[];
  msg: string;
}

/** `ApiError` with the problem+json extras (`request_id`, validation `errors`). */
export class SettingsApiError extends ApiError {
  requestId: string | null;
  fieldErrors: FieldError[];
  constructor(message: string, status: number, requestId: string | null = null, fieldErrors: FieldError[] = []) {
    super(message, status);
    this.name = "SettingsApiError";
    this.requestId = requestId;
    this.fieldErrors = fieldErrors;
  }
}

function parseFieldErrors(value: unknown): FieldError[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const { loc, msg } = item as { loc?: unknown; msg?: unknown };
    if (typeof msg !== "string") return [];
    return [{ loc: Array.isArray(loc) ? loc.map(String) : [], msg }];
  });
}

/** Human message for an error body: the `detail`, plus field messages for a 422. */
function errorMessage(detail: unknown, fieldErrors: FieldError[], fallback: string): string {
  const base = typeof detail === "string" && detail ? detail : fallback;
  if (!fieldErrors.length) return base;
  const fields = fieldErrors
    .map((e) => {
      const field = e.loc.filter((part) => part !== "body").join(".");
      return field ? `${field}: ${e.msg}` : e.msg;
    })
    .join("; ");
  return `${base} (${fields})`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isFormData = typeof FormData !== "undefined" && init.body instanceof FormData;
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(isFormData || init.body === undefined ? {} : { "Content-Type": "application/json" }),
      ...init.headers,
    },
  });
  if (!res.ok) {
    let body: Record<string, unknown> = {};
    try {
      body = ((await res.json()) ?? {}) as Record<string, unknown>;
    } catch {
      // no JSON body
    }
    const fieldErrors = parseFieldErrors(body.errors);
    const requestId = typeof body.request_id === "string" ? body.request_id : null;
    throw new SettingsApiError(errorMessage(body.detail, fieldErrors, res.statusText || `HTTP ${res.status}`), res.status, requestId, fieldErrors);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Branding
// ---------------------------------------------------------------------------

export const BRAND_ASSET_SLOTS = ["logo_light", "logo_dark", "mark_light", "mark_dark", "favicon"] as const;
export type BrandAssetSlot = (typeof BRAND_ASSET_SLOTS)[number];
export type BrandAssetField = `${BrandAssetSlot}_asset_id`;

export const assetField = (slot: BrandAssetSlot): BrandAssetField => `${slot}_asset_id`;

/** Mirrors the server's upload rules (512 KB, PNG / WebP / SVG). */
export const BRAND_ASSET_MAX_BYTES = 512 * 1024;
export const BRAND_ASSET_MIME_TYPES = ["image/png", "image/webp", "image/svg+xml"] as const;
export type BrandAssetMime = (typeof BRAND_ASSET_MIME_TYPES)[number];
export const MAX_SAMPLE_QUESTIONS = 8;
export const MAX_QUESTION_CHARS = 200;

export type BrandAssetUrls = Record<BrandAssetSlot, string | null>;

/** `GET /branding` / `PUT /admin/branding` response (`BrandingRead`). */
export interface BrandingRead {
  brand_name: string;
  brand_assistant_name: string;
  brand_support_email: string;
  product_name: string;
  assistant_name: string;
  company_name: string;
  support_email: string;
  access_contact_email: string | null;
  primary_hex: string;
  accent_hex: string;
  tint_hex: string;
  /** "50" to "950"; "600" is primary_hex. */
  palette: Record<string, string>;
  logo_light_asset_id: string | null;
  logo_dark_asset_id: string | null;
  mark_light_asset_id: string | null;
  mark_dark_asset_id: string | null;
  favicon_asset_id: string | null;
  /** Relative `/branding/assets/{sha}` URLs (served by app/branding/assets/[sha]). */
  assets: BrandAssetUrls;
  login_eyebrow: string | null;
  login_headline: string | null;
  login_subtext: string | null;
  login_description: string | null;
  privacy_url: string | null;
  terms_url: string | null;
  help_url: string | null;
  chat_sample_questions: string[];
  version: number;
  updated_at: string | null;
}

/** `PUT /admin/branding` body: partial; `null` clears an optional field. */
export interface BrandingUpdate {
  version: number;
  product_name?: string;
  assistant_name?: string;
  company_name?: string;
  support_email?: string;
  access_contact_email?: string | null;
  primary_hex?: string;
  accent_hex?: string | null;
  tint_hex?: string | null;
  logo_light_asset_id?: string | null;
  logo_dark_asset_id?: string | null;
  mark_light_asset_id?: string | null;
  mark_dark_asset_id?: string | null;
  favicon_asset_id?: string | null;
  login_eyebrow?: string | null;
  login_headline?: string | null;
  login_subtext?: string | null;
  login_description?: string | null;
  privacy_url?: string | null;
  terms_url?: string | null;
  help_url?: string | null;
  chat_sample_questions?: string[];
}

/** `POST /admin/branding/assets` response (`BrandAssetRead`). */
export interface BrandAssetRead {
  id: string;
  mime: BrandAssetMime;
  size_bytes: number;
  width: number | null;
  height: number | null;
  url: string;
  created_at: string | null;
}

export const BRANDING_KEY = "settings/branding";

export const settingsApi = {
  getBranding: (signal?: AbortSignal) => request<BrandingRead>("/branding", { signal, cache: "no-store" }),

  updateBranding: (body: BrandingUpdate) =>
    request<BrandingRead>("/admin/branding", { method: "PUT", body: JSON.stringify(body) }),

  uploadBrandAsset: (file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return request<BrandAssetRead>("/admin/branding/assets", { method: "POST", body: form });
  },

  listTaxonomy: (signal?: AbortSignal, { includeArchived = true }: { includeArchived?: boolean } = {}) =>
    request<TaxonomyList>(`${TAXONOMY_PATH}?include_archived=${includeArchived}`, { signal }),

  createTaxonomy: (body: TaxonomyCreate) =>
    request<TaxonomyRead>(TAXONOMY_PATH, { method: "POST", body: JSON.stringify(body) }),

  updateTaxonomy: (kind: TaxonomyKind, code: string, body: TaxonomyUpdate) =>
    request<TaxonomyRead>(`${TAXONOMY_PATH}/${kind}/${encodeURIComponent(code)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  archiveTaxonomy: (kind: TaxonomyKind, code: string) =>
    request<TaxonomyRead>(`${TAXONOMY_PATH}/${kind}/${encodeURIComponent(code)}/archive`, { method: "POST" }),
};

// ---------------------------------------------------------------------------
// Client-side validation (the server stays the authority)
// ---------------------------------------------------------------------------

/**
 * Mirrors `validate_link`: `https://host/...` or a site-relative `/path`.
 * Rejects `javascript:`, `data:`, `http:`, `//host`, backslashes, spaces and
 * credentials in the URL.
 */
export function isSafeLink(value: string): boolean {
  if (!value) return false;
  // eslint-disable-next-line no-control-regex -- rejecting ASCII control characters is the point
  if (/[\s\u0000-\u001f\u007f\\]/.test(value)) return false;
  if (value.startsWith("/")) return !value.startsWith("//");
  try {
    const url = new URL(value);
    return url.protocol === "https:" && Boolean(url.hostname) && !url.username && !url.password;
  } catch {
    return false;
  }
}

const EXTENSION_MIME: Record<string, BrandAssetMime> = {
  png: "image/png",
  webp: "image/webp",
  svg: "image/svg+xml",
};

/** Pre-check before upload: PNG / WebP / SVG and at most 512 KB. Returns the problem or null. */
export function checkAssetFile(file: Pick<File, "name" | "type" | "size">): string | null {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  const type = file.type || EXTENSION_MIME[ext] || "";
  if (!(BRAND_ASSET_MIME_TYPES as readonly string[]).includes(type) || EXTENSION_MIME[ext] !== type) {
    return `${file.name} is not a PNG, WebP or SVG image.`;
  }
  if (file.size === 0) return `${file.name} is empty.`;
  if (file.size > BRAND_ASSET_MAX_BYTES) {
    return `${file.name} is ${Math.ceil(file.size / 1024)} KB; the limit is ${BRAND_ASSET_MAX_BYTES / 1024} KB.`;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Insights taxonomy
// ---------------------------------------------------------------------------

export const TAXONOMY_KINDS = ["intent", "bot_quality", "issue"] as const;
export type TaxonomyKind = (typeof TAXONOMY_KINDS)[number];

/** `CODE_PATTERN`: upper case, starts with a letter, at most 32 characters. */
export const TAXONOMY_CODE_RE = /^[A-Z][A-Z0-9_]{0,31}$/;
export const TAXONOMY_LABEL_MAX = 128;
export const TAXONOMY_GROUP_MAX = 64;
export const TAXONOMY_SORT_MAX = 10_000;

/**
 * Codes the rule stage emits (backend `PROTECTED_CODES`): they cannot be
 * archived (the server answers 409), so the UI disables the action.
 */
export const PROTECTED_TAXONOMY_CODES: Record<TaxonomyKind, ReadonlySet<string>> = {
  intent: new Set(["GRE", "ACK", "OOS", "INJ", "AMB", "OTHER"]),
  bot_quality: new Set(["OK", "CORR", "SCOPE", "PART", "FB", "CLAR", "OFF", "FALSE", "ACK"]),
  issue: new Set(["LLM_FAIL", "OTHER"]),
};

export interface TaxonomyRead {
  id: number;
  kind: TaxonomyKind;
  code: string;
  label: string;
  description: string | null;
  color: string | null;
  group: string | null;
  substantive: boolean;
  handled: boolean;
  sort_order: number;
  is_active: boolean;
  is_system: boolean;
  created_at: string;
  updated_at: string;
}

export interface TaxonomyList {
  items: TaxonomyRead[];
}

export interface TaxonomyCreate {
  kind: TaxonomyKind;
  code: string;
  label: string;
  description?: string | null;
  color?: string | null;
  group?: string | null;
  sort_order?: number;
  substantive?: boolean;
  handled?: boolean;
}

/** Editable fields only: kind and code are immutable. */
export interface TaxonomyUpdate {
  label?: string;
  description?: string | null;
  color?: string | null;
  group?: string | null;
  sort_order?: number;
}

export const TAXONOMY_KEY = "settings/taxonomy";

/** Entries of one kind in display order (sort_order, then code). */
export function taxonomyOfKind(items: readonly TaxonomyRead[], kind: TaxonomyKind): TaxonomyRead[] {
  return items
    .filter((item) => item.kind === kind)
    .sort((a, b) => a.sort_order - b.sort_order || a.code.localeCompare(b.code));
}

/** Options for filters and pickers: active entries only (archived ones disappear). */
export function taxonomyFilterOptions(
  items: readonly TaxonomyRead[],
  kind: TaxonomyKind,
): { value: string; label: string }[] {
  return taxonomyOfKind(items, kind)
    .filter((item) => item.is_active)
    .map((item) => ({ value: item.code, label: item.label }));
}

/** Label for a stored code: archived entries still resolve (historic labels); unknown codes show the code. */
export function taxonomyLabel(items: readonly TaxonomyRead[], kind: TaxonomyKind, code: string): string {
  return items.find((item) => item.kind === kind && item.code === code)?.label ?? code;
}

/** True when `code` already exists for `kind`, archived entries included (codes are never reused). */
export function isDuplicateTaxonomyCode(items: readonly TaxonomyRead[], kind: TaxonomyKind, code: string): boolean {
  return items.some((item) => item.kind === kind && item.code === code);
}
