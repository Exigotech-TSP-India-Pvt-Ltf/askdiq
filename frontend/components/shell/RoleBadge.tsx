import React from "react";
import { ROLE_LABELS, type Role } from "@/lib/permissions";
import { Badge, type BadgeTone } from "@/components/ui/Badge";

const TONE: Record<Role, BadgeTone> = {
  superadmin: "brand",
  admin: "info",
  operator: "neutral",
};

/** Role chip for the profile menu and user lists. */
export function RoleBadge({ role }: { role: Role }) {
  return (
    <Badge data-testid="role-badge" tone={TONE[role]}>
      {ROLE_LABELS[role]}
    </Badge>
  );
}
