"use client";

import React, { forwardRef } from "react";
import Link from "next/link";
import { ChevronDown, type LucideIcon } from "lucide-react";
import { ActionTooltip } from "@/components/ui/ActionTooltip";
import { focusRing } from "@/components/ui/styles";

export type HeaderIconButtonVariant = "default" | "primary";

interface BaseProps {
  /** Accessible name (aria-label); also the tooltip unless `tooltip` is given. */
  label: string;
  /** Tooltip text when it should differ from the accessible name. */
  tooltip?: string;
  icon: LucideIcon;
  /** `primary` keeps the brand gradient for a page's main action. */
  variant?: HeaderIconButtonVariant;
  /** Toggle state: shows the pressed style and sets `aria-pressed` (buttons only). */
  pressed?: boolean;
  /** Small dot in the corner, e.g. a non-default filter. */
  indicator?: boolean;
  /** Hides the tooltip (e.g. while the control's popover is open). */
  hideTooltip?: boolean;
  /**
   * Icon + visible text (OR-2): the label shows from `sm` up and is the
   * accessible name (WCAG 2.5.3); below `sm` the control collapses to its icon
   * and keeps the tooltip.
   */
  showLabel?: boolean;
  /** Visible text when it should be shorter than the accessible name (must start the name). */
  text?: string;
  /** Trailing chevron for a dropdown trigger (shown with the label). */
  chevron?: boolean;
  "data-tour"?: string;
}

type ButtonProps = BaseProps &
  Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "aria-label" | "children" | "className"> & { href?: undefined };
type LinkProps = BaseProps & { href: string; title?: string };

export type HeaderIconButtonProps = ButtonProps | LinkProps;

/** Size shared by every icon-only control in the page action clusters (36px). */
export const headerIconSize = "h-9 w-9";
/** Labelled controls: 36px high from `sm`, a 44px touch target below it. */
const labelledSize = "h-11 min-w-11 px-3 sm:h-9 sm:min-w-0 gap-1.5";

const VARIANT: Record<HeaderIconButtonVariant, string> = {
  default:
    "border border-line bg-surface-card text-fg-muted shadow-sm hover:border-brand-300 hover:text-fg dark:hover:border-brand-700/60",
  primary: "bg-brand-gradient text-white shadow-brand-glow hover:opacity-90",
};
const PRESSED = "border border-brand-500 bg-brand-50 text-brand-700 shadow-sm dark:border-brand-400/60 dark:bg-brand-500/15 dark:text-brand-300";

function classes(variant: HeaderIconButtonVariant, pressed: boolean, labelled: boolean): string {
  return `relative inline-flex ${labelled ? labelledSize : headerIconSize} shrink-0 items-center justify-center rounded-lg text-xs font-medium transition-colors motion-reduce:transition-none disabled:cursor-not-allowed disabled:opacity-50 ${
    pressed ? PRESSED : VARIANT[variant]
  } ${focusRing}`;
}

function Dot() {
  return (
    <span
      aria-hidden="true"
      data-indicator
      className="absolute right-1 top-1 h-2 w-2 rounded-full bg-brand-500 ring-2 ring-surface-card dark:bg-brand-400"
    />
  );
}

/**
 * Page action: a 36px icon square, or icon + text with `showLabel` (the
 * `PageToolbar` actions). The aria-label is the name and the shared hover /
 * focus tooltip shows it. Renders a Next `Link` when `href` is given, a
 * button otherwise.
 */
export const HeaderIconButton = forwardRef<HTMLButtonElement, HeaderIconButtonProps>(function HeaderIconButton(
  props,
  ref,
) {
  const {
    label,
    tooltip,
    icon: Icon,
    variant = "default",
    pressed,
    indicator,
    hideTooltip,
    showLabel = false,
    text,
    chevron = false,
    ...rest
  } = props;
  const className = classes(variant, Boolean(pressed), showLabel);
  const content = (
    <>
      <Icon className="h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
      {showLabel ? (
        <span data-label className="hidden whitespace-nowrap sm:inline">
          {text ?? label}
        </span>
      ) : null}
      {showLabel && chevron ? (
        <ChevronDown className="hidden h-3.5 w-3.5 shrink-0 opacity-70 sm:block" strokeWidth={1.75} aria-hidden="true" />
      ) : null}
      {indicator ? <Dot /> : null}
    </>
  );

  let control: React.ReactNode;
  if (rest.href !== undefined) {
    const { href, title, "data-tour": dataTour } = rest as LinkProps;
    control = (
      <Link href={href} aria-label={label} title={title} data-tour={dataTour} className={className}>
        {content}
      </Link>
    );
  } else {
    const buttonProps = rest as Omit<ButtonProps, keyof BaseProps>;
    control = (
      <button
        ref={ref}
        type="button"
        {...buttonProps}
        aria-label={label}
        aria-pressed={pressed === undefined ? undefined : pressed}
        className={className}
      >
        {content}
      </button>
    );
  }

  return (
    <ActionTooltip
      label={tooltip ?? label}
      align="end"
      side="bottom"
      hidden={hideTooltip}
      // A labelled control already shows its name; the bubble is for phones
      // (icon only) or for an extra hint (`tooltip`).
      compactOnly={showLabel && tooltip === undefined}
    >
      {control}
    </ActionTooltip>
  );
});
