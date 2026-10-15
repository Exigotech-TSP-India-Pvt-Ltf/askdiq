"use client";

/**
 * Roles & Permissions tab (planchanges4 F1.4.3, newplan §7.3): a read-only
 * matrix of the permission table the API enforces. The backend exposes no
 * list endpoint, so the matrix is `ROLE_PERMISSIONS` (mirrored from
 * app/auth/permissions.py) and the caller's column is checked against the
 * live `/auth/me` permissions; any drift is flagged.
 */
import React from "react";
import { Check, Minus, TriangleAlert } from "lucide-react";
import { TableCard } from "@/components/admin/TableCard";
import {
  PERMISSIONS,
  PERMISSION_LABEL,
  ROLES,
  ROLE_LABEL,
  ROLE_PERMISSIONS,
  type Me,
  type Permission,
} from "@/lib/user-management-api";

export function permissionDrift(me: Me | undefined): Permission[] {
  if (!me?.role) return [];
  const expected = new Set<Permission>(ROLE_PERMISSIONS[me.role]);
  const live = new Set<Permission>(me.permissions);
  return PERMISSIONS.filter((p) => expected.has(p) !== live.has(p));
}

export function RolesMatrixTab({ me }: { me: Me | undefined }) {
  const drift = permissionDrift(me);
  const rows: readonly Permission[] = Array.from(new Set<Permission>([...PERMISSIONS, ...(me?.permissions ?? [])]));

  return (
    <TableCard
      title="Roles & permissions"
      description="Read-only. The API enforces exactly this table; the UI hides what a role cannot do."
    >
      {drift.length > 0 ? (
        <div role="alert" className="flex items-start gap-2 border-b border-line bg-status-warn-bg px-5 py-3 text-xs text-status-warn-fg">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
          <p>
            Your live permissions from <code className="font-mono">/auth/me</code> differ from this table for:{" "}
            {drift.join(", ")}. The API is the authority.
          </p>
        </div>
      ) : null}
      <table className="w-full border-collapse text-left text-xs" data-testid="um-roles-matrix">
        <caption className="sr-only">Permissions granted to each role</caption>
        <thead className="border-b border-line bg-surface-muted/60 text-fg-muted">
          <tr>
            <th scope="col" className="px-5 py-2.5 font-semibold">
              Permission
            </th>
            {ROLES.map((role) => (
              <th
                key={role}
                scope="col"
                className={`px-3 py-2.5 text-center font-semibold ${me?.role === role ? "bg-brand-50 text-brand-700 dark:bg-brand-950/60 dark:text-brand-300" : ""}`}
              >
                {ROLE_LABEL[role]}
                {me?.role === role ? <span className="font-normal"> (you)</span> : null}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((perm) => (
            <tr key={perm} className="transition-colors hover:bg-surface-muted/60">
              <th scope="row" className="px-5 py-2.5 font-normal">
                <p className="font-medium text-fg">{PERMISSION_LABEL[perm] ?? perm}</p>
                <p className="font-mono text-[11px] text-fg-subtle">{perm}</p>
              </th>
              {ROLES.map((role) => {
                const granted = ROLE_PERMISSIONS[role].includes(perm);
                return (
                  <td
                    key={role}
                    className={`px-3 py-2.5 text-center ${me?.role === role ? "bg-brand-50/50 dark:bg-brand-950/30" : ""}`}
                  >
                    {granted ? (
                      <Check className="mx-auto h-4 w-4 text-status-ok" strokeWidth={1.75} aria-label="Granted" />
                    ) : (
                      <Minus className="mx-auto h-4 w-4 text-fg-subtle" strokeWidth={1.75} aria-label="Not granted" />
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </TableCard>
  );
}
