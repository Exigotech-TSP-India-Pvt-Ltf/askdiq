"use client";

import React, { useId } from "react";
import { CheckCircle2, AlertTriangle } from "lucide-react";
import { focusRing } from "@/components/ui/styles";
import { formatRatio } from "@/lib/brand-palette";

export const inputClass = `w-full rounded-lg border border-line bg-surface-card px-3 py-2 text-sm text-fg placeholder:text-fg-subtle transition-colors hover:border-line-strong aria-[invalid=true]:border-status-danger ${focusRing}`;

export const cardClass = "rounded-2xl border border-line bg-surface-card p-5 shadow-sm";

export function SettingsCard({
  title,
  description,
  icon: Icon,
  children,
  actions,
}: {
  title: string;
  description?: string;
  icon?: React.ComponentType<{ className?: string; strokeWidth?: string | number }>;
  children: React.ReactNode;
  actions?: React.ReactNode;
}) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className={cardClass}>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          {Icon ? (
            <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300">
              <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            </span>
          ) : null}
          <div>
            <h2 id={headingId} className="text-sm font-bold text-fg">
              {title}
            </h2>
            {description ? <p className="mt-0.5 text-xs text-fg-muted">{description}</p> : null}
          </div>
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

interface FieldProps {
  label: string;
  hint?: string;
  error?: string;
  children: (props: { id: string; "aria-invalid": boolean; "aria-describedby"?: string }) => React.ReactNode;
  className?: string;
}

/** Label + control + hint / error, wired with ids for screen readers. */
export function Field({ label, hint, error, children, className = "" }: FieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className={`space-y-1 ${className}`}>
      <label htmlFor={id} className="block text-xs font-semibold text-fg">
        {label}
      </label>
      {children({ id, "aria-invalid": Boolean(error), "aria-describedby": describedBy })}
      {hint && !error ? (
        <p id={hintId} className="text-[11px] text-fg-subtle">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-[11px] font-medium text-status-danger-fg">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** WCAG contrast pill: ratio and pass / fail against `min`. */
export function ContrastBadge({ ratio, min, label }: { ratio: number | null; min: number; label: string }) {
  if (ratio === null) return null;
  const passes = ratio >= min;
  const Icon = passes ? CheckCircle2 : AlertTriangle;
  return (
    <span
      data-testid="contrast-badge"
      data-passes={passes}
      title={`${label}: ${formatRatio(ratio)} (AA needs ${min}:1)`}
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold tabular-nums ${
        passes ? "bg-status-ok-bg text-status-ok-fg" : "bg-status-danger-bg text-status-danger-fg"
      }`}
    >
      <Icon className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
      <span>
        {label} {passes ? "AA" : "Fails AA"} {formatRatio(ratio)}
      </span>
    </span>
  );
}
