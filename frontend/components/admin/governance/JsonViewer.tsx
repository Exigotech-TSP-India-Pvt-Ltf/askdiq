"use client";

import React, { useId, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { CopyButton } from "@/components/ui/CopyButton";
import { focusRing } from "@/components/ui/styles";

/** Pretty JSON for display. Circular or odd values never throw. */
export function toPrettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

/**
 * Expandable JSON viewer for log `detail` (G11). The payload is rendered as
 * text inside `<pre>`, so React escapes it: markup such as `<script>` shows
 * as characters and is never parsed as HTML.
 */
export function JsonViewer({
  value,
  label = "event detail",
  defaultOpen = false,
}: {
  value: unknown;
  label?: string;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();
  const text = useMemo(() => (open ? toPrettyJson(value) : ""), [open, value]);

  return (
    <div className="min-w-0">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={(event) => {
          event.stopPropagation();
          setOpen((v) => !v);
        }}
        className={`inline-flex items-center gap-1 rounded-sm px-1 text-[11px] font-medium text-brand-700 hover:underline dark:text-brand-300 ${focusRing}`}
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
        )}
        {open ? `Hide ${label}` : `Show ${label}`}
      </button>
      {open ? (
        <div id={panelId} className="relative mt-1.5">
          <pre
            data-testid="json-viewer"
            className="max-h-72 max-w-xl overflow-auto whitespace-pre-wrap break-all rounded-lg border border-line bg-surface-muted p-3 font-mono text-[11px] leading-relaxed text-fg-muted"
          >
            {text}
          </pre>
          <span className="absolute right-1.5 top-1.5">
            <CopyButton value={text} label={`Copy ${label}`} />
          </span>
        </div>
      ) : null}
    </div>
  );
}
