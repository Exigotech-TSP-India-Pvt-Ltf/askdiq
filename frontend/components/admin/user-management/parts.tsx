"use client";

import React, { useId } from "react";
import {
  ROLE_LABEL,
  STATUS_LABEL,
  type Role,
  type UserStatus,
} from "@/lib/user-management-api";
import { focusRing } from "@/components/ui/styles";

const CHIP = "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap";

const STATUS_TONE: Record<UserStatus, string> = {
  active: "bg-status-ok-bg text-status-ok-fg",
  pending: "bg-status-warn-bg text-status-warn-fg",
  rejected: "bg-surface-muted text-fg-muted",
  suspended: "bg-status-danger-bg text-status-danger-fg",
};

export function StatusBadge({ status }: { status: UserStatus }) {
  return <span className={`${CHIP} ${STATUS_TONE[status]}`}>{STATUS_LABEL[status]}</span>;
}

const ROLE_TONE: Record<Role, string> = {
  superadmin: "bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300",
  admin: "bg-status-info-bg text-status-info-fg",
  operator: "bg-surface-muted text-fg-muted",
};

export function RoleBadge({ role }: { role: Role | null }) {
  if (!role) return <span className={`${CHIP} border border-dashed border-line text-fg-subtle`}>No role</span>;
  return <span className={`${CHIP} ${ROLE_TONE[role]}`}>{ROLE_LABEL[role]}</span>;
}

const dateTime = new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" });

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : dateTime.format(d);
}

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "45s", "12m", "3h 5m", "2d 4h". */
export function formatWaiting(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || seconds < 0) return "—";
  if (seconds < MINUTE) return `${Math.floor(seconds)}s`;
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)}m`;
  if (seconds < DAY) return `${Math.floor(seconds / HOUR)}h ${Math.floor((seconds % HOUR) / MINUTE)}m`;
  return `${Math.floor(seconds / DAY)}d ${Math.floor((seconds % DAY) / HOUR)}h`;
}

export const fieldClass = `rounded-lg border border-line bg-surface-card px-2.5 py-1.5 text-xs text-fg shadow-sm ${focusRing}`;

interface GuardedButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "disabled"> {
  /** When set the action is unavailable; the reason shows as a tooltip and is announced. */
  disabledReason?: string | null;
  disabled?: boolean;
}

/**
 * Button that stays focusable when blocked by a safeguard (self-change, last
 * superadmin) so keyboard and pointer users both get the reason as a tooltip.
 */
export function GuardedButton({ disabledReason, disabled, onClick, className = "", children, ...rest }: GuardedButtonProps) {
  const tipId = useId();
  const blocked = Boolean(disabledReason);
  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        {...rest}
        disabled={disabled && !blocked}
        aria-disabled={blocked || undefined}
        aria-describedby={blocked ? tipId : undefined}
        onClick={blocked ? (e) => e.preventDefault() : onClick}
        className={`${className} ${blocked ? "cursor-not-allowed opacity-50" : ""}`}
      >
        {children}
      </button>
      {blocked ? (
        <span
          id={tipId}
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-1.5 hidden w-56 -translate-x-1/2 rounded-lg bg-surface-inverse px-2.5 py-1.5 text-center text-[11px] leading-snug text-fg-inverse shadow-md group-focus-within:block group-hover:block"
        >
          {disabledReason}
        </span>
      ) : null}
    </span>
  );
}
