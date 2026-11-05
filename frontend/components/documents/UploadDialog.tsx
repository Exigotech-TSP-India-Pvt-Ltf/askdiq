"use client";

import React, { useCallback, useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { AnimatePresence, m } from "motion/react";
import {
  AlertTriangle,
  CheckCircle2,
  FileUp,
  Info,
  Loader2,
  ShieldAlert,
  Upload,
  X,
} from "lucide-react";
import { Modal } from "@/components/ui/Modal";
import { fadeUp, progressFill, useMotionVariants } from "@/lib/motion";
import { focusRing, iconButton, primaryButton, secondaryButton } from "@/components/ui/styles";
import { FormatBadge } from "@/components/documents/FormatBadge";
import { SourceUrlField, UploadLinkConfirm, type SourceUrlFieldProps } from "@/components/documents/UploadLinkConfirm";
import {
  MAX_UPLOAD_BYTES,
  UPLOAD_ACCEPT,
  changeReasonText,
  fileExtension,
  formatBytes,
  isLinkInUseError,
  isSourceUrlError,
  previewUpload,
  replaceTarget,
  replacesOtherDocument,
  strategyLabel,
  uploadDocument,
  uploadErrorMessage,
  validateUploadFile,
  type IngestPreview,
  type UploadResult,
} from "@/lib/documents-api";
import { useApiQuery } from "@/lib/query-cache";
import { KNOWLEDGE_SETTINGS_KEY, settingsApi, type KnowledgeSettingsRead } from "@/lib/settings-api";
import { displayUrl, safeHttpsUrl, validateSourceUrl } from "@/lib/safe-url";

/** Primary action that clearly reads as unavailable until a file passed the check (VA-51). */
const PRIMARY_DISABLED =
  "disabled:bg-surface-muted disabled:text-fg-subtle disabled:shadow-none disabled:opacity-100";

type Phase = "idle" | "previewing" | "preview" | "ingesting" | "done";

const STEPS = ["Check file", "Preview chunks", "Standardize & index"] as const;
const STEP_OF: Record<Phase, number> = { idle: 0, previewing: 1, preview: 1, ingesting: 2, done: 3 };

export interface UploadDialogProps {
  open: boolean;
  token: string;
  onClose: () => void;
  /** Called after a real (non-preview) ingest succeeded. */
  onUploaded: (result: UploadResult) => void;
}

function confirmLabel(status: UploadResult["status"]): string {
  if (status === "new") return "Ingest document";
  if (status === "unchanged") return "Re-check document";
  return "Apply changes";
}

function linkConfirmLabel(p: IngestPreview): string {
  if (replacesOtherDocument(p)) return "Replace document";
  if (p.content_status === "unchanged") return "Re-check document";
  if (p.content_status === "changed") return "Apply changes";
  return "Ingest document";
}

function previewSummary(p: UploadResult): string {
  if (p.status === "unchanged") {
    return "Content is identical to the stored version: nothing is re-embedded. Re-checking stores the canonical Markdown if it is missing.";
  }
  if (p.status === "new") return `New document: ${p.chunk_count} chunk${p.chunk_count === 1 ? "" : "s"} will be embedded.`;
  const why = changeReasonText(p.change_reasons);
  if (!p.chunks_added && !p.chunks_removed) return `${why}. No chunk changed: nothing is re-embedded.`;
  return `${why}: ${p.chunks_added} new, ${p.chunks_kept} unchanged and ${p.chunks_removed} removed chunks.`;
}

/** The stored link after ingest (opened only when it is a safe https URL). */
function FinalLink({ url }: { url: string | null }) {
  if (!url) return <span className="text-fg-subtle">No link</span>;
  const href = safeHttpsUrl(url);
  if (!href) {
    return (
      <span className="block truncate" title={url}>
        {displayUrl(url)}
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={url}
      className={`block truncate font-medium text-brand-700 underline-offset-2 hover:underline dark:text-brand-300 ${focusRing}`}
    >
      {displayUrl(url)}
    </a>
  );
}

export function UploadDialog({ open, token, onClose, onUploaded }: UploadDialogProps) {
  const titleId = useId();
  const descId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const pickRef = useRef<HTMLButtonElement>(null);
  const variants = useMotionVariants(fadeUp);
  const [phase, setPhase] = useState<Phase>("idle");
  const [file, setFile] = useState<File | null>(null);
  /** Dry-run result: only used when the backend has no `/ingest/preview` yet. */
  const [preview, setPreview] = useState<UploadResult | null>(null);
  const [linkPreview, setLinkPreview] = useState<IngestPreview | null>(null);
  const [editingLink, setEditingLink] = useState(false);
  const [linkConfirmed, setLinkConfirmed] = useState(false);
  const [replaceConfirmed, setReplaceConfirmed] = useState(false);
  const [checkingLink, setCheckingLink] = useState(false);
  const [linkInUse, setLinkInUse] = useState<string | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [sourceUrl, setSourceUrl] = useState("");
  const [urlBlurred, setUrlBlurred] = useState(false);
  const [urlServerError, setUrlServerError] = useState<string | null>(null);
  const urlId = useId();
  const busy = phase === "previewing" || phase === "ingesting" || checkingLink;

  // Allow-list and the required-URL switch; loaded only while the dialog is open.
  // A failure (or an older backend) leaves the server as the only check.
  const knowledge = useApiQuery<KnowledgeSettingsRead>(
    open ? KNOWLEDGE_SETTINGS_KEY : null,
    (signal) => settingsApi.getKnowledgeSettings(signal),
    { revalidateOnFocus: false },
  );
  const allowedHosts = knowledge.data?.allowed_hosts ?? null;
  // Required only for a new document: a re-upload keeps its stored link.
  const isNewDocument = linkPreview
    ? linkPreview.content_status === "new" && !linkPreview.existing_document
    : preview?.status === "new";
  const urlRequired = Boolean(knowledge.data?.require_source_url) && isNewDocument;
  const urlCheck = validateSourceUrl(sourceUrl, { allowedHosts, required: urlRequired });
  const urlError = urlServerError ?? (!urlCheck.ok && (sourceUrl.trim() !== "" || urlBlurred) ? urlCheck.error : null);

  // With a link preview the link sent is the one the preview ran for, confirmed
  // by the admin (or none when nothing was detected and none is required).
  const detectedLink = linkPreview?.detected_link ?? null;
  const linkChecked = sourceUrl.trim() === (detectedLink ?? "");
  const canCheckLink = urlCheck.ok && urlCheck.url !== null && !linkChecked;
  const linkReady = linkPreview
    ? urlCheck.ok && linkChecked && (detectedLink ? !editingLink && linkConfirmed : true)
    : urlCheck.ok;
  const needsReplace = linkPreview ? replacesOtherDocument(linkPreview) : false;
  const ready =
    phase === "preview" && Boolean(preview || linkPreview) && linkReady && (!needsReplace || replaceConfirmed);

  const urlField: Omit<SourceUrlFieldProps, "disabled"> = {
    id: urlId,
    value: sourceUrl,
    required: urlRequired,
    error: urlError,
    serverError: Boolean(urlServerError),
    allowedHosts,
    onChange: (value) => {
      setSourceUrl(value);
      setUrlServerError(null);
    },
    onBlur: () => setUrlBlurred(true),
  };

  const reset = useCallback(() => {
    setPhase("idle");
    setFile(null);
    setPreview(null);
    setLinkPreview(null);
    setEditingLink(false);
    setLinkConfirmed(false);
    setReplaceConfirmed(false);
    setCheckingLink(false);
    setLinkInUse(null);
    setResult(null);
    setError(null);
    setSourceUrl("");
    setUrlBlurred(false);
    setUrlServerError(null);
  }, []);

  useEffect(() => {
    if (!open) reset();
  }, [open, reset]);

  async function choose(next: File | undefined) {
    if (!next || busy) return;
    reset();
    setFile(next);
    const invalid = validateUploadFile(next);
    if (invalid) {
      setError(invalid);
      return;
    }
    setPhase("previewing");
    try {
      const previewed = await previewUpload(token, next);
      if (previewed) {
        applyLinkPreview(previewed, false);
        setPhase("preview");
        return;
      }
      const checked = await uploadDocument(token, next, { dryRun: true });
      setPreview(checked);
      // Pre-fill from the file's metadata (or the stored link of a re-upload).
      const known = checked.detected_source_url ?? checked.source_url ?? null;
      if (known) setSourceUrl(known);
      setPhase("preview");
    } catch (err) {
      setError(uploadErrorMessage(err));
      setPhase("idle");
    }
  }

  /** Shows a link preview; `confirmed` when the admin already chose its link. */
  function applyLinkPreview(p: IngestPreview, confirmed: boolean) {
    setLinkPreview(p);
    setPreview(null);
    setSourceUrl(p.detected_link ?? "");
    setEditingLink(!p.detected_link);
    setLinkConfirmed(confirmed && Boolean(p.detected_link));
    setReplaceConfirmed(false);
    setUrlServerError(null);
  }

  /** Re-runs the preview for an edited link (it may match another document). */
  async function checkLink() {
    if (!file || !urlCheck.ok || !urlCheck.url) return;
    setCheckingLink(true);
    setError(null);
    setUrlServerError(null);
    setLinkInUse(null);
    try {
      const previewed = await previewUpload(token, file, urlCheck.url);
      if (previewed) applyLinkPreview(previewed, true);
    } catch (err) {
      if (isSourceUrlError(err)) setUrlServerError(err.message);
      else setError(uploadErrorMessage(err));
    } finally {
      setCheckingLink(false);
    }
  }

  /** 409 `document_link_in_use`: re-preview to name the document and ask to replace it. */
  async function askToReplace(upload: File, url: string | null, message: string) {
    try {
      const previewed = await previewUpload(token, upload, url);
      if (previewed && replacesOtherDocument(previewed)) {
        applyLinkPreview(previewed, true);
        setLinkInUse(message);
        return;
      }
    } catch {
      // fall back to the server message
    }
    setError(message);
  }

  async function ingest() {
    if (!file || !urlCheck.ok) return;
    setPhase("ingesting");
    setError(null);
    setUrlServerError(null);
    setLinkInUse(null);
    const replaceDocumentId = linkPreview && needsReplace && replaceConfirmed ? (replaceTarget(linkPreview)?.id ?? null) : null;
    try {
      const res = await uploadDocument(token, file, { sourceUrl: urlCheck.url, replaceDocumentId });
      setResult(res);
      setPhase("done");
      onUploaded(res);
    } catch (err) {
      if (isSourceUrlError(err)) setUrlServerError(err.message);
      else if (isLinkInUseError(err)) await askToReplace(file, urlCheck.url, err.message);
      else setError(uploadErrorMessage(err));
      setPhase("preview");
    }
  }

  const step = STEP_OF[phase];
  const isDocx = file ? fileExtension(file.name) === "docx" : false;

  return (
    <Modal
      open={open}
      onClose={busy ? undefined : onClose}
      labelledBy={titleId}
      describedBy={descId}
      initialFocusRef={pickRef}
      className="max-w-lg"
    >
      <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div>
          <h2 id={titleId} className="text-sm font-semibold text-fg">
            Upload document
          </h2>
          <p id={descId} className="mt-0.5 text-xs text-fg-subtle">
            .md, .markdown or .docx up to {formatBytes(MAX_UPLOAD_BYTES)}. Word files are converted; every document is
            stored as canonical Markdown, chunked and embedded.
          </p>
        </div>
        <button type="button" onClick={onClose} disabled={busy} className={iconButton} aria-label="Close upload dialog">
          <X className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        </button>
      </div>

      <div className="space-y-4 px-5 py-4">
        <ol className="grid grid-cols-3 gap-2" aria-label="Upload progress">
          {STEPS.map((label, i) => {
            const state = step > i + 1 || phase === "done" ? "done" : step === i + 1 || (i === 0 && file) ? "current" : "todo";
            return (
              <li key={label} className="space-y-1" aria-current={state === "current" ? "step" : undefined}>
                <div className="h-1 overflow-hidden rounded-full bg-line-strong/50 dark:bg-line-strong">
                  <m.div
                    className="h-full origin-left rounded-full bg-brand-600 dark:bg-brand-400"
                    initial={false}
                    animate={{ scaleX: state === "todo" ? 0 : state === "current" && busy && step === i + 1 ? 0.5 : 1 }}
                    transition={progressFill}
                  />
                </div>
                <span className={`block text-[11px] ${state === "todo" ? "text-fg-subtle" : "font-medium text-fg"}`}>{label}</span>
              </li>
            );
          })}
        </ol>

        {phase !== "done" ? (
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              void choose(e.dataTransfer.files?.[0]);
            }}
            className={`flex flex-col items-center gap-2 rounded-xl border-2 border-dashed px-4 py-6 text-center transition-colors duration-150 ${
              dragging ? "border-brand-500 bg-brand-50 dark:bg-brand-950/40" : "border-line bg-surface-muted/40"
            }`}
          >
            <FileUp className="h-6 w-6 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" />
            <p className="text-xs text-fg-muted">Drop a file here, or</p>
            <button
              ref={pickRef}
              type="button"
              disabled={busy}
              onClick={() => inputRef.current?.click()}
              className={secondaryButton}
            >
              <Upload className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              Choose file
            </button>
            <input
              ref={inputRef}
              type="file"
              accept={UPLOAD_ACCEPT}
              className="sr-only"
              tabIndex={-1}
              aria-label="Document file"
              data-testid="document-file-input"
              onChange={(e) => {
                const picked = e.target.files?.[0];
                e.target.value = "";
                void choose(picked);
              }}
            />
          </div>
        ) : null}

        {file ? (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-2 text-xs">
            <div className="min-w-0">
              <p className="truncate font-medium text-fg" title={file.name}>
                {file.name}
              </p>
              <p className="text-fg-subtle">
                {formatBytes(file.size)} · {isDocx ? "Word document, will be converted to Markdown" : "Markdown"}
              </p>
            </div>
            <FormatBadge convertedFrom={isDocx ? "docx" : "md"} showCaption={false} />
          </div>
        ) : null}

        {linkPreview && (phase === "preview" || phase === "ingesting") ? (
          <UploadLinkConfirm
            preview={linkPreview}
            editing={editingLink}
            linkConfirmed={linkConfirmed}
            replaceConfirmed={replaceConfirmed}
            checking={checkingLink}
            disabled={phase === "ingesting"}
            canCheck={canCheckLink}
            field={urlField}
            onConfirmLink={() => setLinkConfirmed(true)}
            onEdit={() => setEditingLink(true)}
            onCancelEdit={() => {
              setSourceUrl(detectedLink ?? "");
              setUrlServerError(null);
              setEditingLink(false);
            }}
            onCheck={() => void checkLink()}
            onReplaceConfirmedChange={setReplaceConfirmed}
          />
        ) : null}

        {linkInUse ? (
          <p role="alert" className="flex items-start gap-2 rounded-lg bg-status-warn-bg px-3 py-2 text-xs text-status-warn-fg">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
            <span>{linkInUse} Confirm the replacement above to continue.</span>
          </p>
        ) : null}

        {preview && (phase === "preview" || phase === "ingesting") ? (
          <SourceUrlField {...urlField} disabled={phase === "ingesting"} />
        ) : null}

        <AnimatePresence mode="wait" initial={false}>
          {busy ? (
            <m.p
              key="busy"
              variants={variants}
              initial="hidden"
              animate="visible"
              exit="exit"
              role="status"
              className="flex items-center gap-2 text-xs text-fg-muted"
            >
              <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" strokeWidth={1.75} aria-hidden="true" />
              {checkingLink
                ? "Checking the link…"
                : phase === "previewing"
                ? isDocx
                  ? "Converting to Markdown and previewing chunks…"
                  : "Normalizing Markdown and previewing chunks…"
                : "Standardizing, chunking and embedding…"}
            </m.p>
          ) : null}

          {phase === "preview" && preview ? (
            <m.div
              key="preview"
              variants={variants}
              initial="hidden"
              animate="visible"
              exit="exit"
              className="space-y-2 rounded-lg border border-line bg-surface-muted/50 p-3 text-xs"
            >
              <p className="flex items-start gap-2 text-fg">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-status-info" strokeWidth={1.75} aria-hidden="true" />
                <span>
                  Will be stored as <strong>{preview.source_name}</strong>. {previewSummary(preview)}
                </span>
              </p>
              {preview.quarantined ? (
                <p className="flex items-start gap-2 text-status-warn-fg">
                  <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
                  Suspicious instructions found: the document will be quarantined.
                </p>
              ) : null}
              {preview.warnings.length ? (
                <ul className="list-disc space-y-0.5 pl-5 text-status-warn-fg" aria-label="Conversion warnings">
                  {preview.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              ) : null}
            </m.div>
          ) : null}

          {phase === "done" && result ? (
            <m.div
              key="done"
              variants={variants}
              initial="hidden"
              animate="visible"
              exit="exit"
              role="status"
              className={`space-y-2 rounded-lg border p-3 text-xs ${
                result.quarantined ? "border-status-warn/40 bg-status-warn-bg" : "border-status-ok/40 bg-status-ok-bg"
              }`}
            >
              <p className="flex items-start gap-2 font-semibold text-fg">
                {result.quarantined ? (
                  <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-status-warn" strokeWidth={1.75} aria-hidden="true" />
                ) : (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-status-ok" strokeWidth={1.75} aria-hidden="true" />
                )}
                <span>
                  {result.converted_from === "docx" ? "Converted to Markdown" : "Normalized Markdown"},{" "}
                  {result.chunk_count} chunk{result.chunk_count === 1 ? "" : "s"}
                  {result.quarantined ? " (quarantined)" : ""}
                </span>
              </p>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-fg-muted">
                <dt>Stored as</dt>
                <dd className="truncate font-medium text-fg">{result.source_name}</dd>
                <dt>Linked to</dt>
                <dd className="min-w-0 text-fg" data-testid="upload-final-link">
                  <FinalLink url={result.source_url ?? null} />
                </dd>
                <dt>Status</dt>
                <dd className="font-medium text-fg">{result.status}</dd>
                <dt>Chunks</dt>
                <dd className="text-fg">
                  +{result.chunks_added} added · {result.chunks_kept} kept · −{result.chunks_removed} removed
                </dd>
                {result.change_reasons?.length ? (
                  <>
                    <dt>Reason</dt>
                    <dd className="text-fg">{changeReasonText(result.change_reasons)}</dd>
                  </>
                ) : null}
                {result.strategy_used ? (
                  <>
                    <dt>Chunking</dt>
                    <dd className="text-fg">{strategyLabel(result.strategy_used)}</dd>
                  </>
                ) : null}
                {typeof result.canonical_size_bytes === "number" ? (
                  <>
                    <dt>Canonical size</dt>
                    <dd className="text-fg">{formatBytes(result.canonical_size_bytes)}</dd>
                  </>
                ) : null}
              </dl>
              {result.warnings.length ? (
                <p className="flex items-start gap-2 text-status-warn-fg">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
                  {result.warnings.length} conversion warning{result.warnings.length === 1 ? "" : "s"} (see the document
                  page)
                </p>
              ) : null}
            </m.div>
          ) : null}
        </AnimatePresence>

        {error ? (
          <p role="alert" className="flex items-start gap-2 rounded-lg bg-status-danger-bg px-3 py-2 text-xs text-status-danger-fg">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
            {error}
          </p>
        ) : null}
      </div>

      <div className="flex justify-end gap-2 border-t border-line px-5 py-3">
        {phase === "done" && result ? (
          <>
            <button type="button" onClick={reset} className={secondaryButton}>
              Upload another
            </button>
            <Link href={`/documents/${encodeURIComponent(result.document_id)}`} onClick={onClose} className={`${primaryButton} ${focusRing}`}>
              Open document
            </Link>
          </>
        ) : (
          <>
            <button type="button" onClick={onClose} disabled={busy} className={secondaryButton}>
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void ingest()}
              disabled={!ready}
              className={`${primaryButton} ${PRIMARY_DISABLED}`}
            >
              {phase === "ingesting" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" strokeWidth={1.75} aria-hidden="true" />
              ) : null}
              {linkPreview ? linkConfirmLabel(linkPreview) : preview ? confirmLabel(preview.status) : "Ingest document"}
            </button>
          </>
        )}
      </div>
    </Modal>
  );
}
