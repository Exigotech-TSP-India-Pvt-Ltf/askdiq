"use client";

import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, m } from "motion/react";
import { crossfade, pop, useMotionVariants } from "@/lib/motion";
import { useFocusTrap } from "@/lib/use-focus-trap";
import { overlayBackdrop, overlayPanel } from "@/components/ui/styles";

export interface ModalProps {
  open: boolean;
  /** Esc and backdrop click; omit to make the modal non-dismissable (busy). */
  onClose?: () => void;
  role?: "dialog" | "alertdialog";
  labelledBy: string;
  describedBy?: string;
  /** Accessible name when there is no visible heading. */
  ariaLabel?: string;
  initialFocusRef?: React.RefObject<HTMLElement | null>;
  /** "center" for dialogs, "top" for the command palette. */
  placement?: "center" | "top";
  className?: string;
  children: React.ReactNode;
}

/**
 * Centered modal shell shared by ConfirmDialog, the command palette and the
 * shortcuts sheet: portal, fading backdrop, pop-in panel, focus trap, Esc.
 */
export function Modal({
  open,
  onClose,
  role = "dialog",
  labelledBy,
  describedBy,
  ariaLabel,
  initialFocusRef,
  placement = "center",
  className = "max-w-md",
  children,
}: ModalProps) {
  const [mounted, setMounted] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const variants = useMotionVariants(pop);
  useEffect(() => setMounted(true), []);

  useFocusTrap(panelRef, open && mounted, { onEscape: onClose, initialFocusRef });

  if (!mounted) return null;

  return createPortal(
    <AnimatePresence>
      {open ? (
        <div
          key="modal"
          className={`fixed inset-0 z-150 flex justify-center p-4 ${placement === "top" ? "items-start pt-[12vh]" : "items-center"}`}
        >
          <m.div
            aria-hidden="true"
            className={overlayBackdrop}
            variants={crossfade}
            initial="hidden"
            animate="visible"
            exit="exit"
            onClick={onClose}
          />
          <m.div
            ref={panelRef}
            role={role}
            aria-modal="true"
            aria-labelledby={ariaLabel ? undefined : labelledBy}
            aria-label={ariaLabel}
            aria-describedby={describedBy}
            tabIndex={-1}
            variants={variants}
            initial="hidden"
            animate="visible"
            exit="exit"
            className={`relative w-full rounded-2xl ${overlayPanel} ${className}`}
          >
            {children}
          </m.div>
        </div>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}
