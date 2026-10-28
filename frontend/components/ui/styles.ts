/**
 * Shared class strings for the global UX primitives, so every
 * overlay, icon button and card uses the same tokens in light and dark.
 */
export const focusRing =
  "focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-surface-card";

export const iconButton = `inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-fg-subtle transition-colors hover:bg-surface-muted hover:text-fg disabled:cursor-not-allowed disabled:opacity-40 ${focusRing}`;

export const overlayBackdrop = "fixed inset-0 bg-ink/40 backdrop-blur-xs dark:bg-black/60";

/** Drawers and dialogs: elevated surface with the studio brand edge and inner highlight. */
export const overlayPanel =
  "border border-line-brand/70 bg-surface-elevated text-fg shadow-2xl ring-1 ring-inset ring-fg-inverse/5 dark:border-brand-500/20 dark:ring-brand-300/10";

export const popoverPanel =
  "rounded-xl border border-line-brand/70 bg-surface-elevated p-1.5 text-fg shadow-xl dark:border-brand-500/20 dark:shadow-black/40";

export const primaryButton = `inline-flex items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-3.5 py-2 text-xs font-semibold text-white shadow-xs transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`;

export const dangerButton = `inline-flex items-center justify-center gap-1.5 rounded-lg bg-status-danger px-3.5 py-2 text-xs font-semibold text-white shadow-xs transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 dark:text-fg-inverse ${focusRing}`;

export const secondaryButton = `inline-flex items-center justify-center gap-1.5 rounded-lg border border-line bg-surface-card px-3.5 py-2 text-xs font-medium text-fg-muted shadow-xs transition-colors hover:border-brand-300 hover:text-fg dark:hover:border-brand-700/60 ${focusRing}`;

/** Menu / list row (palette results, profile menu items). */
export const menuItem = `flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-xs text-fg-muted transition-colors hover:bg-surface-muted hover:text-fg ${focusRing}`;

/* ------------------------------------------------------------------------ */
/* Studio look (Usage "Live Telemetry" cards), shared by every card, chart,  */
/* KPI tile and table. Tokens only: bg-studio / shadow-studio are defined    */
/* per theme in app/globals.css (@theme).                        */
/* ------------------------------------------------------------------------ */

export type StudioTone = "brand" | "ok" | "info" | "warn" | "danger";

/** Card surface: brand-tinted gradient, corner glow, inner top highlight. */
export const studioSurface =
  "relative overflow-hidden rounded-2xl border border-line-brand bg-surface-card bg-studio text-fg shadow-studio dark:border-brand-500/25";

/** Hover / focus: lift 2 px, stronger glow, brand edge (150 ms, CSS only). */
export const studioInteractive =
  "transition-[transform,box-shadow,border-color] duration-150 hover:border-brand-400/70 hover:shadow-studio-hover focus-within:border-brand-400/70 motion-safe:hover:-translate-y-0.5 motion-reduce:transition-none dark:hover:border-brand-400/60";

/** Selected / focused card (e.g. the active telemetry focus). */
export const studioActive = "border-brand-500 ring-2 ring-brand-500/70 dark:border-brand-400/60 dark:ring-brand-400/60";

/** Divider under card headers and above footers. */
export const studioDivider = "border-line-brand/70 dark:border-brand-500/20";

/** Inset tile inside a studio card (metrics, stage rows, callouts). */
export const studioTile =
  "rounded-xl border border-line-brand/60 bg-surface-studio/60 dark:border-brand-500/20 dark:bg-white/3";

export const studioTileInteractive =
  "transition-colors duration-150 hover:border-brand-300 hover:bg-surface-studio dark:hover:border-brand-400/50 dark:hover:bg-white/6";

/** Icon badge / mono pill colours per tone. */
export const studioToneClasses: Record<StudioTone, string> = {
  brand:
    "border-brand-200/80 bg-brand-50 text-brand-700 dark:border-brand-500/30 dark:bg-brand-500/15 dark:text-brand-300",
  ok: "border-status-ok/25 bg-status-ok-bg text-status-ok-fg",
  info: "border-status-info/25 bg-status-info-bg text-status-info-fg",
  warn: "border-status-warn/25 bg-status-warn-bg text-status-warn-fg",
  danger: "border-status-danger/25 bg-status-danger-bg text-status-danger-fg",
};

export const studioIconBadge = "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border shadow-xs";

export const studioPill =
  "inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider";

/** Card title / subtitle typography. */
export const studioTitle = "text-sm font-extrabold tracking-tight text-fg";
export const studioSubtitle = "text-[11px] font-medium text-fg-subtle dark:text-brand-200/70";

/* Tables (DataTable, ChartTable, ad-hoc tables inside cards). */
export const tableHead =
  "border-b border-line-brand/70 bg-surface-studio text-fg-muted dark:border-brand-500/20";
export const tableHeadCell = "font-semibold text-fg-muted";
export const tableBody = "divide-y divide-line/70 text-fg-muted dark:divide-white/6";
/**
 * Row states (zebra, hover, `aria-selected` / `data-selected`) set one colour,
 * --row-tint (app/globals.css), so pinned cells can repaint it exactly.
 */
export const tableRow = "dt-row";
/** Opaque themed surface under a table's rows: what pinned cells paint over. */
export const tableSurface = "dt-surface";
/**
 * The one pinned-column style (header + body cell, right edge): the same
 * themed background as its row in every state, and a divider only while
 * content scrolls under it (`data-scroll-under` from `useScrollUnder`).
 */
export const stickyHeadCell = "dt-sticky-head";
export const stickyCell = "dt-sticky-cell";
export const tableFooter = "border-t border-line-brand/60 bg-surface-studio/50 dark:border-brand-500/15";

/** Segmented control track (TabGroup, StudioSegmented). */
export const segmentTrack =
  "inline-flex gap-1 rounded-xl border border-line bg-surface-muted/80 p-1 shadow-inner dark:border-brand-500/20 dark:bg-surface-studio";
