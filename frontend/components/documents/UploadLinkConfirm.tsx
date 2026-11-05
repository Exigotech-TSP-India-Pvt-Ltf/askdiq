"use client";

import React from "react";
import { AlertTriangle, CheckCircle2, Info, Link2, Loader2, Pencil, RefreshCw, ShieldAlert } from "lucide-react";
import { focusRing, secondaryButton } from "@/components/ui/styles";
import {
  linkSourceLabel,
  previewOutcome,
  replaceTarget,
  type IngestPreview,
} from "@/lib/documents-api";
import { MAX_SOURCE_URL_CHARS, displayUrl } from "@/lib/safe-url";

export interface SourceUrlFieldProps {
  id: string;
  value: string;
  required: boolean;
  disabled: boolean;
  error: string | null;
  /** The error came from the server (announced as an alert). */
  serverError: boolean;
  allowedHosts: readonly string[] | null;
  onChange: (value: string) => void;
  onBlur: () => void;
}

/** The Source URL input with its hint and error (https only, allow-list). */
export function SourceUrlField({
  id,
  value,
  required,
  disabled,
  error,
  serverError,
  allowedHosts,
  onChange,
  onBlur,
}: SourceUrlFieldProps) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-xs font-semibold text-fg">
        Source URL
        <span className="font-normal text-fg-subtle">{required ? " (required)" : " (optional)"}</span>
      </label>
      <input
        id={id}
        type="url"
        inputMode="url"
        autoComplete="url"
        spellCheck={false}
        maxLength={MAX_SOURCE_URL_CHARS}
        value={value}
        disabled={disabled}
        placeholder="https://docs.example.com/page"
        aria-invalid={Boolean(error)}
        aria-required={required}
        aria-describedby={`${id}-hint${error ? ` ${id}-error` : ""}`}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        className={`w-full rounded-lg border border-line bg-surface-card px-3 py-2 text-xs text-fg placeholder:text-fg-subtle aria-invalid:border-status-danger ${focusRing}`}
      />
      <p id={`${id}-hint`} className="text-[11px] text-fg-subtle">
        The page answers link to when they cite this document (https only
        {allowedHosts?.length ? `; allowed domains: ${allowedHosts.join(", ")}` : ""}).
      </p>
      {error ? (
        <p id={`${id}-error`} role={serverError ? "alert" : undefined} className="text-[11px] font-medium text-status-danger-fg">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export interface UploadLinkConfirmProps {
  preview: IngestPreview;
  /** Editing the link (always true when nothing was detected). */
  editing: boolean;
  linkConfirmed: boolean;
  replaceConfirmed: boolean;
  /** Re-running the preview for an edited link. */
  checking: boolean;
  disabled: boolean;
  /** The edited link is valid and differs from the previewed one. */
  canCheck: boolean;
  field: Omit<SourceUrlFieldProps, "disabled">;
  onConfirmLink: () => void;
  onEdit: () => void;
  onCancelEdit: () => void;
  onCheck: () => void;
  onReplaceConfirmedChange: (value: boolean) => void;
}

/**
 * Before ingesting: the page link the document will get (and where it came
 * from) to confirm or edit, the existing document it matches and what will
 * happen to it, and the preview warnings.
 */
export function UploadLinkConfirm({
  preview,
  editing,
  linkConfirmed,
  replaceConfirmed,
  checking,
  disabled,
  canCheck,
  field,
  onConfirmLink,
  onEdit,
  onCancelEdit,
  onCheck,
  onReplaceConfirmedChange,
}: UploadLinkConfirmProps) {
  const link = preview.detected_link;
  const target = replaceTarget(preview);

  return (
    <div className="space-y-3 text-xs" data-testid="upload-link-confirm">
      <section aria-label="Page link" className="space-y-2 rounded-lg border border-line bg-surface-muted/50 p-3">
        <h3 className="flex items-center gap-1.5 font-semibold text-fg">
          <Link2 className="h-3.5 w-3.5 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" />
          Page link
        </h3>
        {editing ? (
          <>
            <SourceUrlField {...field} disabled={disabled || checking} />
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={onCheck} disabled={disabled || checking || !canCheck} className={secondaryButton}>
                {checking ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" strokeWidth={1.75} aria-hidden="true" />
                ) : (
                  <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                )}
                Use this link
              </button>
              {link ? (
                <button type="button" onClick={onCancelEdit} disabled={disabled || checking} className={secondaryButton}>
                  Keep detected link
                </button>
              ) : null}
            </div>
          </>
        ) : link ? (
          <>
            <p className="break-all font-medium text-fg" title={link} data-testid="upload-detected-link">
              {displayUrl(link)}
            </p>
            <p className="text-fg-subtle">From {linkSourceLabel(preview.link_source)}.</p>
            <div className="flex flex-wrap items-center gap-2">
              {linkConfirmed ? (
                <span className="inline-flex items-center gap-1 font-medium text-status-ok-fg" role="status">
                  <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                  Link confirmed
                </span>
              ) : (
                <button type="button" onClick={onConfirmLink} disabled={disabled} className={secondaryButton}>
                  <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                  Confirm link
                </button>
              )}
              <button type="button" onClick={onEdit} disabled={disabled} className={secondaryButton}>
                <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                Edit link
              </button>
            </div>
          </>
        ) : null}
      </section>

      <section
        aria-label="What will happen"
        className={`space-y-2 rounded-lg border p-3 ${
          target ? "border-status-warn/40 bg-status-warn-bg" : "border-line bg-surface-muted/50"
        }`}
      >
        <p className="flex items-start gap-2 text-fg">
          {target ? (
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-status-warn" strokeWidth={1.75} aria-hidden="true" />
          ) : (
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-status-info" strokeWidth={1.75} aria-hidden="true" />
          )}
          <span>
            Will be stored as <strong>{preview.canonical_name}</strong>. {previewOutcome(preview)}
          </span>
        </p>
        {target ? (
          <label className="flex items-start gap-2 font-medium text-fg">
            <input
              type="checkbox"
              checked={replaceConfirmed}
              disabled={disabled}
              onChange={(e) => onReplaceConfirmedChange(e.target.checked)}
              className="mt-0.5 h-3.5 w-3.5 rounded-sm border-line accent-brand-600"
            />
            Replace {target.name} with {preview.canonical_name}
          </label>
        ) : null}
        {preview.quarantined ? (
          <p className="flex items-start gap-2 text-status-warn-fg">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden="true" />
            Suspicious instructions found: the document will be quarantined.
          </p>
        ) : null}
      </section>

      {preview.warnings.length ? (
        <ul className="list-disc space-y-0.5 pl-5 text-status-warn-fg" aria-label="Conversion warnings">
          {preview.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
