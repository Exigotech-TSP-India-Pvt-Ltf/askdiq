/**
 * Shared class strings for the global UX primitives (newplan §15.2), so every
 * overlay, icon button and card uses the same tokens in light and dark.
 */
export const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-surface-card";

export const iconButton = `inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-fg-subtle transition-colors hover:bg-surface-muted hover:text-fg disabled:cursor-not-allowed disabled:opacity-40 ${focusRing}`;

export const overlayBackdrop = "fixed inset-0 bg-ink/40 backdrop-blur-sm dark:bg-black/60";

export const overlayPanel = "border border-line bg-surface-elevated text-fg shadow-2xl";

export const popoverPanel =
  "rounded-xl border border-line bg-surface-elevated p-1.5 text-fg shadow-xl dark:shadow-black/40";

export const primaryButton = `inline-flex items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`;

export const dangerButton = `inline-flex items-center justify-center gap-1.5 rounded-lg bg-status-danger px-3.5 py-2 text-xs font-semibold text-white shadow-sm transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 dark:text-fg-inverse ${focusRing}`;

export const secondaryButton = `inline-flex items-center justify-center gap-1.5 rounded-lg border border-line bg-surface-card px-3.5 py-2 text-xs font-medium text-fg-muted shadow-sm transition-colors hover:border-brand-300 hover:text-fg dark:hover:border-brand-700/60 ${focusRing}`;

/** Menu / list row (palette results, profile menu items). */
export const menuItem = `flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs text-fg-muted transition-colors hover:bg-surface-muted hover:text-fg ${focusRing}`;
