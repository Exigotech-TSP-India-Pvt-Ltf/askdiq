"use client";

import React from "react";
import { ArrowRight, FileCode, FileText } from "lucide-react";

export interface FormatBadgeProps {
  /** Original upload format: `docx` or `md` (anything else is shown as Markdown). */
  convertedFrom?: string | null;
  originalFilename?: string | null;
  originalSha256?: string | null;
  converter?: string | null;
  /** Show the "Standardized to Markdown" / "Native Markdown" caption. */
  showCaption?: boolean;
  className?: string;
}

const chip = "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[11px] font-semibold";

/**
 * Original format -> canonical format. Every document is served as canonical
 * Markdown; a .docx upload is converted ("Standardized to Markdown").
 */
export function FormatBadge({
  convertedFrom,
  originalFilename,
  originalSha256,
  converter,
  showCaption = true,
  className = "",
}: FormatBadgeProps) {
  const isDocx = convertedFrom?.toLowerCase() === "docx";
  const title = isDocx
    ? [
        `Standardized to Markdown from ${originalFilename || "a .docx upload"}`,
        converter ? `Converter: ${converter}` : null,
        originalSha256 ? `Original SHA-256: ${originalSha256}` : null,
      ]
        .filter(Boolean)
        .join("\n")
    : "Uploaded as Markdown; normalized, not converted";

  return (
    <span className={`inline-flex flex-col gap-0.5 ${className}`} title={title}>
      <span className="inline-flex items-center gap-1">
        {isDocx ? (
          <>
            <span className={`${chip} border-status-info/40 bg-status-info-bg text-status-info-fg`}>
              <FileText className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
              .docx
            </span>
            <ArrowRight className="h-3 w-3 text-fg-subtle" strokeWidth={1.75} aria-label="converted to" />
          </>
        ) : null}
        <span className={`${chip} border-line bg-surface-muted text-fg`}>
          <FileCode className="h-3 w-3 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" />
          .md
        </span>
      </span>
      {showCaption ? (
        <span className={`text-[10px] font-medium ${isDocx ? "text-brand-700 dark:text-brand-300" : "text-fg-subtle"}`}>
          {isDocx ? "Standardized to Markdown" : "Native Markdown"}
        </span>
      ) : null}
    </span>
  );
}
