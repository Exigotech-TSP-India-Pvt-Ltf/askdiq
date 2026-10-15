"use client";

import React from "react";
import { FileCode, Sparkles } from "lucide-react";

export interface FormatBadgeProps {
  convertedFrom?: string | null;
  originalFilename?: string | null;
  originalSha256?: string | null;
  className?: string;
}

export function FormatBadge({
  convertedFrom,
  originalFilename,
  originalSha256,
  className = "",
}: FormatBadgeProps) {
  const isConverted = convertedFrom?.toLowerCase() === "docx";

  const provenanceTooltip = isConverted
    ? `Standardized from .docx\nOriginal File: ${originalFilename || "uploaded.docx"}\nSHA-256: ${
        originalSha256 ? `${originalSha256.slice(0, 16)}...` : "verified"
      }`
    : undefined;

  return (
    <div className={`inline-flex items-center gap-1.5 flex-wrap ${className}`}>
      {/* Canonical active format */}
      <span className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-mono font-semibold text-slate-800">
        <FileCode className="w-3 h-3 text-slate-500" />
        MARKDOWN (.md)
      </span>

      {/* Conversion provenance chip if converted from docx */}
      {isConverted && (
        <span
          title={provenanceTooltip}
          className="inline-flex items-center gap-1 rounded-md border border-brand-200 bg-brand-50 px-2 py-0.5 text-[11px] font-medium text-brand-700 cursor-help"
        >
          <Sparkles className="w-3 h-3 text-brand-600" />
          <span>Standardized from .docx</span>
        </span>
      )}
    </div>
  );
}
