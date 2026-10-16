import React from "react";
import { ROLE_LABELS, type Role } from "@/lib/permissions";

const TONE: Record<Role, string> = {
  superadmin: "border-brand-500/30 bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300",
  admin: "border-status-info/30 bg-status-info-bg text-status-info-fg",
  operator: "border-line bg-surface-muted text-fg-muted",
};

/** Role chip for the profile menu and user lists. */
export function RoleBadge({ role }: { role: Role }) {
  return (
    <span
      data-testid="role-badge"
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${TONE[role]}`}
    >
      {ROLE_LABELS[role]}
    </span>
  );
}
