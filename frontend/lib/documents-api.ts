/**
 * Typed client and pure helpers for the Documents inventory and detail pages.
 *
 * Backend: `backend/app/api/routes/documents.py` + `ingest.py`. Every field
 * beyond the original `DocumentSummary` is optional so an older backend still
 * renders; `null` means "not recorded for this document" and is shown as "—",
 * never estimated. Requests use the same `/api/proxy` route and `ApiError` as
 * `lib/api.ts`.
 */
import { ApiError } from "./api";
import type { DocumentSummary, IngestResult } from "./types";

const API_BASE_URL = "/api/proxy";
const CLIENT_ID_STORAGE_KEY = "deployiq.client_id";

/** Upload limits, mirrored from the server (`ingest_max_file_bytes`, `naming.py`). */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const UPLOAD_EXTENSIONS = ["md", "markdown", "docx"] as const;
export const UPLOAD_ACCEPT = ".md,.markdown,.docx";

export const SHORT_HASH_CHARS = 12;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type DocumentStatus = "active" | "quarantined" | "empty";

/** Row of `GET /documents` (inventory fields are additive). */
export interface DocumentInventoryItem extends DocumentSummary {
  content_sha256?: string | null;
  canonical_format?: string;
  standardized?: boolean;
  converter?: string | null;
  converter_version?: string | null;
  original_size_bytes?: number | null;
  canonical_size_bytes?: number | null;
  canonical_available?: boolean;
  total_chunk_chars?: number;
  chunk_strategies?: string[];
  chunking_strategy?: string | null;
  embedding_model?: string | null;
  embedding_dim?: number | null;
  ingested_at?: string | null;
  ingested_by?: string | null;
  status?: DocumentStatus | string;
  pii_redacted?: string[] | null;
  pipeline_version?: string | null;
  labels?: Record<string, string>;
}

export interface IngestEvent {
  at: string;
  actor: string;
  status: string | null;
  original_filename: string | null;
  converted_from: string | null;
  content_hash: string | null;
  chunks_added: number | null;
  chunks_kept: number | null;
  chunks_removed: number | null;
  total_chunks: number | null;
  embedding_tokens: number | null;
  warnings: string[];
  pii_redacted: string[] | null;
  quarantined: boolean | null;
}

export interface ChunkSizeStats {
  count: number;
  total_chars: number;
  min_chars: number | null;
  max_chars: number | null;
  avg_chars: number | null;
}

/** `GET /documents/{id}`. */
export interface DocumentDetail extends DocumentInventoryItem {
  history: IngestEvent[];
  history_truncated: boolean;
  chunk_stats: ChunkSizeStats;
}

/** `GET /documents/{id}/canonical`. */
export interface CanonicalMarkdown {
  document_id: string;
  source_name: string;
  format: string;
  available: boolean;
  content: string | null;
  size_bytes: number | null;
  content_sha256: string | null;
}

/** `POST /ingest/file` (fields beyond `IngestResult` are additive). */
export interface UploadResult extends IngestResult {
  document_id: string;
  chunk_count: number;
  strategy_used?: string;
  dry_run?: boolean;
  original_size_bytes?: number | null;
  canonical_size_bytes?: number | null;
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

function getClientId(): string {
  if (typeof window === "undefined") return "server";
  try {
    let id = window.localStorage.getItem(CLIENT_ID_STORAGE_KEY);
    if (!id) {
      id = crypto.randomUUID();
      window.localStorage.setItem(CLIENT_ID_STORAGE_KEY, id);
    }
    return id;
  } catch {
    return "anonymous";
  }
}

async function errorFrom(res: Response): Promise<ApiError> {
  let detail: string = res.statusText || `Request failed (${res.status})`;
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object") {
      const { detail: d, title } = body as { detail?: unknown; title?: unknown };
      if (typeof d === "string") detail = d;
      else if (typeof title === "string") detail = title;
    }
  } catch {
    // no JSON body
  }
  return new ApiError(detail, res.status);
}

async function send<T>(
  path: string,
  { token, method = "GET", body, signal }: { token: string; method?: string; body?: FormData; signal?: AbortSignal },
): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    body,
    signal,
    headers: {
      ...(body ? {} : { "Content-Type": "application/json" }),
      "X-Client-Id": getClientId(),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  if (!res.ok) throw await errorFrom(res);
  return (await res.json()) as T;
}

/** 405 / 501: this backend predates the endpoint (render without it). */
function isNotDeployed(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 405 || err.status === 501);
}

const seg = encodeURIComponent;

export function listDocumentInventory(token: string, signal?: AbortSignal) {
  return send<DocumentInventoryItem[]>("/documents", { token, signal });
}

/** Detail with history; `null` when the backend has no detail endpoint yet. */
export async function getDocumentDetail(token: string, id: string, signal?: AbortSignal) {
  try {
    return await send<DocumentDetail>(`/documents/${seg(id)}`, { token, signal });
  } catch (err) {
    if (isNotDeployed(err)) return null;
    throw err;
  }
}

/** Canonical Markdown; `null` when the backend has no canonical endpoint yet. */
export async function getCanonicalMarkdown(token: string, id: string, signal?: AbortSignal) {
  try {
    return await send<CanonicalMarkdown>(`/documents/${seg(id)}/canonical`, { token, signal });
  } catch (err) {
    if (isNotDeployed(err) || (err instanceof ApiError && err.status === 404)) return null;
    throw err;
  }
}

/** `POST /ingest/file`. Flags go as form fields (what the endpoint reads). */
export function uploadDocument(token: string, file: File, options: { dryRun?: boolean; force?: boolean } = {}) {
  const form = new FormData();
  form.append("file", file);
  if (options.dryRun) form.append("dry_run", "true");
  if (options.force) form.append("force", "true");
  return send<UploadResult>("/ingest/file", { token, method: "POST", body: form });
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

export function fileExtension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot + 1).toLowerCase();
}

/** Client-side check before upload (the server validates the same rules). */
export function validateUploadFile(file: Pick<File, "name" | "size">): string | null {
  if (!(UPLOAD_EXTENSIONS as readonly string[]).includes(fileExtension(file.name))) {
    return "Only .md, .markdown and .docx files can be uploaded.";
  }
  if (file.size === 0) return "The file is empty.";
  if (file.size > MAX_UPLOAD_BYTES) return "The file is larger than the 10 MB upload limit.";
  return null;
}

/** Message for an upload error status (problem+json detail as fallback). */
export function uploadErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 413) return "The file is larger than the upload limit.";
    if (err.status === 415) return "Unsupported file type. Only .md, .markdown and .docx are accepted.";
    if (err.status === 422) return `The document could not be read: ${err.message}`;
    if (err.status === 429) return "Too many uploads. Wait a moment and try again.";
    if (err.status === 403) return "You don't have permission to upload documents.";
    return err.message;
  }
  if (err instanceof Error) return `Upload failed: ${err.message}`;
  return "Upload failed. Check your connection and try again.";
}

const BYTE_UNITS = ["B", "KB", "MB", "GB"] as const;
const BYTES_PER_UNIT = 1024;

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  let value = bytes;
  let unit = 0;
  while (value >= BYTES_PER_UNIT && unit < BYTE_UNITS.length - 1) {
    value /= BYTES_PER_UNIT;
    unit += 1;
  }
  return unit === 0 ? `${value} B` : `${value.toFixed(value >= 10 ? 0 : 1)} ${BYTE_UNITS[unit]}`;
}

export function shortHash(hash: string | null | undefined): string | null {
  return hash ? hash.slice(0, SHORT_HASH_CHARS) : null;
}

/** Original upload format: `docx` or `md`. */
export function originalFormat(doc: Pick<DocumentInventoryItem, "converted_from" | "original_filename">): "docx" | "md" {
  if (doc.converted_from === "docx") return "docx";
  if (!doc.converted_from && doc.original_filename && fileExtension(doc.original_filename) === "docx") return "docx";
  return "md";
}

export function isStandardized(doc: DocumentInventoryItem): boolean {
  return typeof doc.standardized === "boolean" ? doc.standardized : originalFormat(doc) !== "md";
}

export function documentStatus(doc: DocumentInventoryItem): DocumentStatus {
  if (doc.status === "active" || doc.status === "quarantined" || doc.status === "empty") return doc.status;
  if (doc.quarantined) return "quarantined";
  return doc.chunk_count === 0 ? "empty" : "active";
}

/** When the document's current content was last ingested. */
export function ingestedAt(doc: DocumentInventoryItem): string {
  return doc.ingested_at || doc.updated_at || doc.created_at;
}

export const FORMAT_FILTERS = ["all", "md", "docx"] as const;
export type FormatFilter = (typeof FORMAT_FILTERS)[number];
export const STATUS_FILTERS = ["all", "active", "quarantined", "empty"] as const;
export type StatusFilter = (typeof STATUS_FILTERS)[number];
export const WARNING_FILTERS = ["all", "with", "without"] as const;
export type WarningFilter = (typeof WARNING_FILTERS)[number];

export const INVENTORY_SORTS = [
  "ingested_desc",
  "ingested_asc",
  "name_asc",
  "name_desc",
  "chunks_desc",
  "chunks_asc",
  "size_desc",
  "size_asc",
] as const;
export type InventorySort = (typeof INVENTORY_SORTS)[number];
export const DEFAULT_INVENTORY_SORT: InventorySort = "ingested_desc";

export function isOneOf<T extends string>(values: readonly T[], value: unknown): value is T {
  return typeof value === "string" && (values as readonly string[]).includes(value);
}

export interface InventoryFilters {
  q: string;
  format: FormatFilter;
  status: StatusFilter;
  warnings: WarningFilter;
}

function sizeOf(doc: DocumentInventoryItem): number {
  return doc.canonical_size_bytes ?? doc.original_size_bytes ?? doc.total_chunk_chars ?? -1;
}

const time = (value: string): number => {
  const t = new Date(value).getTime();
  return Number.isNaN(t) ? 0 : t;
};

/** Search (name, original file, id, hash, labels), filter and sort the inventory. */
export function filterInventory(
  docs: readonly DocumentInventoryItem[],
  filters: InventoryFilters,
  sort: InventorySort,
): DocumentInventoryItem[] {
  const needle = filters.q.trim().toLowerCase();
  const out = docs.filter((d) => {
    if (needle) {
      const haystack = [
        d.source_name,
        d.original_filename ?? "",
        d.document_id,
        d.content_sha256 ?? d.content_hash ?? "",
        ...Object.values(d.labels ?? {}),
      ]
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    if (filters.format !== "all" && originalFormat(d) !== filters.format) return false;
    if (filters.status !== "all" && documentStatus(d) !== filters.status) return false;
    const warnings = d.warnings?.length ?? 0;
    if (filters.warnings === "with" && warnings === 0) return false;
    if (filters.warnings === "without" && warnings > 0) return false;
    return true;
  });
  const byName = (a: DocumentInventoryItem, b: DocumentInventoryItem) => a.source_name.localeCompare(b.source_name);
  const compare: Record<InventorySort, (a: DocumentInventoryItem, b: DocumentInventoryItem) => number> = {
    ingested_desc: (a, b) => time(ingestedAt(b)) - time(ingestedAt(a)) || byName(a, b),
    ingested_asc: (a, b) => time(ingestedAt(a)) - time(ingestedAt(b)) || byName(a, b),
    name_asc: byName,
    name_desc: (a, b) => byName(b, a),
    chunks_desc: (a, b) => b.chunk_count - a.chunk_count || byName(a, b),
    chunks_asc: (a, b) => a.chunk_count - b.chunk_count || byName(a, b),
    size_desc: (a, b) => sizeOf(b) - sizeOf(a) || byName(a, b),
    size_asc: (a, b) => sizeOf(a) - sizeOf(b) || byName(a, b),
  };
  return out.sort(compare[sort]);
}

/** Human label for a chunking strategy id (`structure_aware` -> "Structure aware"). */
export function strategyLabel(strategy: string | null | undefined): string {
  if (!strategy) return "—";
  const text = strategy.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
