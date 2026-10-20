"use client";

import type React from "react";
import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import { ArrowUpRight, Minus, TrendingDown, TrendingUp } from "lucide-react";
import { Sparkline, type SparkPoint, type SparkTone } from "./Sparkline";
import type { SelectHandler } from "./primitives";
import { studioInteractive, studioSurface } from "@/components/ui/styles";

export interface KpiTrendCardProps {
  label: string;
  value: React.ReactNode;
  /** Change vs the previous period as a fraction (0.12 = +12 %). */
  delta?: number | null;
  /** Whether a rising value is good (default) or bad (errors, latency, cost). */
  goodDirection?: "up" | "down";
  /** 7-day (or any) trend for the sparkline. */
  trend: readonly SparkPoint[];
  /** Formats the sparkline tooltip values (e.g. USD); default compact numbers. */
  formatTrendValue?: (value: number) => string;
  onSelect?: SelectHandler<SparkPoint>;
  /** Makes the card a link (deep-link, pre-filtered). */
  href?: string;
  /** Spread from `usePrefetchOnIntent` to warm the target page. */
  intentHandlers?: React.HTMLAttributes<HTMLElement>;
  icon?: LucideIcon;
  tone?: SparkTone;
  /** One-line footnote under the sparkline (truncated; strings also get a tooltip). */
  hint?: React.ReactNode;
  /** Time window of the value, e.g. "Last 7 days" (shown under the label). */
  windowLabel?: string;
  /** Definition tip (e.g. `<InfoTip>`), rendered beside the header outside the card link. */
  info?: React.ReactNode;
  className?: string;
}

const percent = new Intl.NumberFormat("en", { style: "percent", maximumFractionDigits: 1 });

function DeltaBadge({ delta, goodDirection }: { delta: number; goodDirection: "up" | "down" }) {
  const flat = Math.abs(delta) < 0.0005;
  const good = flat ? null : (delta > 0) === (goodDirection === "up");
  const Icon = flat ? Minus : delta > 0 ? TrendingUp : TrendingDown;
  const tone =
    good === null
      ? "bg-surface-muted text-fg-muted"
      : good
        ? "bg-status-ok-bg text-status-ok-fg"
        : "bg-status-danger-bg text-status-danger-fg";
  const sign = delta > 0 ? "+" : "";
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold tabular-nums ${tone}`}>
      <Icon className="h-3 w-3" strokeWidth={1.75} aria-hidden="true" />
      {sign}
      {percent.format(delta)}
      <span className="sr-only">{good === null ? "(no change)" : good ? "(improving)" : "(worsening)"}</span>
    </span>
  );
}

// Studio surface (same as the Usage Live Telemetry cards).
const CARD = `group flex flex-col gap-3 p-5 ${studioSurface}`;
const CARD_INTERACTIVE = studioInteractive;

/** KPI tile with value, period delta and a sparkline. */
export function KpiTrendCard({
  label,
  value,
  delta,
  goodDirection = "up",
  trend,
  onSelect,
  href,
  intentHandlers,
  icon: Icon,
  tone = "brand",
  hint,
  formatTrendValue,
  windowLabel,
  info,
  className = "",
}: KpiTrendCardProps) {
  const header = (
    <div className="flex items-center justify-between gap-2">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-fg-subtle">{label}</p>
        {windowLabel && <p className="truncate text-[11px] text-fg-subtle">{windowLabel}</p>}
      </div>
      {href ? (
        <ArrowUpRight
          className="h-4 w-4 text-fg-subtle transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-accent-fg motion-reduce:transition-none"
          strokeWidth={1.75}
          aria-hidden="true"
        />
      ) : (
        Icon && <Icon className="h-4 w-4 text-fg-subtle" strokeWidth={1.75} aria-hidden="true" />
      )}
    </div>
  );
  const figure = (
    <div className="flex min-w-0 items-end justify-between gap-2">
      <p data-testid="kpi-value" className="min-w-0 truncate whitespace-nowrap text-2xl font-semibold tabular-nums text-fg">
        {value}
      </p>
      {delta !== undefined && delta !== null && <DeltaBadge delta={delta} goodDirection={goodDirection} />}
    </div>
  );

  return (
    <div className={`${CARD} ${href ? CARD_INTERACTIVE : ""} ${info ? "overflow-visible" : ""} ${className}`}>
      {info && <div className="absolute right-10 top-5 z-10">{info}</div>}
      {href ? (
        <Link href={href} className="flex flex-col gap-3 rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500" {...intentHandlers}>
          {header}
          {figure}
        </Link>
      ) : (
        <>
          {header}
          {figure}
        </>
      )}
      <Sparkline
        data={trend}
        onSelect={onSelect}
        label={`${label} trend`}
        tone={tone}
        height={36}
        formatValue={formatTrendValue}
      />
      {hint ? (
        <p className="truncate whitespace-nowrap text-[11px] text-fg-subtle" title={typeof hint === "string" ? hint : undefined}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}
