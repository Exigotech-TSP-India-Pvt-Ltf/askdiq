"use client";

/* eslint-disable @next/next/no-img-element -- brand artwork preview (SVG / PNG / WebP of unknown size). */
import React, { useId, useRef, useState } from "react";
import { ImageUp, Loader2, Trash2 } from "lucide-react";
import { focusRing, iconButton } from "@/components/ui/styles";
import { BRAND_ASSET_MAX_BYTES, BRAND_ASSET_MIME_TYPES, checkAssetFile } from "@/lib/settings-api";

interface AssetDropzoneProps {
  label: string;
  hint?: string;
  /** Current artwork (uploaded asset or the default). */
  src: string;
  /** True when a tenant asset is set (shows "Use default"). */
  custom: boolean;
  /** Dark artwork is previewed on a dark tile. */
  tone: "light" | "dark";
  busy?: boolean;
  error?: string | null;
  onFile: (file: File) => void;
  onClear: () => void;
  /** Reports a pre-check rejection (bad type / size) without uploading. */
  onReject: (message: string) => void;
}

/** Drag-and-drop (or click / keyboard) upload tile with client type and size pre-checks. */
export function AssetDropzone({ label, hint, src, custom, tone, busy, error, onFile, onClear, onReject }: AssetDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const id = useId();

  function accept(file: File | undefined) {
    if (!file) return;
    const problem = checkAssetFile(file);
    if (problem) onReject(problem);
    else onFile(file);
  }

  const tile = tone === "dark" ? "bg-surface-inverse" : "bg-surface-muted";
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span id={`${id}-label`} className="text-xs font-semibold text-fg">
          {label}
        </span>
        {custom ? (
          <button type="button" className={`${iconButton} h-7 w-7`} aria-label={`Use the default ${label.toLowerCase()}`} onClick={onClear}>
            <Trash2 className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          </button>
        ) : null}
      </div>
      <button
        type="button"
        aria-labelledby={`${id}-label ${id}-action`}
        aria-describedby={error ? `${id}-error` : undefined}
        disabled={busy}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          accept(e.dataTransfer.files?.[0]);
        }}
        data-testid={`dropzone-${label}`}
        className={`group flex w-full flex-col items-center justify-center gap-2 rounded-xl border border-dashed p-3 text-center transition-[border-color,box-shadow,transform] duration-150 hover:-translate-y-0.5 motion-reduce:hover:translate-y-0 hover:border-brand-300 hover:shadow-md dark:hover:border-brand-700/60 ${
          dragging ? "border-brand-500 shadow-md" : "border-line-strong"
        } ${error ? "border-status-danger" : ""} ${focusRing}`}
      >
        <span className={`flex h-14 w-full items-center justify-center rounded-lg ${tile}`}>
          {busy ? (
            <Loader2 className="h-5 w-5 animate-spin text-fg-subtle motion-reduce:animate-none" strokeWidth={1.75} aria-hidden="true" />
          ) : (
            <img src={src} alt="" className="max-h-10 max-w-[85%] object-contain" />
          )}
        </span>
        <span id={`${id}-action`} className="flex items-center gap-1 text-[11px] font-medium text-fg-muted group-hover:text-fg">
          <ImageUp className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          {busy ? "Uploading…" : "Drop or choose a file"}
        </span>
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={[...BRAND_ASSET_MIME_TYPES, ".png", ".webp", ".svg"].join(",")}
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        data-testid={`file-${label}`}
        onChange={(e) => {
          accept(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-[11px] font-medium text-status-danger-fg">
          {error}
        </p>
      ) : (
        <p className="text-[11px] text-fg-subtle">{hint ?? `PNG, WebP or SVG, up to ${BRAND_ASSET_MAX_BYTES / 1024} KB.`}</p>
      )}
    </div>
  );
}
