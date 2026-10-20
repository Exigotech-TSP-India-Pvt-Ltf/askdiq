"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { Modal } from "@/components/ui/Modal";
import { dangerButton, focusRing, primaryButton, secondaryButton } from "@/components/ui/styles";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** "danger" styles the confirm button red and uses role="alertdialog". */
  tone?: "default" | "danger";
  /**
   * Typed confirmation for destructive superadmin actions: the confirm button
   * stays disabled until the user types exactly this text.
   */
  confirmText?: string;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

/** Confirmation dialog with optional typed confirmation. */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "default",
  confirmText,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const descId = useId();
  const inputId = useId();

  useEffect(() => {
    if (!open) {
      setTyped("");
      setBusy(false);
    }
  }, [open]);

  const matches = confirmText === undefined || typed === confirmText;

  async function confirm(event: React.FormEvent) {
    event.preventDefault();
    if (!matches || busy) return;
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={busy ? undefined : onCancel}
      role={tone === "danger" ? "alertdialog" : "dialog"}
      labelledBy={titleId}
      describedBy={description ? descId : undefined}
      initialFocusRef={confirmText !== undefined ? inputRef : cancelRef}
      className="max-w-md p-5"
    >
      <form onSubmit={confirm} className="space-y-4">
        <div className="flex items-start gap-3">
          {tone === "danger" ? (
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-status-danger-bg text-status-danger">
              <AlertTriangle className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
            </span>
          ) : null}
          <div className="min-w-0">
            <h2 id={titleId} className="text-sm font-semibold text-fg">
              {title}
            </h2>
            {description ? (
              <div id={descId} className="mt-1 text-xs leading-relaxed text-fg-muted">
                {description}
              </div>
            ) : null}
          </div>
        </div>

        {confirmText !== undefined ? (
          <div className="space-y-1.5">
            <label htmlFor={inputId} className="block text-xs text-fg-muted">
              Type <span className="font-mono font-semibold text-fg">{confirmText}</span> to confirm
            </label>
            <input
              id={inputId}
              ref={inputRef}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={typed.length > 0 && !matches}
              className={`w-full rounded-lg border border-line bg-surface-card px-3 py-2 font-mono text-xs text-fg ${focusRing}`}
            />
          </div>
        ) : null}

        <div className="flex justify-end gap-2">
          <button ref={cancelRef} type="button" onClick={onCancel} disabled={busy} className={secondaryButton}>
            {cancelLabel}
          </button>
          <button
            type="submit"
            disabled={!matches || busy}
            aria-busy={busy}
            className={tone === "danger" ? dangerButton : primaryButton}
          >
            {confirmLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}
