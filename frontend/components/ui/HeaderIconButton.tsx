"use client";

import React, { forwardRef } from "react";
import Link from "next/link";
import type { LucideIcon } from "lucide-react";
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
  "data-tour"?: string;
}

type ButtonProps = BaseProps &
  Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "aria-label" | "children" | "className"> & { href?: undefined };
type LinkProps = BaseProps & { href: string; title?: string };

export type HeaderIconButtonProps = ButtonProps | LinkProps;

/** Size shared by every control in the page-header action cluster (36px). */
export const headerIconSize = "h-9 w-9";

const VARIANT: Record<HeaderIconButtonVariant, string> = {
  default:
    "border border-line bg-surface-card text-fg-muted shadow-sm hover:border-brand-300 hover:text-fg dark:hover:border-brand-700/60",
  primary: "bg-brand-gradient text-white shadow-brand-glow hover:opacity-90",
};
const PRESSED = "border border-brand-500 bg-brand-50 text-brand-700 shadow-sm dark:border-brand-400/60 dark:bg-brand-500/15 dark:text-brand-300";

function classes(variant: HeaderIconButtonVariant, pressed: boolean): string {
  return `relative inline-flex ${headerIconSize} shrink-0 items-center justify-center rounded-lg transition-colors motion-reduce:transition-none disabled:cursor-not-allowed disabled:opacity-50 ${
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
 * Icon-only action for the right-corner cluster of `DashboardHeader`: fixed
 * 36px square, aria-label as the name and the shared hover / focus tooltip.
 * Renders a Next `Link` when `href` is given, a button otherwise.
 */
export const HeaderIconButton = forwardRef<HTMLButtonElement, HeaderIconButtonProps>(function HeaderIconButton(
  props,
  ref,
) {
  const { label, tooltip, icon: Icon, variant = "default", pressed, indicator, hideTooltip, ...rest } = props;
  const className = classes(variant, Boolean(pressed));
  const content = (
    <>
      <Icon className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
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
    <ActionTooltip label={tooltip ?? label} align="end" side="bottom" hidden={hideTooltip}>
      {control}
    </ActionTooltip>
  );
});
