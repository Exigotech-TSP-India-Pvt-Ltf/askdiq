"use client";

import React, { useId } from "react";
import { RotateCcw } from "lucide-react";
import { iconButton } from "@/components/ui/styles";
import { normalizeHex } from "@/lib/brand-palette";
import { inputClass } from "./fields";

interface ColorFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** Shown in the swatch when `value` is empty or invalid (e.g. the derived colour). */
  fallback: string;
  error?: string;
  hint?: string;
  /** Contrast badges (live). */
  badges?: React.ReactNode;
  /** Offers "use derived colour" (sets ""). */
  clearable?: boolean;
}

/** Native colour picker + hex text input, with live contrast badges. */
export function ColorField({ label, value, onChange, fallback, error, hint, badges, clearable }: ColorFieldProps) {
  const id = useId();
  const errorId = `${id}-error`;
  const swatch = normalizeHex(value) ?? normalizeHex(fallback) ?? "";
  return (
    <div className="space-y-1.5">
      <label htmlFor={`${id}-hex`} className="block text-xs font-semibold text-fg">
        {label}
      </label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          aria-label={`${label} picker`}
          value={swatch}
          onChange={(e) => onChange(e.target.value)}
          className="h-9 w-11 shrink-0 cursor-pointer rounded-lg border border-line bg-surface-card p-1"
        />
        <input
          id={`${id}-hex`}
          type="text"
          inputMode="text"
          spellCheck={false}
          autoComplete="off"
          maxLength={7}
          value={value}
          placeholder={clearable ? `Derived (${fallback})` : undefined}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? errorId : undefined}
          onChange={(e) => onChange(e.target.value)}
          className={`${inputClass} font-mono uppercase tabular-nums`}
        />
        {clearable && value ? (
          <button type="button" className={iconButton} aria-label={`Use the derived ${label.toLowerCase()}`} onClick={() => onChange("")}>
            <RotateCcw className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          </button>
        ) : null}
      </div>
      {badges ? (
        <div className="flex flex-wrap gap-1.5" aria-live="polite">
          {badges}
        </div>
      ) : null}
      {error ? (
        <p id={errorId} className="text-[11px] font-medium text-status-danger-fg">
          {error}
        </p>
      ) : hint ? (
        <p className="text-[11px] text-fg-subtle">{hint}</p>
      ) : null}
    </div>
  );
}
