"use client";

/**
 * Studio primitives: the Usage "Live Telemetry" card look (layered gradient,
 * inner glow edge, brand ring on hover / focus, icon-badge header with a mono
 * pill) as reusable building blocks. ChartCard, KpiTrendCard, StatCard,
 * TableCard and SkeletonCard are built on these, so every page inherits it.
 *
 * Tokens only (bg-studio / shadow-studio follow light and dark); motion only
 * from lib/motion.ts presets and reduced to opacity for reduced-motion users.
 */
import React, { useId } from "react";
import { m } from "motion/react";
import { cardEnter, layoutSpring, staggerFor, useMotionVariants } from "@/lib/motion";
import { CountUp } from "@/components/ui/CountUp";
import {
  focusRing,
  segmentTrack,
  studioActive,
  studioDivider,
  studioIconBadge,
  studioInteractive,
  studioPill,
  studioSubtitle,
  studioSurface,
  studioTile,
  studioTileInteractive,
  studioTitle,
  studioToneClasses,
  type StudioTone,
} from "@/components/ui/styles";

type IconType = React.ComponentType<{ className?: string; strokeWidth?: string | number }>;

/** Class string for a studio card surface (for elements that cannot use StudioCard). */
export function studioCardClass({
  interactive = false,
  active = false,
  className = "",
}: { interactive?: boolean; active?: boolean; className?: string } = {}): string {
  return [studioSurface, interactive ? studioInteractive : "", active ? studioActive : "", className]
    .filter(Boolean)
    .join(" ");
}

export function StudioIconBadge({ icon: Icon, tone = "brand" }: { icon: IconType; tone?: StudioTone }) {
  return (
    <span data-testid="studio-icon-badge" className={`${studioIconBadge} ${studioToneClasses[tone]}`}>
      <Icon className="h-[18px] w-[18px]" strokeWidth={1.75} aria-hidden="true" />
    </span>
  );
}

export function StudioPill({ tone = "brand", children }: { tone?: StudioTone; children: React.ReactNode }) {
  return <span className={`${studioPill} ${studioToneClasses[tone]}`}>{children}</span>;
}

export interface StudioCardHeaderProps {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  icon?: IconType;
  tone?: StudioTone;
  /** Mono pill beside the title ("Live accounting", "3,901 turns"). */
  pill?: React.ReactNode;
  /** Right-hand slot (value, segmented control, actions). */
  aside?: React.ReactNode;
  titleId?: string;
  /** Heading level for the title. */
  as?: "h2" | "h3" | "h4";
  divider?: boolean;
  className?: string;
}

export function StudioCardHeader({
  title,
  subtitle,
  icon,
  tone = "brand",
  pill,
  aside,
  titleId,
  as: Heading = "h2",
  divider = true,
  className = "",
}: StudioCardHeaderProps) {
  return (
    <div
      className={`relative z-10 flex items-center justify-between gap-3 ${divider ? `border-b pb-3 ${studioDivider}` : ""} ${className}`}
    >
      <div className="flex min-w-0 items-center gap-2.5">
        {icon ? <StudioIconBadge icon={icon} tone={tone} /> : null}
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Heading id={titleId} className={studioTitle}>
              {title}
            </Heading>
            {pill ? <StudioPill tone={tone}>{pill}</StudioPill> : null}
          </div>
          {subtitle ? <p className={`mt-0.5 ${studioSubtitle}`}>{subtitle}</p> : null}
        </div>
      </div>
      {aside ? <div className="flex shrink-0 items-center gap-2">{aside}</div> : null}
    </div>
  );
}

export interface StudioCardProps extends Omit<StudioCardHeaderProps, "titleId" | "divider" | "className" | "title"> {
  title?: React.ReactNode;
  footer?: React.ReactNode;
  /** Lift + glow on hover (clickable cards). */
  interactive?: boolean;
  /** Ring when this card is the selected focus. */
  active?: boolean;
  className?: string;
  bodyClassName?: string;
  children?: React.ReactNode;
  "data-testid"?: string;
}

/**
 * Studio card. Inside a `StudioGrid` it enters with the grid's stagger;
 * standalone it renders statically (no entrance), so it never flashes.
 */
export function StudioCard({
  title,
  subtitle,
  icon,
  tone = "brand",
  pill,
  aside,
  as,
  footer,
  interactive = false,
  active = false,
  className = "",
  bodyClassName = "",
  children,
  "data-testid": testId = "studio-card",
}: StudioCardProps) {
  const titleId = useId();
  const variants = useMotionVariants(cardEnter);
  return (
    <m.section
      variants={variants}
      data-testid={testId}
      data-active={active || undefined}
      aria-labelledby={title ? titleId : undefined}
      className={`${studioCardClass({ interactive, active })} flex flex-col p-6 ${className}`}
    >
      {title ? (
        <StudioCardHeader
          title={title}
          subtitle={subtitle}
          icon={icon}
          tone={tone}
          pill={pill}
          aside={aside}
          as={as}
          titleId={titleId}
        />
      ) : null}
      <div className={`relative z-10 flex-1 ${title ? "mt-4" : ""} ${bodyClassName}`}>{children}</div>
      {footer ? (
        <div
          className={`relative z-10 mt-4 flex items-center justify-between gap-2 border-t pt-3 text-[11px] text-fg-subtle ${studioDivider}`}
        >
          {footer}
        </div>
      ) : null}
    </m.section>
  );
}

/** Responsive grid whose StudioCard children enter with a short stagger. */
export function StudioGrid({
  children,
  className = "grid grid-cols-1 gap-6 lg:grid-cols-3",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  const variants = staggerFor(React.Children.count(children));
  return (
    <m.div variants={variants} initial="hidden" animate="visible" className={className} data-testid="studio-grid">
      {children}
    </m.div>
  );
}

export interface StudioMetricTileProps {
  label: string;
  /** Numbers count up (reduced motion: shown at once); other nodes render as-is. */
  value: number | React.ReactNode;
  format?: (value: number) => string;
  unit?: React.ReactNode;
  tone?: StudioTone;
  onClick?: () => void;
  selected?: boolean;
  className?: string;
}

const VALUE_TONE: Record<StudioTone, string> = {
  brand: "text-fg",
  ok: "text-status-ok-fg",
  info: "text-status-info-fg",
  warn: "text-status-warn-fg",
  danger: "text-status-danger-fg",
};

const defaultFormat = (v: number) => Math.round(v).toLocaleString();

/** Metric tile (inset in a studio card or on its own in a KPI strip). */
export function StudioMetricTile({
  label,
  value,
  format = defaultFormat,
  unit,
  tone = "brand",
  onClick,
  selected = false,
  className = "",
}: StudioMetricTileProps) {
  const content = (
    <>
      <span className="text-[11px] font-medium text-fg-subtle">{label}</span>
      <span className="mt-1 flex items-baseline gap-1">
        <span className={`font-mono text-xl font-extrabold tabular-nums ${VALUE_TONE[tone]}`}>
          {typeof value === "number" ? <CountUp value={value} format={format} /> : value}
        </span>
        {unit ? <span className="text-[10px] text-fg-subtle">{unit}</span> : null}
      </span>
    </>
  );
  const base = `flex flex-col justify-between p-3 text-left ${studioTile} ${selected ? studioActive : ""} ${className}`;
  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        aria-pressed={selected}
        className={`${base} ${studioTileInteractive} ${focusRing}`}
      >
        {content}
      </button>
    );
  }
  return (
    <div data-testid="studio-metric-tile" className={base}>
      {content}
    </div>
  );
}

/** Section header between groups of cards. */
export function StudioSectionHeader({
  title,
  subtitle,
  icon,
  tone = "brand",
  actions,
  className = "",
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  icon?: IconType;
  tone?: StudioTone;
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex flex-wrap items-end justify-between gap-3 ${className}`}>
      <div className="flex min-w-0 items-center gap-2.5">
        {icon ? <StudioIconBadge icon={icon} tone={tone} /> : null}
        <div className="min-w-0">
          <h2 className="text-base font-extrabold tracking-tight text-fg">{title}</h2>
          {subtitle ? <p className="text-xs text-fg-subtle">{subtitle}</p> : null}
        </div>
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export interface StudioSegmentOption<T extends string> {
  id: T;
  label: React.ReactNode;
  /** Accessible name when `label` is an icon or symbol. */
  ariaLabel?: string;
}

/** Segmented control with a sliding layoutId pill (e.g. Vol / %). */
export function StudioSegmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  size = "sm",
  className = "",
}: {
  options: readonly StudioSegmentOption<T>[];
  value: T;
  onChange: (id: T) => void;
  ariaLabel: string;
  size?: "xs" | "sm";
  className?: string;
}) {
  const pillId = `studio-seg-${useId()}`;
  const pad = size === "xs" ? "px-2 py-1 text-[10px]" : "px-3 py-1 text-xs";
  return (
    <div role="group" aria-label={ariaLabel} className={`${segmentTrack} ${className}`}>
      {options.map((opt) => {
        const on = opt.id === value;
        return (
          <button
            key={opt.id}
            type="button"
            aria-pressed={on}
            aria-label={opt.ariaLabel}
            onClick={() => onChange(opt.id)}
            className={`relative rounded-lg font-semibold transition-colors duration-150 ${pad} ${focusRing} ${
              on ? "text-white" : "text-fg-muted hover:bg-surface-card/70 hover:text-fg dark:hover:bg-white/6"
            }`}
          >
            {on ? (
              <m.span
                layoutId={pillId}
                transition={layoutSpring}
                aria-hidden="true"
                data-testid="studio-segment-pill"
                className="absolute inset-0 rounded-lg bg-brand-gradient shadow-brand-glow"
              />
            ) : null}
            <span className="relative">{opt.label}</span>
          </button>
        );
      })}
    </div>
  );
}
