"use client";

import React, { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2 } from "lucide-react";
import { ApiError } from "@/lib/api";
import { patchDocumentLink, type DocumentLinkRead } from "@/lib/documents-api";
import { MAX_SOURCE_URL_CHARS, validateSourceUrl } from "@/lib/safe-url";
import { focusRing, popoverPanel, primaryButton, secondaryButton } from "@/components/ui/styles";

const PANEL_WIDTH = 320;
const EDGE = 8;
const OFFSET = 6;
/** Room the panel needs below the trigger before it flips above it. */
const PANEL_HEIGHT_ESTIMATE = 230;

export interface LinkEditTarget {
  document_id: string;
  source_name: string;
  source_url?: string | null;
  section_links?: boolean;
}

export interface LinkEditPopoverProps {
  token: string;
  doc: LinkEditTarget;
  /** The "Edit link" button: the panel is placed next to it and focus returns to it. */
  anchor: HTMLElement;
  onClose: () => void;
  onSaved: (link: DocumentLinkRead) => void;
}

interface Position {
  left: number;
  top?: number;
  bottom?: number;
}

function place(anchor: HTMLElement): Position {
  const rect = anchor.getBoundingClientRect();
  const width = Math.min(PANEL_WIDTH, window.innerWidth - EDGE * 2);
  const left = Math.min(Math.max(EDGE, rect.right - width), Math.max(EDGE, window.innerWidth - width - EDGE));
  if (rect.bottom + OFFSET + PANEL_HEIGHT_ESTIMATE > window.innerHeight && rect.top > PANEL_HEIGHT_ESTIMATE) {
    return { left, bottom: window.innerHeight - rect.top + OFFSET };
  }
  return { left, top: rect.bottom + OFFSET };
}

function saveErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return "You don't have permission to edit knowledge links.";
    if (err.status === 404) return "This document no longer exists.";
    return err.message;
  }
  return "Couldn't save the link. Check your connection and try again.";
}

/**
 * Small non-modal editor for a document's link (OR-7 / OR-12): URL field and
 * "Section links" checkbox, Save / Cancel. Esc or a click outside cancels;
 * focus moves to the URL field on open and back to the trigger on close. A
 * server 422 (invalid or not-allowed URL) is shown inline. Rendered in a
 * portal so the table's horizontal scroller can't clip it.
 */
export function LinkEditPopover({ token, doc, anchor, onClose, onSaved }: LinkEditPopoverProps) {
  const titleId = useId();
  const urlId = useId();
  const sectionId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [url, setUrl] = useState(doc.source_url ?? "");
  const [sectionLinks, setSectionLinks] = useState(Boolean(doc.section_links));
  const [serverError, setServerError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [position, setPosition] = useState<Position | null>(null);

  const check = validateSourceUrl(url);
  const error = serverError ?? (check.ok ? null : check.error);
  const hasUrl = url.trim() !== "";

  const close = useCallback(
    (restoreFocus = true) => {
      onClose();
      if (restoreFocus) anchor.focus();
    },
    [anchor, onClose],
  );

  useLayoutEffect(() => {
    const update = () => setPosition(place(anchor));
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [anchor]);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  useEffect(() => {
    function onPointerDown(event: MouseEvent) {
      const target = event.target as Node;
      if (panelRef.current?.contains(target) || anchor.contains(target)) return;
      close(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [anchor, close]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!check.ok || saving) return;
    setSaving(true);
    setServerError(null);
    try {
      const result = await patchDocumentLink(token, doc.document_id, {
        source_url: check.url,
        section_links: check.url ? sectionLinks : false,
      });
      onSaved(result);
      close();
    } catch (err) {
      setServerError(saveErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  const panel = (
    <div
      ref={panelRef}
      role="dialog"
      aria-labelledby={titleId}
      data-testid="link-edit-popover"
      // Portal events still bubble through React: keep them off the table row.
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Escape") {
          e.preventDefault();
          close();
        }
      }}
      style={{
        position: "fixed",
        left: position?.left ?? 0,
        top: position?.top,
        bottom: position?.bottom,
        width: `min(${PANEL_WIDTH}px, calc(100vw - ${EDGE * 2}px))`,
        visibility: position ? "visible" : "hidden",
      }}
      className={`z-50 p-4! ${popoverPanel}`}
    >
      <form onSubmit={save} noValidate className="space-y-3">
        <h2 id={titleId} className="truncate text-xs font-semibold text-fg" title={`Edit link for ${doc.source_name}`}>
          Edit link for {doc.source_name}
        </h2>
        <div className="space-y-1">
          <label htmlFor={urlId} className="block text-[11px] font-medium text-fg-muted">
            Source URL
          </label>
          <input
            ref={inputRef}
            id={urlId}
            type="url"
            inputMode="url"
            spellCheck={false}
            autoComplete="url"
            maxLength={MAX_SOURCE_URL_CHARS}
            value={url}
            placeholder="https://docs.example.com/page"
            aria-invalid={Boolean(error)}
            aria-describedby={error ? `${urlId}-error` : `${urlId}-hint`}
            onChange={(e) => {
              setUrl(e.target.value);
              setServerError(null);
            }}
            className={`w-full rounded-lg border border-line bg-surface-card px-2.5 py-1.5 text-xs text-fg placeholder:text-fg-subtle aria-invalid:border-status-danger ${focusRing}`}
          />
          {error ? (
            <p id={`${urlId}-error`} role={serverError ? "alert" : undefined} className="text-[11px] font-medium text-status-danger-fg">
              {error}
            </p>
          ) : (
            <p id={`${urlId}-hint`} className="text-[11px] text-fg-subtle">
              https only. Leave empty to remove the link. No re-embedding is needed.
            </p>
          )}
        </div>
        <label htmlFor={sectionId} className="flex items-start gap-2 text-xs text-fg">
          <input
            id={sectionId}
            type="checkbox"
            checked={hasUrl && sectionLinks}
            disabled={!hasUrl}
            onChange={(e) => setSectionLinks(e.target.checked)}
            className="mt-0.5 h-3.5 w-3.5 rounded-sm border-line accent-brand-600"
          />
          <span>
            Section links
            <span className="block text-[11px] text-fg-subtle">Citations add the section anchor (#heading) to the URL.</span>
          </span>
        </label>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={() => close()} className={secondaryButton}>
            Cancel
          </button>
          <button type="submit" disabled={!check.ok || saving} className={primaryButton}>
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" strokeWidth={1.75} aria-hidden="true" /> : null}
            Save
          </button>
        </div>
      </form>
    </div>
  );

  return typeof document === "undefined" ? panel : createPortal(panel, document.body);
}
