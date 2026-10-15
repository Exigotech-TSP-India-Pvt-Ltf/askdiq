"use client";

/**
 * The one chart container (planchanges4 F0.4.1, newplan §15.2). Replaces the
 * local copies that lived in the Dashboard (`AuroraChartCard`), Monitoring,
 * Evaluation and Evaluation (Live) pages.
 *
 * - `variant="aurora"` is the branded card with the ambient glow (Dashboard,
 *   Evaluation Live); `variant="plain"` is the flat card (Monitoring, Evaluation).
 * - `titleStyle="eyebrow"` renders the small uppercase title used by the
 *   evaluation pages; `"heading"` the regular title.
 * - Loading skeleton, error (with retry) and empty states.
 * - "View as table": every chart has an accessible table fallback.
 */
import type React from "react";
import { useId, useState } from "react";
import { ChartColumnBig, RotateCcw, Table2, TriangleAlert } from "lucide-react";
import { ChartEmpty } from "./primitives";

export type ChartCardVariant = "plain" | "aurora";
export type ChartCardTitleStyle = "heading" | "eyebrow";

export interface ChartTableColumn {
  key: string;
  label: string;
  /** Right-aligned with tabular numerals. */
  numeric?: boolean;
}

export interface ChartTableData {
  columns: readonly ChartTableColumn[];
  rows: readonly Record<string, React.ReactNode>[];
  caption?: string;
}

export interface ChartCardProps {
  title: string;
  subtitle?: string;
  /** Time window shown after the subtitle, e.g. "Last 7 days". */
  window?: string;
  /** Header slot (badges, range control, menus). */
  actions?: React.ReactNode;
  variant?: ChartCardVariant;
  titleStyle?: ChartCardTitleStyle;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  empty?: boolean;
  emptyMessage?: string;
  /** Data for the "View as table" fallback; the toggle is hidden without it. */
  table?: ChartTableData;
  className?: string;
  /** Size of the chart area. Default `h-64 w-full`. */
  bodyClassName?: string;
  children: React.ReactNode;
}

const HOVER =
  "transition duration-150 motion-safe:hover:-translate-y-0.5 hover:shadow-md motion-reduce:transition-none";

const SECTION: Record<ChartCardVariant, string> = {
  plain: `rounded-xl border border-line bg-surface-card p-6 hover:border-brand-300 dark:hover:border-brand-700/60 ${HOVER}`,
  aurora: `relative overflow-hidden rounded-2xl border border-brand-300/80 dark:border-brand-500/25 bg-surface-card p-6 shadow-card dark:shadow-xl hover:border-brand-400 dark:hover:border-brand-400/60 ${HOVER}`,
};

const TITLE: Record<ChartCardVariant, Record<ChartCardTitleStyle, string>> = {
  plain: {
    heading: "text-base font-semibold text-fg",
    eyebrow: "text-xs font-semibold uppercase tracking-wide text-fg-muted",
  },
  aurora: {
    heading: "text-sm font-extrabold tracking-tight text-fg",
    eyebrow: "text-xs font-bold uppercase tracking-wider text-fg-muted dark:text-brand-300/90",
  },
};

const SUBTITLE: Record<ChartCardVariant, string> = {
  plain: "mt-1 text-xs text-fg-subtle",
  aurora: "mt-0.5 text-[11px] font-medium text-fg-subtle dark:text-brand-200/70",
};

const ICON = { className: "h-4 w-4", strokeWidth: 1.75, "aria-hidden": true } as const;

const BUTTON =
  "inline-flex items-center gap-1.5 rounded-lg border border-line px-2 py-1 text-[11px] font-semibold text-fg-muted transition-colors duration-150 hover:border-brand-300 hover:text-fg dark:hover:border-brand-700/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 focus-visible:ring-offset-surface-card";

export function ChartSkeleton() {
  return (
    <div role="status" aria-busy="true" className="flex h-full w-full flex-col justify-end gap-2">
      <span className="sr-only">Loading chart</span>
      <div className="flex h-full items-end gap-2 motion-safe:animate-pulse" aria-hidden="true">
        {[40, 65, 50, 80, 55, 70, 45].map((h, i) => (
          <div key={i} className="flex-1 rounded-t-md bg-surface-muted" style={{ height: `${h}%` }} />
        ))}
      </div>
    </div>
  );
}

function ChartError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex h-full w-full flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-status-danger/40 bg-status-danger-bg p-4 text-center text-xs text-status-danger-fg"
    >
      <TriangleAlert {...ICON} />
      <p>{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className={BUTTON}>
          <RotateCcw {...ICON} />
          Retry
        </button>
      )}
    </div>
  );
}

export function ChartTable({ table, labelledBy }: { table: ChartTableData; labelledBy?: string }) {
  return (
    <div className="h-full w-full overflow-auto">
      <table className="w-full text-left text-xs" aria-labelledby={labelledBy}>
        {table.caption && <caption className="sr-only">{table.caption}</caption>}
        <thead className="sticky top-0 bg-surface-card">
          <tr className="border-b border-line">
            {table.columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                className={`px-2 py-1.5 font-semibold text-fg-muted ${col.numeric ? "text-right" : ""}`}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, i) => (
            <tr key={i} className="border-b border-line/60 last:border-0">
              {table.columns.map((col) => (
                <td
                  key={col.key}
                  className={`px-2 py-1.5 text-fg ${col.numeric ? "text-right tabular-nums" : ""}`}
                >
                  {row[col.key] ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ChartCard({
  title,
  subtitle,
  window: timeWindow,
  actions,
  variant = "plain",
  titleStyle = "heading",
  loading = false,
  error = null,
  onRetry,
  empty = false,
  emptyMessage,
  table,
  className = "",
  bodyClassName = "h-64 w-full",
  children,
}: ChartCardProps) {
  const [asTable, setAsTable] = useState(false);
  const titleId = useId();
  const bodyId = useId();
  const ready = !loading && !error && !empty;
  const canToggle = ready && table !== undefined && table.rows.length > 0;
  const showTable = canToggle && asTable;
  const aurora = variant === "aurora";
  const subtitleText = [subtitle, timeWindow].filter(Boolean).join(" · ");

  let body: React.ReactNode;
  if (loading) body = <ChartSkeleton />;
  else if (error) body = <ChartError message={error} onRetry={onRetry} />;
  else if (empty) body = <ChartEmpty message={emptyMessage} />;
  else if (showTable) body = <ChartTable table={table} labelledBy={titleId} />;
  else body = children;

  return (
    <section aria-labelledby={titleId} className={`${SECTION[variant]} ${className}`}>
      {aurora && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -right-12 -top-12 h-36 w-36 rounded-full bg-brand-400/10 blur-2xl dark:bg-brand-500/15"
        />
      )}
      <div
        className={`flex justify-between gap-3 ${
          aurora ? "relative z-10 items-center border-b border-brand-100 pb-3 dark:border-brand-500/20" : "items-start"
        }`}
      >
        <div className="min-w-0">
          <h2 id={titleId} className={TITLE[variant][titleStyle]}>
            {title}
          </h2>
          {subtitleText && <p className={SUBTITLE[variant]}>{subtitleText}</p>}
        </div>
        {(actions || canToggle) && (
          <div className="flex shrink-0 items-center gap-2">
            {actions}
            {canToggle && (
              <button
                type="button"
                aria-pressed={asTable}
                aria-controls={bodyId}
                onClick={() => setAsTable((v) => !v)}
                className={BUTTON}
              >
                {asTable ? <ChartColumnBig {...ICON} /> : <Table2 {...ICON} />}
                {asTable ? "View as chart" : "View as table"}
              </button>
            )}
          </div>
        )}
      </div>
      <div
        id={bodyId}
        aria-busy={loading || undefined}
        className={`${aurora ? "relative z-10 mt-5" : titleStyle === "eyebrow" ? "mt-4" : "mt-5"} ${bodyClassName}`}
      >
        {body}
      </div>
    </section>
  );
}

/** Builds `ChartCard.table` from chart rows. */
export function toChartTable<T extends object>(
  rows: readonly T[],
  columns: readonly (ChartTableColumn & {
    key: Extract<keyof T, string>;
    format?: (value: unknown) => React.ReactNode;
  })[],
  caption?: string,
): ChartTableData {
  return {
    columns: columns.map(({ key, label, numeric }) => ({ key, label, numeric })),
    caption,
    rows: rows.map((row) => {
      const out: Record<string, React.ReactNode> = {};
      for (const col of columns) {
        const v: unknown = row[col.key];
        if (col.format) out[col.key] = col.format(v);
        else out[col.key] = typeof v === "number" || typeof v === "string" ? v : v == null ? null : String(v);
      }
      return out;
    }),
  };
}
