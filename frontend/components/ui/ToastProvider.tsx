"use client";

import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { toast as toastVariants, useMotionVariants } from "@/lib/motion";
import { focusRing, iconButton } from "@/components/ui/styles";

export type ToastTone = "success" | "error" | "info";

export interface ToastOptions {
  title: string;
  description?: string;
  tone?: ToastTone;
  /** Shows an "Undo" button; the toast closes after it runs. */
  onUndo?: () => void;
  /** Auto-dismiss delay; 0 keeps the toast until closed. */
  durationMs?: number;
}

interface ToastItem extends Required<Pick<ToastOptions, "title" | "tone" | "durationMs">> {
  id: number;
  description?: string;
  onUndo?: () => void;
}

interface ToastContextValue {
  show: (options: ToastOptions) => number;
  dismiss: (id: number) => void;
}

/** Default auto-dismiss delay (long enough to reach "Undo"). */
export const TOAST_DURATION_MS = 6000;
const MAX_VISIBLE = 4;

const noop: ToastContextValue = { show: () => -1, dismiss: () => undefined };
const ToastContext = createContext<ToastContextValue | null>(null);

/** Toast API; a no-op outside ToastProvider so components stay renderable in isolation. */
export function useToast(): ToastContextValue {
  return useContext(ToastContext) ?? noop;
}

const TONE: Record<ToastTone, { icon: React.ElementType; className: string }> = {
  success: { icon: CheckCircle2, className: "text-status-ok" },
  error: { icon: AlertTriangle, className: "text-status-danger" },
  info: { icon: Info, className: "text-status-info" },
};

/**
 * Global toasts: Motion enter / exit inside a `role="status"`
 * live region, an Undo action and auto-dismiss that pauses while the
 * pointer or keyboard focus is on the toast.
 */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const show = useCallback((options: ToastOptions) => {
    const id = nextId.current++;
    const item: ToastItem = {
      id,
      title: options.title,
      description: options.description,
      tone: options.tone ?? "info",
      onUndo: options.onUndo,
      durationMs: options.durationMs ?? TOAST_DURATION_MS,
    };
    setToasts((list) => [...list, item].slice(-MAX_VISIBLE));
    return id;
  }, []);

  const value = useMemo(() => ({ show, dismiss }), [show, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* A persistent polite live region: screen readers announce each toast
          as it is inserted (a region created together with its content is
          often missed). */}
      <div
        role="status"
        aria-live="polite"
        aria-label="Notifications"
        className="pointer-events-none fixed bottom-4 right-4 z-[200] flex w-[calc(100%-2rem)] max-w-sm flex-col gap-2"
      >
        <AnimatePresence initial={false}>
          {toasts.map((t) => (
            <Toast key={t.id} item={t} onDismiss={dismiss} />
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

function Toast({ item, onDismiss }: { item: ToastItem; onDismiss: (id: number) => void }) {
  const variants = useMotionVariants(toastVariants);
  const [paused, setPaused] = useState(false);
  const remaining = useRef(item.durationMs);
  const startedAt = useRef(0);
  const { icon: Icon, className } = TONE[item.tone];

  useEffect(() => {
    if (paused || item.durationMs <= 0) return undefined;
    startedAt.current = Date.now();
    const timer = setTimeout(() => onDismiss(item.id), remaining.current);
    return () => {
      clearTimeout(timer);
      remaining.current -= Date.now() - startedAt.current;
    };
  }, [paused, item.id, item.durationMs, onDismiss]);

  const pause = () => setPaused(true);
  const resume = () => setPaused(false);

  function undo() {
    item.onUndo?.();
    onDismiss(item.id);
  }

  return (
    <m.div
      layout
      aria-atomic="true"
      data-tone={item.tone}
      variants={variants}
      initial="hidden"
      animate="visible"
      exit="exit"
      onMouseEnter={pause}
      onMouseLeave={resume}
      onFocus={pause}
      onBlur={resume}
      data-paused={paused ? "true" : "false"}
      className="pointer-events-auto flex items-start gap-3 rounded-xl border border-line-brand/70 bg-surface-elevated p-3.5 text-fg shadow-xl dark:border-brand-500/20 dark:shadow-black/40"
    >
      <Icon className={`mt-0.5 h-[18px] w-[18px] shrink-0 ${className}`} strokeWidth={1.75} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-semibold text-fg">{item.title}</p>
        {item.description ? <p className="mt-0.5 text-xs text-fg-muted">{item.description}</p> : null}
      </div>
      {item.onUndo ? (
        <button
          type="button"
          onClick={undo}
          className={`shrink-0 rounded-md px-2 py-1 text-xs font-semibold text-brand-700 transition-colors hover:bg-surface-muted dark:text-brand-300 ${focusRing}`}
        >
          Undo
        </button>
      ) : null}
      <button
        type="button"
        onClick={() => onDismiss(item.id)}
        aria-label="Dismiss notification"
        className={`${iconButton} h-6 w-6`}
      >
        <X className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
      </button>
    </m.div>
  );
}
