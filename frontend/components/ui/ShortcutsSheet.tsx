"use client";

import React, { useId } from "react";
import { Keyboard, X } from "lucide-react";
import { SHORTCUTS, type ShortcutDef } from "@/lib/shortcuts";
import { Modal } from "@/components/ui/Modal";
import { iconButton } from "@/components/ui/styles";

interface ShortcutsSheetProps {
  open: boolean;
  onClose: () => void;
  shortcuts?: readonly ShortcutDef[];
}

/** The `?` keyboard-shortcut help sheet (newplan §15.1). */
export function ShortcutsSheet({ open, onClose, shortcuts = SHORTCUTS }: ShortcutsSheetProps) {
  const titleId = useId();
  const groups = Array.from(new Set(shortcuts.map((s) => s.group)));

  return (
    <Modal open={open} onClose={onClose} labelledBy={titleId} className="max-w-md">
      <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
        <h2 id={titleId} className="flex items-center gap-2 text-sm font-semibold text-fg">
          <Keyboard className="h-[18px] w-[18px] text-brand-600 dark:text-brand-400" strokeWidth={1.75} aria-hidden="true" />
          Keyboard shortcuts
        </h2>
        <button type="button" onClick={onClose} aria-label="Close shortcuts" className={iconButton}>
          <X className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        </button>
      </div>
      <div className="space-y-4 px-5 py-4">
        {groups.map((group) => (
          <section key={group} aria-label={group}>
            <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">{group}</h3>
            <dl className="divide-y divide-line">
              {shortcuts
                .filter((s) => s.group === group)
                .map((s) => (
                  <div key={s.description} className="flex items-center justify-between gap-4 py-2">
                    <dt className="text-xs text-fg-muted">{s.description}</dt>
                    <dd className="flex shrink-0 items-center gap-1">
                      {s.keys.map((key) => (
                        <kbd
                          key={key}
                          className="rounded-md border border-line bg-surface-muted px-1.5 py-0.5 font-mono text-[11px] font-medium text-fg"
                        >
                          {key}
                        </kbd>
                      ))}
                    </dd>
                  </div>
                ))}
            </dl>
          </section>
        ))}
        <p className="text-[11px] text-fg-subtle">Single-key shortcuts are paused while you type in a field.</p>
      </div>
    </Modal>
  );
}
