"use client";

import React from "react";
import { formatAbsoluteTime, formatRelativeTime, toServerDate } from "@/lib/time";

interface RelativeTimeProps {
  /** Server timestamp (ISO 8601; naive values are read as UTC). */
  value: string | null | undefined;
  /** Text shown when the value is missing or unparseable. */
  fallback?: string;
  /**
   * `relative` (default) shows "3h ago" with the full local date-time in the
   * tooltip; `absolute` shows the full date-time with the age in the tooltip.
   */
  display?: "relative" | "absolute";
  /** Text before the date, e.g. "last seen ". */
  prefix?: string;
  className?: string;
}

/**
 * The one way the dashboard shows when a record happened: a semantic `<time>`
 * whose `dateTime` is the UTC instant and whose tooltip carries the viewer's
 * local date-time with timezone.
 */
export function RelativeTime({ value, fallback = "—", display = "relative", prefix = "", className = "" }: RelativeTimeProps) {
  const date = toServerDate(value);
  if (!date) return <span className={className}>{fallback}</span>;
  const absolute = formatAbsoluteTime(date);
  const relative = formatRelativeTime(date);
  const [text, tooltip] = display === "absolute" ? [absolute, relative] : [relative, absolute];
  return (
    <time dateTime={date.toISOString()} title={tooltip} className={`whitespace-nowrap ${className}`} suppressHydrationWarning>
      {prefix}
      {text}
    </time>
  );
}
